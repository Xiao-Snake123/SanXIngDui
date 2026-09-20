"""语料向量索引：pgvector（HNSW）↔ 进程内暴力检索。

为什么两种实现都留着
--------------------
50 条语料的暴力检索本身就快到可以忽略，**换向量库不是为了更快**。接 pgvector 换到的是：

1. **重启不再重复付 embedding 的钱。** 向量是语料派生的、可重建的缓存，
   但重建要真金白银调用模型。持久化之后，`vector_reuse` 命中就整批跳过 ——
   这条在本地实测能直接看到（第二次启动的 embedding 调用数为 0）。
2. **跨进程共享**。多副本部署时不需要每个副本各自算一遍全量语料向量。
3. **可扩展**。语料从 50 条涨到 5 万条时，暴力检索的 O(N) 会先撑不住；
   HNSW 是拿空间换对数级查询。表结构与索引从一开始就按那个规模建。

降级方向是单向的：pgvector 不可用 → 进程内。**召回质量几乎不变**
（同一批向量、同一种余弦度量），变的是上面三件事。

维度一致性
----------
向量列的维度在建表时固定（`vector(1024)`）。换 embedding 模型 = 换维度 = 换语义空间，
两者都会让「库里已有的向量」变成毒药。所以每条向量都记了 `embedder` 名字，
名字不一致就不复用、只算缺的那部分 —— 这是这个文件里最重要的一个判断。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.rag.corpus import CorpusEntry
from app.rag.embedder import cosine
from app.storage.db import Database

logger = get_logger("app.storage.vectors")

# 回调：把文本批量向量化，并返回**实际使用**的 embedder 名字。
# 返回名字而不是让调用方去读 self.embedder，是因为向量通道可能在这次调用中
# 从「模型」降级到「哈希」—— 名字必须对应真正产出这批向量的那条通道，
# 否则下一次启动的复用判断会把哈希向量当成模型向量重用。
EmbedFn = Callable[[list[str]], Awaitable[tuple[list[list[float]], str]]]


@dataclass(slots=True)
class VectorReport:
    backend: str
    dimension: int
    count: int
    reused: int = 0        # 复用了库里已有向量的条数
    embedded: int = 0      # 本次真正调用 embedding 的条数
    purged: int = 0        # 清理掉的「不属于当前语料」的残留行数
    ok: bool = True
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "dimension": self.dimension,
            "count": self.count,
            "reused": self.reused,
            "embedded": self.embedded,
            "purged": self.purged,
            "ok": self.ok,
            "reason": self.reason or None,
            **self.extra,
        }


class VectorIndex(Protocol):
    backend: str
    dimension: int

    async def ensure(self, entries: list[CorpusEntry], embed_fn: EmbedFn, embedder_name: str) -> VectorReport: ...

    async def search(self, vector: list[float], *, limit: int) -> dict[str, float]: ...

    async def aclose(self) -> None: ...


class MemoryVectorIndex:
    """进程内暴力检索（原来的行为）。

    保留它有三个理由：数据库没配置、连不上、或者 pgvector 扩展缺失。
    三种情况下检索链路都必须是完整的 —— 少一层外部依赖，系统就少一个起不来的理由。
    """

    backend = "memory"

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self._vectors: dict[str, list[float]] = {}

    async def ensure(
        self, entries: list[CorpusEntry], embed_fn: EmbedFn, embedder_name: str
    ) -> VectorReport:
        if not entries:
            return VectorReport(backend=self.backend, dimension=self.dimension, count=0)
        vectors, used = await embed_fn([entry.searchable_text for entry in entries])
        self._vectors = {
            entry.doc_id: vector for entry, vector in zip(entries, vectors, strict=False)
        }
        return VectorReport(
            backend=self.backend,
            dimension=self.dimension,
            count=len(self._vectors),
            embedded=len(self._vectors),
            extra={"embedder": used},
        )

    async def search(self, vector: list[float], *, limit: int) -> dict[str, float]:
        scores: dict[str, float] = {}
        for doc_id, stored in self._vectors.items():
            score = cosine(vector, stored)
            if score > 0:
                scores[doc_id] = score
        return _top(scores, limit)

    async def aclose(self) -> None:
        self._vectors.clear()


class PgVectorIndex:
    """pgvector 存向量 + HNSW 余弦索引召回。"""

    backend = "pgvector"

    def __init__(self, db: Database, dimension: int) -> None:
        self.db = db
        self.dimension = dimension

    # ── 建库 ────────────────────────────────────────────────────────────────
    async def ensure(
        self, entries: list[CorpusEntry], embed_fn: EmbedFn, embedder_name: str
    ) -> VectorReport:
        if not entries:
            return VectorReport(backend=self.backend, dimension=self.dimension, count=0)
        try:
            return await self._ensure(entries, embed_fn, embedder_name)
        except Exception as exc:  # noqa: BLE001
            # 表不存在 / 维度不匹配 / 扩展被删 —— 任何一种都只降级，不向上抛：
            # 向量库是可选依赖，它坏掉不该让服务起不来
            logger.warning("pgvector 通道不可用，向量检索退回进程内实现: %s", exc)
            metrics.record_degradation(
                component="vector_store", reason=f"{type(exc).__name__}: {exc}", fallback="memory"
            )
            return VectorReport(
                backend=self.backend,
                dimension=self.dimension,
                count=0,
                ok=False,
                reason=f"{type(exc).__name__}: {exc}",
            )

    async def _ensure(
        self, entries: list[CorpusEntry], embed_fn: EmbedFn, embedder_name: str
    ) -> VectorReport:
        # 复用条件必须同时满足两条：**同一个 embedder**，且**正文指纹一致**。
        # 判断本身抽在 `entries_needing_embedding` 里（纯函数，可离线单测）。
        existing = await self._existing()
        wanted = {entry.doc_id for entry in entries}

        # 清理不属于当前语料的残留行。
        # **必须放在「全部复用就提前返回」那条分支之前** —— 实测踩过：
        # 切换语料后 150 条全部复用命中，函数在下面直接 return，
        # 残留的 50 行一直没被清掉。
        purged = await self._purge_stale(wanted)

        pending = entries_needing_embedding(entries, existing, embedder_name)
        reused = len(entries) - len(pending)
        if not pending:
            logger.info(
                "向量复用命中: %d 条（embedder=%s），本次不调用 embedding%s",
                reused,
                embedder_name,
                f"；已清理 {purged} 条不属于当前语料的残留" if purged else "",
            )
            metrics.inc("sxd_vector_reused_total", float(reused))
            return VectorReport(
                backend=self.backend,
                dimension=self.dimension,
                count=len(wanted),
                reused=reused,
                embedded=0,
                purged=purged,
                extra={"embedder": embedder_name, "reuse_saved_calls": reused},
            )

        vectors, used = await embed_fn([entry.searchable_text for entry in pending])
        rows = [
            {
                "doc_id": entry.doc_id,
                "title": entry.title,
                "object": entry.object,
                "era": entry.era,
                "category": entry.category,
                "source": entry.source,
                "authority": entry.authority,
                "tags": list(entry.tags),
                "embedder": used,
                "content_hash": entry.content_hash,
                "embedding": vector,
            }
            for entry, vector in zip(pending, vectors, strict=False)
        ]
        written = await self._write(rows)
        if written == 0 and rows:
            return VectorReport(
                backend=self.backend,
                dimension=self.dimension,
                count=0,
                reused=reused,
                embedded=0,
                ok=False,
                reason="向量写入失败，检索将退回进程内实现",
            )
        logger.info(
            "向量入库完成: 新算 %d 条，复用 %d 条，embedder=%s%s",
            written,
            reused,
            used,
            f"；已清理 {purged} 条不属于当前语料的残留" if purged else "",
        )
        return VectorReport(
            backend=self.backend,
            dimension=self.dimension,
            count=reused + written,
            reused=reused,
            embedded=written,
            purged=purged,
            extra={"embedder": used},
        )

    async def _purge_stale(self, wanted: set[str]) -> int:
        """删除「不属于当前语料」的向量行。返回删除行数。

        **为什么必须有这一步**：切换语料（或从语料里删条目）时，旧行不会被自动清理。
        实测：把 `CORPUS_DIR` 从 v1（50 条自撰）切到 v2（150 条真实文献）之后，
        `corpus_chunks` 里仍是 200 行 —— 多出来的 50 行是 v1 的残留。

        后果不是「多了点垃圾数据」，而是**检索结果被污染**：

        1. 那些行会参与 HNSW 召回，**挤占 top-k 名额**，把真正属于当前语料的候选
           挤出候选集；
        2. 它们也参与融合时的 max 归一化，于是**改变排序**；
        3. 最根本的是：系统可能返回一个**已经不在语料里的 doc_id** —— 引用一条
           谁也查不到的出处，正是本项目最要避免的那类错误。

        实测症状（`scripts/check_storage.py` 抓到）：
            HNSW 召回与暴力检索一致 -- top10 重合 3/10 (30%)
            两条向量通道的 top3 完全一致 -- 1/3 条 query 一致
        两个通道本应给出近似相同的结果，差异全部来自「pgvector 看得见残留、
        进程内实现只看得见当前语料」这个不对称。

        **为什么只在 pgvector 侧做**：`MemoryVectorIndex.ensure` 是整体替换
        `self._vectors`，天然不存在残留。这个不对称本身没错，
        但不能让它变成「两个通道看到不同的文档集合」。
        """
        from sqlalchemy import delete

        from app.storage.models import CorpusChunk

        if not wanted:
            return 0
        async with self.db.session() as session:
            result = await session.execute(
                delete(CorpusChunk).where(CorpusChunk.doc_id.notin_(wanted))
            )
            await session.commit()
        removed = int(result.rowcount or 0)
        if removed:
            logger.warning(
                "清理了 %d 条不属于当前语料的向量残留（语料已换版本或删过条目）。"
                "它们此前会参与检索并挤占候选名额。",
                removed,
            )
            metrics.inc("sxd_vector_purged_total", float(removed))
        return removed

    async def _existing(self) -> dict[str, tuple[str, str | None]]:
        """返回 {doc_id: (embedder 名, 正文指纹)}。

        这里**不按 embedder 过滤**，把全部行都取回来交给调用方比对：
        换 embedding 模型时要能识别出「这批向量是别的模型算的」，
        而老数据的 `content_hash` 是 NULL，天然不会等于任何指纹，
        于是会被重算一遍 —— 这正是我们想要的（宁可多花一次钱，
        也不要拿两个语义空间的向量做余弦）。
        """
        if not settings.vector_reuse:
            return {}
        from sqlalchemy import select

        from app.storage.models import CorpusChunk

        async with self.db.session() as session:
            rows = (
                await session.execute(
                    select(CorpusChunk.doc_id, CorpusChunk.embedder, CorpusChunk.content_hash).where(
                        CorpusChunk.embedding.is_not(None)
                    )
                )
            ).all()
        return {
            str(doc_id): (str(embedder), content_hash if content_hash is None else str(content_hash))
            for doc_id, embedder, content_hash in rows
        }

    async def _write(self, rows: list[dict[str, Any]]) -> int:
        # 复用 repository 的 upsert：同一份 SQL 只有一处，避免「向量写入」这两条路径
        # 各写一半最后漂移（向量是 1024 维的长字符串，漂移了很难肉眼发现）
        from app.storage.repository import TaskRepository

        return await TaskRepository(self.db).upsert_corpus_chunks(rows)

    # ── 检索 ────────────────────────────────────────────────────────────────
    async def search(self, vector: list[float], *, limit: int) -> dict[str, float]:
        from app.storage.repository import _vector_literal

        statement = text(
            """
            SELECT doc_id, 1 - (embedding <=> CAST(:query AS vector)) AS score
            FROM corpus_chunks
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:query AS vector)
            LIMIT :limit
            """
        )
        async with self.db.session() as session:
            rows = (
                await session.execute(
                    statement,
                    {"query": _vector_literal(vector), "limit": max(1, limit)},
                )
            ).all()
        return {str(doc_id): float(score) for doc_id, score in rows if score and score > 0}

    async def aclose(self) -> None:
        return None


def entries_needing_embedding(
    entries: list[CorpusEntry],
    existing: dict[str, tuple[str, str | None]],
    embedder_name: str,
) -> list[CorpusEntry]:
    """挑出**必须重新向量化**的条目。

    复用条件同时满足两条才成立：

    1. **同一个 embedder** —— 换模型等于换语义空间，旧向量与新 query 做余弦
       不会报错，只会让召回变差；
    2. **正文指纹一致** —— `doc_id` 是稳定主键（要能追溯回报告页码），
       所以修史料的人会保留 id 只改正文。只看 id 就会继续用旧向量。

    抽成纯函数的理由：这是整条复用逻辑里唯一的判断，也是最容易写错的一处；
    独立出来之后可以在**不连数据库**的前提下把所有分支测掉
    （见 `tests/test_storage_vectors.py`）。
    """
    return [
        entry
        for entry in entries
        if existing.get(entry.doc_id) != (embedder_name, entry.content_hash)
    ]


def _top(scores: dict[str, float], limit: int) -> dict[str, float]:
    if len(scores) <= limit:
        return scores
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    return dict(ordered)


def build_vector_index(db: Database, *, dimension: int) -> VectorIndex | None:
    """按可用性挑实现。

    返回 None 表示「连进程内都不可用」（语料为空时不建索引），调用方自行处理。
    注意这里**不抛异常**：向量通道是可选依赖，缺了只能降级，不能拦住启动。
    """
    if db.available and db.vector_version:
        return PgVectorIndex(db, dimension)
    if db.available and not db.vector_version:
        logger.warning(
            "数据库已连接但未安装 pgvector 扩展，向量检索退回进程内实现。"
            "修复：CREATE EXTENSION vector;（需要超级用户）"
        )
        metrics.record_degradation(
            component="vector_store", reason="pgvector extension missing", fallback="memory"
        )
    return MemoryVectorIndex(dimension)
