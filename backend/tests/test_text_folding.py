"""繁简折叠的契约测试。

守两类东西：

1. **该折叠的地方确实折叠了** —— 否则繁体古籍召不回来（实测：古籍占语料 65%，
   却贡献 0 召回，「蚕丛 纵目」一条古籍都命中不了）。
2. **不该折叠的地方一个字都不能改** —— `normalize()` 与 `split_sentences()`
   服务于展示路径：它们切出的句子会作为「原文引用」进 prompt、也可能直接显示。
   在那里折叠会让《華陽國志》的引文变成简体，而用户看到的出处仍写着《華陽國志》
   —— 这不是「归一化」，是**篡改引文**。

第 2 类比第 1 类更容易出错：把 fold 塞进 normalize 会让所有检索测试都通过，
而错误只体现在「引用看起来是简体的」这种没人会去核对的地方。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.bm25 import BM25Index
from app.rag.corpus import Corpus, CorpusEntry, load_corpus
from app.rag.embedder import HashingEmbedder
from app.rag.store import HybridRetriever
from app.rag.text import FOLD_BACKEND, fold, normalize, split_sentences, tokenize

# 《華陽國志·蜀志》原文（繁体），本项目最关键的古代记载：
# 「有蜀侯蠶叢，其目縱，始稱王。」
CLASSICAL = "有蜀侯蠶叢，其目縱，始稱王。"
SIMPLIFIED = "有蜀侯蚕丛，其目纵，始称王。"


class TestFold:
    def test_traditional_becomes_simplified(self):
        assert fold("蠶叢") == "蚕丛"
        assert fold("魚鳧") == "鱼凫"
        assert fold("華陽國志") == "华阳国志"
        assert fold(CLASSICAL) == SIMPLIFIED

    def test_fold_is_idempotent(self):
        """幂等是必要的：折叠会在分词器、索引文本等多处被重复调用。"""
        once = fold(CLASSICAL)
        assert fold(once) == once

    def test_simplified_text_is_untouched(self):
        assert fold(SIMPLIFIED) == SIMPLIFIED
        assert fold("青铜纵目面具") == "青铜纵目面具"

    def test_non_chinese_is_untouched(self):
        assert fold("Bronze mask 138cm") == "Bronze mask 138cm"
        assert fold("") == ""

    def test_backend_is_reported(self):
        """折叠是否生效必须可查询 —— 否则「古籍召不回来」会变成一个查不出原因的现象。"""
        assert FOLD_BACKEND in {"zhconv", "disabled"}


class TestFoldDoesNotLeakIntoDisplay:
    """把 fold 塞进 normalize() 会让所有检索测试通过，但会篡改引文。这两条守住它。"""

    def test_normalize_does_not_fold(self):
        """`normalize` 会做 NFKC（全角→半角），但**不得**做繁简折叠。

        断言用「繁体字仍在」而不是「整串逐字不变」：NFKC 本来就会把全角逗号
        「，」转成半角「,」，那是它的既有职责。若要求整串不变，测的就不是
        「有没有折叠」而是「有没有 NFKC」，会把这条测试变成一个假警报源。
        """
        assert "蠶" in normalize(CLASSICAL)
        assert "縱" in normalize(CLASSICAL)
        assert "蚕" not in normalize(CLASSICAL)

    def test_split_sentences_preserves_traditional(self):
        """抽取式摘要会把这些句子当「原文引用」用，必须与原文逐字一致。"""
        sentences = split_sentences(CLASSICAL)
        assert sentences, "至少要切出一句"
        assert "蠶叢" in sentences[0]
        assert "蚕" not in sentences[0]


class TestTokenizeFolds:
    def test_traditional_and_simplified_share_tokens(self):
        """这是修复召回的核心：两侧必须落到同一批 token。"""
        traditional = set(tokenize(CLASSICAL))
        simplified = set(tokenize(SIMPLIFIED))
        assert traditional == simplified, "繁简两种写法应当产生完全相同的 token 集合"

    def test_domain_terms_survive(self):
        """折叠后「蠶叢」变成 bigram「蚕丛」，这是修复召回的载体。"""
        assert "蚕丛" in tokenize(CLASSICAL)

    def test_classical_word_order_limits_bigram_matching(self):
        """古汉语语序会让 bigram 落空 —— 记录这个局限，别假装它不存在。

        原文是「其**目縱**」而不是「纵目」，所以 bigram「纵目」在这条史料里
        **不存在**，只有 unigram 的「纵」「目」能命中。
        也就是说：查询「纵目」能召回这条史料，靠的是「蚕丛」等其他词，
        以及 unigram 兜底 —— 不是靠「纵目」这个词本身。

        这条测试把现状钉住：如果将来有人改用词典分词或调整 n-gram 策略，
        会立刻看到这里的变化，从而重新评估古汉语语序带来的影响。
        """
        tokens = tokenize(CLASSICAL)
        assert "纵目" not in tokens, "原文是「其目縱」，不该凭空出现「纵目」这个 bigram"
        assert "目纵" in tokens
        assert {"纵", "目"} <= set(tokens), "unigram 应当兜住这类语序差异"


class TestSearchableTextIsFolded:
    def test_searchable_text_is_simplified(self):
        entry = CorpusEntry(doc_id="a", title="蜀志", text=CLASSICAL)
        assert "蠶" not in entry.searchable_text
        assert "蚕丛" in entry.searchable_text

    def test_folding_changes_content_hash(self):
        """折叠必须体现在 content_hash 上。

        否则库里的旧向量会被判定为「还能用」而复用，向量通道会继续嵌入繁体文本 ——
        出现「BM25 修好了、向量通道仍是坏的」这种半修状态。
        """
        traditional = CorpusEntry(doc_id="a", title="t", text=CLASSICAL)
        already_simplified = CorpusEntry(doc_id="a", title="t", text=SIMPLIFIED)
        assert traditional.content_hash == already_simplified.content_hash


class TestBM25MatchesAcrossScripts:
    def test_simplified_query_hits_traditional_document(self):
        index = BM25Index()
        index.add("guji-1", CLASSICAL)          # 索引的是繁体原文
        index.add("wiki-1", "三星堆遗址位于广汉市。")
        index.build()

        hits = index.search("蚕丛 纵目", top_k=5)  # 查询用简体
        assert hits, "简体查询必须能命中繁体文献"
        assert hits[0].doc_id == "guji-1"


class TestRealCorpusRecall:
    """对仓库里那份真实 v2 语料的端到端检查 —— 它就是为这个 bug 采的。"""

    @pytest.fixture(scope="class")
    def retriever(self, backend_root: Path) -> HybridRetriever:
        directory = backend_root / "data" / "corpus" / "v2"
        assert directory.exists(), (
            "v2 语料不存在。它是 scripts/collect_corpus.py 的产出，"
            "删掉它会让「繁体古籍能否被召回」失去验证对象。"
        )
        corpus = load_corpus(directory)
        assert len(corpus) > 0
        # 只建 BM25，不 warm 向量 —— 避免单测写到真实数据库
        return HybridRetriever(corpus, HashingEmbedder())

    @pytest.mark.parametrize(
        "query",
        ["蚕丛 纵目", "鱼凫 王", "蜀侯 蚕丛 始称王"],
    )
    def test_simplified_query_retrieves_ancient_text(self, retriever, query):
        """修复前这三个查询命中 0 条古籍（全是维基简体条目）。

        这是本次改造的验收点：古籍占语料 65%，必须真的能被检索到。
        """
        chunks, _ = _search(retriever, query)
        assert chunks, f"{query!r} 无任何命中"
        types = {chunk.source_type for chunk in chunks}
        assert "ancient_text" in types, (
            f"{query!r} 仍然召回不到古籍（命中类型: {types}）—— 繁简折叠可能失效，"
            f"当前 fold_backend={FOLD_BACKEND}"
        )

    def test_ancient_text_entries_are_traditional(self, retriever):
        """前提校验：如果古籍被改成了简体，上面的用例会变得没有意义。"""
        ancient = [e for e in retriever.corpus.all() if e.source_type == "ancient_text"]
        assert ancient, "语料里应当有古籍条目"
        with_traditional = [e for e in ancient if fold(e.text) != e.text]
        assert with_traditional, "古籍条目应当保留繁体原文（不得在采集时被转成简体）"


def _search(retriever: HybridRetriever, query: str):
    import asyncio

    return asyncio.run(retriever.search(query, top_n=6, rerank=False))
