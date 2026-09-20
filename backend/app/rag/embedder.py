"""文本向量化。

两条通道：
- `DashScopeEmbedder`：text-embedding-v3，语义泛化能力强（"纵目面具" ≈ "凸目铜面像"）。
- `HashingEmbedder`：**零依赖降级通道**。字符 3-gram → 固定 512 维哈希桶（带符号的
  hashing trick）→ sublinear TF → L2 归一化。它捕捉的是字面重合度，
  在没有网络/没有 Key 的环境里保证检索链路不断。

两者对上层是同一个接口，融合权重与重排逻辑完全复用，因此
「是否配置 Key」只影响召回质量，不影响系统结构与代码路径。
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Literal, Protocol

import httpx

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.rag.text import ngrams

logger = get_logger("app.rag.embedder")

Kind = Literal["document", "query"]


class Embedder(Protocol):
    name: str
    dimension: int
    degraded: bool

    async def embed(self, texts: list[str], *, kind: Kind = "document") -> list[list[float]]: ...

    async def aclose(self) -> None: ...


class HashingEmbedder:
    """确定性哈希向量：无需模型、无需网络、跨进程一致。"""

    degraded = True

    # 维度默认跟随 settings.vector_dim（= 数据库里 vector 列的维度）。
    # 两边不一致的后果不是报错，而是「写不进 pgvector、静默退回进程内」——
    # 所以名字里带上维度，让 /api/health 与语料表的 embedder 列一眼能对上。
    def __init__(self, dimension: int = 1024, gram: int = 3) -> None:
        self.dimension = dimension
        self.gram = gram

    @property
    def name(self) -> str:
        return f"hashing-ngram-{self.dimension}"

    async def embed(self, texts: list[str], *, kind: Kind = "document") -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        grams = ngrams(text, self.gram)
        if not grams:
            return vector

        counts = Counter(grams)
        for gram, count in counts.items():
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            raw = int.from_bytes(digest, "big")
            index = raw % self.dimension
            sign = 1.0 if (raw >> 63) & 1 else -1.0  # 带符号哈希，抵消碰撞带来的正偏差
            vector[index] += sign * (1.0 + math.log(count))

        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector

    async def aclose(self) -> None:  # pragma: no cover - 无资源
        return None


class DashScopeEmbedder:
    name = "dashscope-embedding"
    degraded = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimension: int = 1024,
        batch_size: int = 10,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimension = dimension
        self.batch_size = batch_size
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )

    async def embed(self, texts: list[str], *, kind: Kind = "document") -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = [text[:6000] or " " for text in texts[start : start + self.batch_size]]
            payload = {
                "model": self.model,
                "input": batch,
                "dimensions": self.dimension,
                "encoding_format": "float",
            }
            response = await self._client.post(f"{self.base_url}/embeddings", json=payload)
            if response.status_code >= 400:
                raise ProviderError(
                    f"Embedding 调用失败: {response.text[:300]}", status=response.status_code
                )
            data = response.json().get("data") or []
            data.sort(key=lambda item: item.get("index", 0))
            vectors.extend([item.get("embedding") or [] for item in data])
            metrics.inc("sxd_embedding_calls", model=self.model)
        if len(vectors) != len(texts):
            raise ProviderError(f"Embedding 返回数量不匹配: {len(vectors)} != {len(texts)}")
        return vectors

    async def aclose(self) -> None:
        await self._client.aclose()


_dense_cache: dict[str, list[list[float]]] = {}


class CachedEmbedder:
    """包一层进程内缓存：语料向量在首次建库后不再重复请求（省钱且降低 P95 延迟）。"""

    def __init__(self, inner: Embedder, max_entries: int = 20000) -> None:
        self.inner = inner
        self.name = inner.name
        self.dimension = inner.dimension
        self.degraded = inner.degraded
        self.max_entries = max_entries
        self._cache: dict[str, list[float]] = {}

    async def embed(self, texts: list[str], *, kind: Kind = "document") -> list[list[float]]:
        if kind == "query":
            return await self.inner.embed(texts, kind=kind)

        pending = [text for text in texts if _cache_key(text) not in self._cache]
        if pending and len(self._cache) < self.max_entries:
            fresh = await self.inner.embed(pending, kind=kind)
            for text, vector in zip(pending, fresh, strict=False):
                self._cache[_cache_key(text)] = vector
        return [self._cache.get(_cache_key(text)) or [0.0] * self.dimension for text in texts]

    async def aclose(self) -> None:
        await self.inner.aclose()


def _cache_key(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


def build_embedder() -> Embedder:
    """按配置装配向量通道；无 Key 时无声降级（只打一条 metric）。

    两条通道的维度都取 `settings.vector_dim`：向量库的列维度是固定的，
    让 embedder 去适配列，而不是让列去追 embedder。
    """
    if settings.has_dashscope_key:
        inner = DashScopeEmbedder(
            api_key=settings.dashscope_key_plain,
            base_url=settings.openai_compatible_base,
            model=settings.model_embedding,
            dimension=settings.vector_dim,
        )
        metrics.set_gauge("sxd_embedder_degraded", 0)
        return CachedEmbedder(inner)

    metrics.set_gauge("sxd_embedder_degraded", 1)
    metrics.inc("sxd_degraded_total", component="embedder", fallback="hashing")
    logger.warning("未配置 DASHSCOPE_API_KEY，向量通道降级为本地哈希向量")
    return HashingEmbedder(dimension=settings.vector_dim)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(size))
    return max(-1.0, min(1.0, dot))
