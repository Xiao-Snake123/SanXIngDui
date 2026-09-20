"""检索链路测试：BM25、混合融合、重排归一化、过滤器。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.rag.bm25 import BM25Index
from app.rag.corpus import load_corpus
from app.rag.embedder import HashingEmbedder
from app.rag.rerank import RRFReranker
from app.rag.store import HybridRetriever
from app.rag.text import tokenize


class TestTokenizer:
    def test_bigram_covers_domain_terms(self):
        tokens = tokenize("青铜纵目面具")
        assert "纵目" in tokens, "领域专名必须被 bigram 覆盖"
        assert "面具" in tokens
        assert "青" in tokens, "unigram 用于保证单字召回"

    def test_stopwords_removed(self):
        assert "的" not in tokenize("青铜的纹饰")

    def test_empty_input(self):
        assert tokenize("") == []
        assert tokenize("   ") == []


class TestBM25:
    def test_ranking_prefers_matching_document(self):
        index = BM25Index()
        index.add("about_tree", "青铜神树 三层九枝 九只神鸟 分铸法 焊接")
        index.add("about_mask", "青铜纵目面具 柱状凸目 双耳平展 体量最大")
        index.add("about_scepter", "金杖 鱼 鸟 箭 人头像 锤揲 金皮")
        index.build()

        hits = index.search("纵目面具 凸目", top_k=3)
        assert hits, "应当有命中"
        assert hits[0].doc_id == "about_mask"
        assert hits[0].matched_terms

    def test_empty_index_returns_empty(self):
        index = BM25Index().build()
        assert index.search("任意查询") == []

    def test_no_match_returns_empty(self):
        index = BM25Index()
        index.add("doc", "青铜神树")
        index.build()
        assert index.search("完全无关的英文查询 zzzz") == []

    def test_idf_downweights_ubiquitous_terms(self):
        index = BM25Index()
        for i in range(5):
            index.add(f"doc{i}", "三星堆 文物 描述")
        index.add("rare", "三星堆 纵目 面具")
        index.build()
        assert index.idf["纵目"] > index.idf["文物"]


class TestRRFReranker:
    def test_scores_are_normalized_and_monotonic(self):
        reranker = RRFReranker()
        candidates = [(f"doc{i}", f"text {i}") for i in range(10)]
        results = asyncio.run(reranker.rerank("q", candidates, top_n=5))

        assert results[0].score == pytest.approx(1.0), "rank 0 必须归一化到 1.0"
        scores = [item.score for item in results]
        assert scores == sorted(scores, reverse=True)
        assert all(0.0 < score <= 1.0 for score in scores)
        # 关键回归：曾经因为未归一化导致所有候选分数塌缩到 0.011、丧失区分度
        assert scores[0] - scores[-1] > 0.05


class TestHybridRetriever:
    @pytest.fixture(scope="class")
    def retriever(self, corpus_dir: Path) -> HybridRetriever:
        corpus = load_corpus(corpus_dir)
        assert len(corpus) > 0, "种子语料必须存在，否则检索链路无法验证"
        instance = HybridRetriever(corpus, HashingEmbedder())
        asyncio.run(instance._warm_vectors())
        return instance

    def test_corpus_loaded(self, retriever: HybridRetriever):
        stats = retriever.stats()
        assert stats["corpus_size"] >= 40
        assert stats["vector_count"] == stats["corpus_size"]

    def test_semantic_query_hits_expected_doc(self, retriever: HybridRetriever):
        chunks, diagnostics = asyncio.run(
            retriever.search("青铜纵目面具 形制 柱状凸目", top_n=3)
        )
        assert chunks
        titles = [chunk.title for chunk in chunks]
        assert any("纵目" in title for title in titles), f"命中的标题: {titles}"
        assert diagnostics.bm25_hits > 0

    def test_retrieval_scores_survive_the_pipeline(self, retriever: HybridRetriever):
        """回归测试：`search()` 曾把已带分数的 RetrievedChunk 又当 CorpusEntry 重新包装，
        由于两者字段高度重叠，不会报错但会静默丢掉所有 bm25/dense 分数与通道标记。
        这里把「分数必须真的传出来」固化成断言。"""
        chunks, _ = asyncio.run(retriever.search("青铜神树 分铸法", top_n=5))
        assert chunks
        assert any(chunk.bm25_score > 0 for chunk in chunks), "BM25 分数在管线中被丢弃了"
        assert all(chunk.channels for chunk in chunks), "通道来源标记在管线中被丢弃了"
        assert any(chunk.fused_score > 0 for chunk in chunks)

    def test_fusion_scores_are_bounded(self, retriever: HybridRetriever):
        chunks, _ = asyncio.run(retriever.search("青铜神树 分铸法", top_n=5))
        assert chunks
        for chunk in chunks:
            assert 0.0 <= chunk.final_score <= 1.5
            assert chunk.channels, "每条命中都要标明来自哪条通道，便于排查坏 case"

    def test_multi_query_merges_and_dedupes(self, retriever: HybridRetriever):
        chunks, diagnostics = asyncio.run(
            retriever.search_multi(["青铜大立人 形制", "大祭司 服饰", "金杖 纹饰"], top_n=6)
        )
        ids = [chunk.doc_id for chunk in chunks]
        assert len(ids) == len(set(ids)), "多路检索结果必须去重"
        assert diagnostics.query_count == 3

    def test_multi_query_boosts_multi_channel_hits(self, retriever: HybridRetriever):
        chunks, _ = asyncio.run(retriever.search_multi(["青铜神树", "神树 分铸法"], top_n=5))
        multi = [chunk for chunk in chunks if len(chunk.channels) > 1]
        assert multi, "被多路命中的史料应记录多个通道来源（互证信号）"

    def test_filters_apply(self, retriever: HybridRetriever):
        chunks, _ = asyncio.run(
            retriever.search("青铜", top_n=5, filters={"object": "青铜神树"})
        )
        assert chunks
        assert all(chunk.object == "青铜神树" for chunk in chunks)

    def test_empty_query_returns_nothing(self, retriever: HybridRetriever):
        chunks, diagnostics = asyncio.run(retriever.search("   "))
        assert chunks == []
        assert diagnostics.query_count == 1

    def test_exclude_tags_filter(self, retriever: HybridRetriever):
        chunks, _ = asyncio.run(
            retriever.search("青铜", top_n=8, filters={"exclude_tags": ["文化解读"]})
        )
        assert all("文化解读" not in chunk.tags for chunk in chunks)
