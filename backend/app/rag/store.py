"""混合检索器：BM25 ∪ 向量 → 归一化融合 → Cross-Encoder 重排。

召回与精排的分工
----------------
- 召回：BM25 取 top_k，向量取 top_k，做**并集**。并集而非交集，是因为
  「用户说青铜大立人、史料写铜立人像」这类同义改写只能靠向量找回，
  而「鱼凫王」这类罕见专名只有 BM25 找得准。
- 融合：各自在候选集内做 max 归一化后加权求和（`HYBRID_ALPHA` 控 BM25 占比）。
  比 RRF 更细粒度，且天然支持「权重可调」这一线上调参手段。
- 精排：cross-encoder 重排后乘上 `authority`（史料可信度），
  让正式发掘报告优先于二手科普——这是文物场景特有的业务加权。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.rag.bm25 import BM25Index
from app.rag.corpus import SOURCE_AUTHORITY, Citation, Corpus, CorpusEntry, load_corpus
from app.rag.embedder import Embedder, HashingEmbedder, build_embedder
from app.rag.rerank import get_reranker
from app.rag.text import FOLD_BACKEND, truncate
from app.storage.db import database
from app.storage.vectors import MemoryVectorIndex, VectorIndex, VectorReport, build_vector_index

logger = get_logger("app.rag.store")


@dataclass(slots=True)
class RetrievedChunk:
    doc_id: str
    title: str
    text: str
    object: str
    era: str
    category: str
    tags: list[str]
    source: str
    authority: float
    # 出处与授权必须一路透传到下游：问答链路要靠 citation 回带引用，
    # 中途任何一层丢掉它，「可追溯」就断在这里。
    source_type: str = "unknown"
    license: str = "unknown"
    quote: str = ""
    # 条目的加工说明（如「古籍的校勘注已剥离，正文为通行读本」）。
    # 必须随引用一起透传：引用一部古籍却不说明它被整理过，会让读者以为
    # 看到的就是某个版本的原文 —— 那是引用最该避免的误导。
    note: str = ""
    # 直接持有 Citation 对象而不是 dict：出处的格式化规则只应存在于 Citation 一处，
    # 在检索层再拼一次字符串，迟早会和下层走偏。
    citation: Citation = field(default_factory=Citation)
    traceable: bool = False
    evidential: bool = False
    bm25_score: float = 0.0
    dense_score: float = 0.0
    fused_score: float = 0.0
    rerank_score: float = 0.0
    final_score: float = 0.0
    matched_terms: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)

    @property
    def display_source(self) -> str:
        """展示用出处文字。有结构化引用时优先，否则退回 v1 的自由文本标签。"""
        if self.citation.title or self.citation.url:
            return self.citation.as_text()
        return self.source

    def to_dict(self, *, text_limit: int = 420) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "text": truncate(self.text, text_limit),
            "object": self.object,
            "era": self.era,
            "category": self.category,
            "tags": self.tags[:6],
            # 用 `display_source` 而不是原始的 `source` 字段：
            # v2 语料的出处是结构化的 `citation`，遗留的 `source` 是空字符串 ——
            # 直接输出它会让「读 source 就能拿到出处」这个长期约定静默失效
            # （实测被 test_api 的断言抓到：出处为空）。这里给的是渲染好的一句话，
            # 结构化字段仍在下面的 `citation` 里。
            "source": self.display_source,
            "citation": self.citation.to_dict(),
            "source_type": self.source_type,
            "license": self.license,
            "authority": round(self.authority, 4),
            # 把「能不能当依据」直接暴露给前端与审计，而不是让调用方自己按类型判断
            "traceable": self.traceable,
            "evidential": self.evidential,
            "scores": {
                "bm25": round(self.bm25_score, 4),
                "dense": round(self.dense_score, 4),
                "fused": round(self.fused_score, 4),
                "rerank": round(self.rerank_score, 4),
                "final": round(self.final_score, 4),
            },
            "matched_terms": self.matched_terms[:6],
            "channels": self.channels,
        }


@dataclass
class RetrievalDiagnostics:
    query_count: int = 0
    bm25_hits: int = 0
    dense_hits: int = 0
    reranked: int = 0
    dense_degraded: bool = True
    rerank_degraded: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_count": self.query_count,
            "bm25_hits": self.bm25_hits,
            "dense_hits": self.dense_hits,
            "reranked": self.reranked,
            "dense_channel": "hashing(降级)" if self.dense_degraded else "dashscope-embedding",
            "rerank_channel": "rrf(降级)" if self.rerank_degraded else "gte-rerank-v2",
        }


class HybridRetriever:
    def __init__(self, corpus: Corpus, embedder: Embedder) -> None:
        self.corpus = corpus
        self.embedder = embedder
        self._bm25 = BM25Index()
        # 向量通道由 VectorIndex 抽象承担：pgvector（持久化 + HNSW）或进程内暴力检索。
        # 检索语义（余弦 + 取正分）两者完全一致，换的是「重启要不要重算向量」。
        self._index: VectorIndex | None = None
        self.vector_report: VectorReport | None = None

        for entry in corpus.all():
            self._bm25.add(entry.doc_id, entry.searchable_text)
        self._bm25.build()
        logger.info("BM25 索引构建完成: %d 文档, 平均长度 %.1f", len(self._bm25.doc_ids), self._bm25.avg_len)

    # ── 建库 ────────────────────────────────────────────────────────────────
    @classmethod
    async def build(cls, corpus_dir: Path | None = None) -> "HybridRetriever":
        corpus = load_corpus(corpus_dir or settings.corpus_path)
        embedder = build_embedder()
        retriever = cls(corpus, embedder)
        await retriever._warm_vectors()
        return retriever

    async def _warm_vectors(self) -> None:
        """建立向量通道。

        两条降级路径，任何一条成立都不能让服务起不来：
        1. embedding 调用失败 → 整体换成哈希向量（质量下降，但检索不断）；
        2. 向量库写入失败（没配库 / 没装 pgvector / 维度对不上）→ 退回进程内实现。
        """
        if not self.corpus:
            return
        entries = self.corpus.all()
        # 向量库与关系库同栈。`scripts/` 下的脚本不经过 FastAPI 的 lifespan，
        # 若这里不按需连接，脚本会静默用进程内检索 —— 而服务端用 pgvector，
        # 同一份语料在两处走不同通道，是最难察觉的一类不一致。
        await database.ensure_connected()
        index = build_vector_index(database, dimension=self.embedder.dimension)
        if index is None:  # pragma: no cover - 当前实现永远返回可用索引
            return

        report = await index.ensure(entries, self._embed_documents, self.embedder.name)
        if not report.ok:
            logger.warning("向量库不可用（%s），退回进程内检索: %s", report.backend, report.reason)
            index = MemoryVectorIndex(self.embedder.dimension)
            report = await index.ensure(entries, self._embed_documents, self.embedder.name)

        self._index = index
        self.vector_report = report
        logger.info(
            "语料向量就绪: %d 条（%s，复用 %d / 新算 %d，维度 %d）",
            report.count,
            report.backend,
            report.reused,
            report.embedded,
            report.dimension,
        )

    async def _embed_documents(self, texts: list[str]) -> tuple[list[list[float]], str]:
        """向量化语料；失败时降级为哈希向量，并返回**实际使用**的 embedder 名字。"""
        try:
            return await self.embedder.embed(texts), self.embedder.name
        except Exception as exc:  # noqa: BLE001
            logger.warning("语料向量预热失败，降级为哈希向量: %s", exc)
            metrics.record_degradation(component="embedder", reason=str(exc), fallback="hashing")
            self.embedder = HashingEmbedder(dimension=settings.vector_dim)
            return await self.embedder.embed(texts), self.embedder.name

    # ── 单 query 检索 ───────────────────────────────────────────────────────
    async def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        top_n: int | None = None,
        filters: dict[str, Any] | None = None,
        rerank: bool = True,
    ) -> tuple[list[RetrievedChunk], RetrievalDiagnostics]:
        top_k = top_k or settings.retrieval_top_k
        top_n = top_n or settings.retrieval_rerank_top_n
        diagnostics = RetrievalDiagnostics(query_count=1, dense_degraded=self.embedder.degraded)
        if not self.corpus or not query.strip():
            return [], diagnostics

        bm25_hits = self._bm25.search(query, top_k=top_k)
        diagnostics.bm25_hits = len(bm25_hits)

        dense_scores: dict[str, float] = {}
        try:
            if self._index is not None:
                query_vector = (await self.embedder.embed([query], kind="query"))[0]
                # 候选预算统一由这里给：向量通道只负责「召回」。
                # 进程内实现同样按这个上限截断，保证「换后端不改变融合行为」——
                # 否则 pgvector 取 top-16、进程内取全库，同一份语料会跑出两种排序。
                dense_scores = await self._index.search(query_vector, limit=max(top_k, top_n) * 2)
            diagnostics.dense_hits = len(dense_scores)
        except Exception as exc:  # noqa: BLE001 - 向量通道失败不应中断检索
            logger.warning("向量检索失败，退化为纯 BM25: %s", exc)
            metrics.record_degradation(component="retriever", reason=str(exc), fallback="bm25_only")

        candidates = self._fuse(bm25_hits, dense_scores, filters)
        if not candidates:
            return [], diagnostics

        # 注意：_fuse 返回的已经是带分数的 RetrievedChunk，**不能**再走 _to_chunk
        # 重新包装 —— CorpusEntry 与 RetrievedChunk 的字段名高度重叠，
        # 重新包装不会报错，但会静默丢掉 bm25/dense 分数与 channels 标记。
        chunks = list(candidates[: max(top_k, top_n)])

        if rerank and len(chunks) > 1:
            chunks, diagnostics = await self._rerank(query, chunks, top_n, diagnostics)

        return chunks[:top_n], diagnostics

    # ── 多 query 检索（Planner 会给出多个视角的 query）────────────────────────
    async def search_multi(
        self,
        queries: list[str],
        *,
        top_k: int | None = None,
        top_n: int | None = None,
    ) -> tuple[list[RetrievedChunk], RetrievalDiagnostics]:
        cleaned = [query.strip() for query in queries if query and query.strip()]
        if not cleaned:
            return [], RetrievalDiagnostics()

        results = await asyncio.gather(
            *(self.search(query, top_k=top_k, top_n=top_n, rerank=False) for query in cleaned)
        )

        merged: dict[str, RetrievedChunk] = {}
        aggregated = RetrievalDiagnostics(query_count=len(cleaned), dense_degraded=self.embedder.degraded)

        for chunks, diagnostics in results:
            aggregated.bm25_hits += diagnostics.bm25_hits
            aggregated.dense_hits += diagnostics.dense_hits
            for chunk in chunks:
                existing = merged.get(chunk.doc_id)
                if existing is None:
                    merged[chunk.doc_id] = chunk
                else:
                    # 同一史料被多个 query 命中 → 提升其最终分（多视角互证）
                    existing.final_score = max(existing.final_score, chunk.final_score)
                    existing.fused_score = max(existing.fused_score, chunk.fused_score)
                    existing.channels = sorted({*existing.channels, *chunk.channels})
                    existing.matched_terms = list(
                        dict.fromkeys([*existing.matched_terms, *chunk.matched_terms])
                    )[:8]

        ordered = sorted(merged.values(), key=lambda item: item.final_score, reverse=True)
        reranked, aggregated = await self._rerank(
            " ".join(cleaned), ordered, top_n or settings.retrieval_rerank_top_n, aggregated
        )
        return reranked, aggregated

    # ── 内部 ────────────────────────────────────────────────────────────────
    def _fuse(
        self,
        bm25_hits: list,
        dense_scores: dict[str, float],
        filters: dict[str, Any] | None,
    ) -> list[RetrievedChunk]:
        bm25_max = max((hit.score for hit in bm25_hits), default=0.0) or 1.0
        dense_max = max(dense_scores.values(), default=0.0) or 1.0

        chunks: dict[str, RetrievedChunk] = {}

        for hit in bm25_hits:
            entry = self.corpus.get(hit.doc_id)
            if entry is None or not self._match_filters(entry, filters):
                continue
            chunk = self._to_chunk(entry)
            chunk.bm25_score = hit.score
            chunk.matched_terms = hit.matched_terms
            chunk.channels.append("bm25")
            chunks[hit.doc_id] = chunk

        for doc_id, score in dense_scores.items():
            entry = self.corpus.get(doc_id)
            if entry is None or not self._match_filters(entry, filters):
                continue
            chunk = chunks.get(doc_id) or self._to_chunk(entry)
            chunk.dense_score = score
            if "dense" not in chunk.channels:
                chunk.channels.append("dense")
            chunks[doc_id] = chunk

        alpha = settings.hybrid_alpha
        for chunk in chunks.values():
            bm25_norm = chunk.bm25_score / bm25_max
            dense_norm = max(0.0, chunk.dense_score) / dense_max
            chunk.fused_score = alpha * bm25_norm + (1.0 - alpha) * dense_norm
            # 可信度加权：同一相关度下，发掘报告优先于二手科普
            chunk.final_score = chunk.fused_score * (0.75 + 0.25 * chunk.authority)

        return sorted(chunks.values(), key=lambda item: item.final_score, reverse=True)

    async def _rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_n: int,
        diagnostics: RetrievalDiagnostics,
    ) -> tuple[list[RetrievedChunk], RetrievalDiagnostics]:
        if not chunks:
            return chunks, diagnostics
        try:
            results = await get_reranker().rerank(
                query, [(chunk.doc_id, chunk.text) for chunk in chunks], top_n
            )
            by_id = {chunk.doc_id: chunk for chunk in chunks}
            ordered: list[RetrievedChunk] = []
            for result in results:
                chunk = by_id.get(result.doc_id)
                if chunk is None:
                    continue
                chunk.rerank_score = result.score
                # 重排分主导，保留 30% 融合分以抑制 cross-encoder 的偶发翻转
                chunk.final_score = 0.7 * result.score + 0.3 * chunk.final_score
                if "rerank" not in chunk.channels:
                    chunk.channels.append("rerank")
                ordered.append(chunk)
            ordered.sort(key=lambda item: item.final_score, reverse=True)
            diagnostics.reranked = len(ordered)
            diagnostics.rerank_degraded = bool(getattr(get_reranker(), "degraded", False))
            return ordered or chunks, diagnostics
        except ProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("重排失败，回退到融合分排序: %s", exc)
            metrics.record_degradation(component="reranker", reason=str(exc), fallback="fused_score")
            return chunks[:top_n], diagnostics

    @staticmethod
    def _match_filters(entry: CorpusEntry, filters: dict[str, Any] | None) -> bool:
        if not filters:
            return True
        for key in ("object", "era", "category"):
            wanted = filters.get(key)
            if wanted and entry.__getattribute__(key) != wanted:
                return False
        wanted_tags = filters.get("tags") or []
        if wanted_tags and not set(wanted_tags) & set(entry.tags):
            return False
        exclude = filters.get("exclude_tags") or []
        if exclude and set(exclude) & set(entry.tags):
            return False

        # 按来源类型排除。问答链路靠它实现「自撰内容不许当依据」——
        # 过滤必须发生在**召回阶段**而不是召回后再筛：召回后再筛会让
        # 被排除的条目白白占掉 top_n 名额，导致真正可用的证据被挤在门外。
        excluded_types = filters.get("exclude_source_types") or []
        if excluded_types and entry.source_type in set(excluded_types):
            return False
        return True

    def _to_chunk(self, entry: CorpusEntry) -> RetrievedChunk:
        return RetrievedChunk(
            doc_id=entry.doc_id,
            title=entry.title,
            text=entry.text,
            object=entry.object,
            era=entry.era,
            category=entry.category,
            tags=list(entry.tags),
            source=entry.source,
            authority=entry.authority,
            source_type=entry.source_type,
            license=entry.license,
            quote=entry.quote,
            note=entry.note,
            citation=entry.citation,
            traceable=entry.is_traceable,
            evidential=entry.is_evidential,
        )

    def stats(self) -> dict[str, Any]:
        report = self.vector_report
        return {
            "corpus_size": len(self.corpus),
            "corpus_files": self.corpus.files,
            # 语料整体指纹：回答「这份答案基于哪一版语料」的可复核凭证
            "corpus_fingerprint": self.corpus.fingerprint,
            # 语料质量信号。只报「50 条」是一种粉饰 —— 必须同时说出
            # 其中有多少条不可追溯、多少条没有来源类型。
            "corpus_quality": {
                "untraceable": self.corpus.untraceable,
                "legacy_without_source_type": self.corpus.legacy_without_source_type,
                "handwritten_authority": self.corpus.handwritten_authority,
                "authority_table": dict(SOURCE_AUTHORITY),
            },
            "vector_count": report.count if report else 0,
            # 向量链路的真实档位必须能被查询到：排查「检索为什么变了」时，
            # 第一个要确认的就是「现在到底走的是 pgvector 还是进程内」
            "vector_backend": report.backend if report else "未初始化",
            "vector_dimension": report.dimension if report else self.embedder.dimension,
            "vector_reused": report.reused if report else 0,
            "vector_embedded": report.embedded if report else 0,
            "embedder": self.embedder.name,
            "embedder_degraded": self.embedder.degraded,
            # 繁简折叠是否生效。必须暴露：语料里 83% 含繁体，
            # 折叠一旦不可用，古籍会整体丧失召回能力，而现象只是「检索结果变差了」——
            # 没有这个字段，排查会从「换个查询试试」开始，浪费很久。
            "fold_backend": FOLD_BACKEND,
            "facets": self.corpus.facets() if self.corpus else {},
        }

    async def aclose(self) -> None:
        if self._index is not None:
            await self._index.aclose()
        await self.embedder.aclose()


# ── 进程内单例 ──────────────────────────────────────────────────────────────
_retriever: HybridRetriever | None = None
_lock = asyncio.Lock()


async def get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        async with _lock:
            if _retriever is None:
                _retriever = await HybridRetriever.build()
    return _retriever


async def reset_retriever() -> None:
    """评估脚本 / 热更新语料后调用。"""
    global _retriever
    if _retriever is not None:
        await _retriever.aclose()
    _retriever = None
