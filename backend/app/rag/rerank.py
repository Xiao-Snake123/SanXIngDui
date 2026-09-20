"""重排（Rerank）。

召回阶段追求**高召回**（BM25 + 向量的并集），重排阶段追求**高精度**：
用 cross-encoder 类模型对 (query, doc) 逐对打分，把真正相关的史料顶到最前。

本文件提供两条通道：
- `DashScopeReranker`：gte-rerank-v2（cross-encoder，效果最好）
- `RRFReranker`：Reciprocal Rank Fusion，把 BM25 名次与向量名次做倒数融合。
  虽然不如 cross-encoder，但在无 Key 时是**显著优于单路**的免费方案。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.core.metrics import metrics

logger = get_logger("app.rag.rerank")


@dataclass(slots=True)
class RerankResult:
    doc_id: str
    score: float
    index: int


class DashScopeReranker:
    degraded = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 30.0,
    ) -> None:
        self.model = model
        self._url = f"{base_url.rstrip('/')}/api/v1/services/rerank/text-rerank/text-rerank"
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )

    async def rerank(
        self, query: str, candidates: list[tuple[str, str]], top_n: int
    ) -> list[RerankResult]:
        if not candidates:
            return []
        payload = {
            "model": self.model,
            "input": {"query": query, "documents": [text for _, text in candidates]},
            "parameters": {"return_documents": False, "top_n": max(1, top_n)},
        }
        response = await self._client.post(self._url, json=payload)
        if response.status_code >= 400:
            raise ProviderError(
                f"Rerank 调用失败: {response.text[:300]}", status=response.status_code
            )
        results = (response.json().get("output") or {}).get("results") or []
        metrics.inc("sxd_rerank_calls", model=self.model)
        output: list[RerankResult] = []
        for item in results:
            index = int(item.get("index", 0))
            if 0 <= index < len(candidates):
                output.append(
                    RerankResult(
                        doc_id=candidates[index][0],
                        score=float(item.get("relevance_score") or 0.0),
                        index=index,
                    )
                )
        return output

    async def aclose(self) -> None:
        await self._client.aclose()


class RRFReranker:
    """Reciprocal Rank Fusion，k 取 60（TREC 经验值）。

    注意：原生的 RRF 分数绝对值极小（rank 0 只有 1/61），如果直接与融合分做加权，
    结果会被压成一个常数、丢失区分度（实测表现为所有候选 final_score 都是 0.011）。
    因此这里做归一化：rank 0 得 1.0，后续按 1/(k+rank+1) 的比值递减，
    保持单调性的同时把值域拉回 (0, 1]，与 cross-encoder 通道可比。
    """

    k = 60
    degraded = True

    async def rerank(
        self, query: str, candidates: list[tuple[str, str]], top_n: int
    ) -> list[RerankResult]:
        # candidates 已按融合分降序，此处按名次给分，保证重排结果与融合分单调一致
        ranked = [
            RerankResult(
                doc_id=doc_id,
                score=(self.k + 1) / (self.k + index + 1),
                index=index,
            )
            for index, (doc_id, _) in enumerate(candidates)
        ]
        return ranked[:top_n]

    async def aclose(self) -> None:  # pragma: no cover
        return None


_reranker = None


def get_reranker():
    global _reranker
    if _reranker is not None:
        return _reranker
    if settings.has_dashscope_key:
        _reranker = DashScopeReranker(
            api_key=settings.dashscope_key_plain,
            base_url=settings.dashscope_base_url,
            model=settings.model_rerank,
        )
    else:
        metrics.inc("sxd_degraded_total", component="reranker", fallback="rrf")
        logger.info("未配置 Key，重排降级为 RRF")
        _reranker = RRFReranker()
    return _reranker


async def aclose_reranker() -> None:
    """关闭全局 reranker。

    `HybridRetriever.aclose()` 只关 index 与 embedder，从来不关 reranker ——
    于是它内部的 httpx 客户端在关停时不会被释放。由 lifespan 的 finally 调用。
    """
    global _reranker
    if _reranker is None:
        return
    try:
        await _reranker.aclose()
    except Exception:  # noqa: BLE001 - 关停阶段的失败不应阻止退出
        pass
    _reranker = None
