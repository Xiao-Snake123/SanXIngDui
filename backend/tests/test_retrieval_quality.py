"""检索质量的回归保护。

为什么把质量指标放进单测：在此之前检索质量**没有任何度量**，
改动是变好还是变坏只能靠人眼看几条结果。三次事故（回炉 bug、繁简 bug、
校勘注污染）都是靠实测数据才定位的 —— 既然数据管用，就该让它自动跑。

阈值取实测值下方留余量，不贴着实测值取：
贴太紧会让每次无关改动都变红（噪声），太松则抓不到退化。
实测（v2，仅 BM25，折叠开启）：recall@5=0.982 / recall@10=1.000 / MRR=0.822。

**这套指标有已知的乐观偏差**：评测集是我通读语料后写的，
所以它测的是「这些主题能不能检索到」，而不是「任意真实提问能不能检索到」。
要消除这个偏差需要真实用户提问，见 `docs/RAG_PLAN.md` §6。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.rag.corpus import load_corpus
from app.rag.embedder import HashingEmbedder
from app.rag.store import HybridRetriever
from app.rag.text import FOLD_BACKEND

# 阈值：低于这些值说明检索发生了可观测的退化
MIN_RECALL_AT_5 = 0.90
MIN_RECALL_AT_10 = 0.95
MIN_MRR = 0.70


def _eval_script_module():
    """复用 scripts/eval_retrieval.py 的实现，而不是在测试里再写一套指标。

    重复实现指标代码是危险的：测试用的公式一旦与评估脚本不一致，
    两边会给出不同的数字，而「哪个才是真的」无从判断。
    """
    import sys

    root = Path(__file__).resolve().parents[1]
    if str(root / "scripts") not in sys.path:
        sys.path.insert(0, str(root / "scripts"))
    import eval_retrieval

    return eval_retrieval


@pytest.fixture(scope="module")
def quality_run(backend_root: Path) -> dict:
    module = _eval_script_module()
    corpus_dir = backend_root / "data" / "corpus" / "v2"
    assert corpus_dir.exists(), "v2 语料不存在，检索质量无从验证"
    corpus = load_corpus(corpus_dir)
    items = module.load_golden(module.GOLDEN_PATH)

    # 只建 BM25：单测不得写数据库（见 conftest 的 hermetic_storage）
    retriever = HybridRetriever(corpus, HashingEmbedder())
    return {"items": items, "result": module.evaluate(retriever, items)}


class TestGoldenSetIsGrounded:
    def test_every_evidence_fragment_exists_in_corpus(self, backend_root: Path):
        """评测集的期望证据必须被语料支撑。

        这条是「不给自己出题」的机械约束：编造一条语料里不存在的期望，
        会让指标永久偏低，而现象看起来像「检索不行」。
        """
        module = _eval_script_module()
        corpus = load_corpus(backend_root / "data" / "corpus" / "v2")
        items = module.load_golden(module.GOLDEN_PATH)
        problems = module.validate_golden(items, corpus)
        assert not problems, "评测集与语料不一致:\n" + "\n".join(problems)

    def test_golden_set_covers_all_four_types(self, quality_run):
        """四类问题都要有，否则指标会被单一类型主导。"""
        types = {item["type"] for item in quality_run["items"]}
        assert types == {"专名直查", "语义改写", "形制数值", "跨文档比较"}

    def test_semantic_rewrites_do_not_copy_card_wording(self, quality_run):
        """语义改写类的问题不得直接抄卡片原文。

        抄原文测的只是词面匹配 —— 那是 BM25 的免费分，说明不了语义检索能力。
        这里做一个粗检查：问题里不应出现期望证据片段的完整字串。
        """
        for item in quality_run["items"]:
            if item["type"] != "语义改写":
                continue
            for fragment in item["evidence"]:
                assert fragment not in item["query"], (
                    f"{item['id']} 的问题直接包含了期望证据 {fragment!r}，"
                    "这测的只是词面匹配"
                )


class TestRetrievalQualityThresholds:
    def test_recall_at_5(self, quality_run):
        value = quality_run["result"]["overall"]["recall@5"]
        assert value >= MIN_RECALL_AT_5, f"recall@5={value:.3f} 低于阈值 {MIN_RECALL_AT_5}"

    def test_recall_at_10(self, quality_run):
        value = quality_run["result"]["overall"]["recall@10"]
        assert value >= MIN_RECALL_AT_10, f"recall@10={value:.3f} 低于阈值 {MIN_RECALL_AT_10}"

    def test_mrr(self, quality_run):
        value = quality_run["result"]["overall"]["mrr"]
        assert value >= MIN_MRR, f"MRR={value:.3f} 低于阈值 {MIN_MRR}"

    def test_every_type_has_some_recall(self, quality_run):
        """任一类型整体崩掉都要可见，不能被平均值掩盖。"""
        for type_name, bucket in quality_run["result"]["per_type"].items():
            assert bucket["recall@10"] >= 0.70, (
                f"类型「{type_name}」的 recall@10={bucket['recall@10']:.3f} 过低"
            )


class TestFoldIsActive:
    def test_fold_backend_available(self):
        """折叠不可用时检索会退化（实测 recall@10 从 1.000 掉到 0.912），
        所以「zhconv 没装」必须是显式的失败，而不是安静的降级。"""
        assert FOLD_BACKEND == "zhconv", (
            f"繁简折叠不可用（FOLD_BACKEND={FOLD_BACKEND}）。"
            "语料中 83% 含繁体，折叠失效会让古籍整体丧失召回能力。"
            "修复：pip install zhconv"
        )

    def test_simplified_query_hits_traditional_evidence(self, backend_root: Path):
        """端到端确认：简体提问能召回繁体古籍的证据。

        `RetrievedChunk.text` 是**原始正文**（繁体未改），所以断言要对它做折叠 ——
        这顺带证明了「折叠只作用于检索、不篡改正文」：原文里的「其目縱」仍是繁体。
        """
        from app.rag.text import fold

        corpus = load_corpus(backend_root / "data" / "corpus" / "v2")
        retriever = HybridRetriever(corpus, HashingEmbedder())
        chunks, _ = asyncio.run(retriever.search("蚕丛的眼睛有什么特别", top_n=10, rerank=False))
        assert chunks, "简体提问没有任何命中"
        assert any("其目纵" in fold(chunk.text) for chunk in chunks), (
            "简体提问没能召回《華陽國志》的「其目縱」记载"
        )
