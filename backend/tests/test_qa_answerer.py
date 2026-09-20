"""领域问答的契约测试。

守的是**防幻觉**那几条硬约束。它们不是「模型应该怎么做」的建议，
而是代码必须保证的行为 —— 写在 prompt 里靠模型自觉是不算数的。
"""

from __future__ import annotations

import asyncio

import pytest

from app.qa.answerer import _extract_indices, answer
from app.rag.corpus import Corpus, CorpusEntry
from app.rag.embedder import HashingEmbedder
from app.rag.store import HybridRetriever


def _entry(doc_id: str, text: str, source_type: str, title: str = "条目") -> CorpusEntry:
    return CorpusEntry.from_dict(
        {
            "doc_id": doc_id,
            "title": title,
            "text": text,
            "source_type": source_type,
            "license": "public_domain" if source_type == "ancient_text" else "unknown",
            "citation": {
                "title": title,
                "author": "测试作者",
                "publisher": "测试出版者",
                "year": "2026",
                "locator": "卷一",
                "url": None,
                "accessed": None,
            },
        }
    )


@pytest.fixture
def mixed_retriever() -> HybridRetriever:
    """含「可当依据」与「不可当依据」两类条目的检索器（只建 BM25，不连数据库）。"""
    corpus = Corpus(entries={})
    ancient = _entry(
        "ancient-1",
        "有蜀侯蠶叢，其目縱，始稱王。死，作石棺、石槨。",
        "ancient_text",
        "蜀志",
    )
    ancient.note = "测试用加工说明：校勘注已剥离"
    corpus.entries["ancient-1"] = ancient
    corpus.entries["encyclopedia-1"] = _entry(
        "encyclopedia-1",
        "青铜纵目面具宽138厘米，高64.5厘米，眼睛呈柱状向外凸。",
        "encyclopedia",
        "纵目面具",
    )
    corpus.entries["project-1"] = _entry(
        "project-1", "本项目自撰的风格提示，用于出图参考，不构成史料。", "project_doc"
    )
    corpus.entries["unknown-1"] = _entry("unknown-1", "来源不明的一段话，无法追溯到出处。", "unknown")
    return HybridRetriever(corpus, HashingEmbedder())


class TestCitationParsing:
    def test_extracts_indices_from_answer(self):
        assert _extract_indices("面具宽138厘米[1]，眼睛外凸[2]。", limit=3) == [1, 2]

    def test_deduplicates(self):
        assert _extract_indices("甲[1]乙[1]丙[2]", limit=3) == [1, 2]

    def test_out_of_range_is_dropped(self):
        """超出候选数量的编号必须丢弃 —— 那意味着模型引用了它没见过的东西。"""
        assert _extract_indices("甲[1]乙[9]", limit=3) == [1]

    def test_no_markers(self):
        assert _extract_indices("没有任何标记的回答", limit=3) == []


class TestNonEvidentialSourcesAreExcluded:
    def test_project_doc_and_unknown_never_cited(self, mixed_retriever):
        """自撰内容与来源不明的一律不得作为依据。

        这是问答链路最核心的一条：它们可以供图，不能当史料。
        """
        result = asyncio.run(
            answer("青铜纵目面具的宽度是多少", retriever=mixed_retriever)
        )
        assert result.refused is False
        cited_types = {c["source_type"] for c in result.citations}
        assert "project_doc" not in cited_types
        assert "unknown" not in cited_types

    def test_filter_runs_at_recall_not_after(self, mixed_retriever):
        """过滤必须在召回阶段生效。

        若只在上层筛掉，被排除的条目会先占掉 top_n 名额，
        导致真正可用的证据被挤在门外 —— 那是一种很难发现的召回损失。
        """
        chunks, _ = asyncio.run(
            mixed_retriever.search(
                "纵目面具",
                top_n=2,
                rerank=False,
                filters={"exclude_source_types": ["project_doc", "unknown"]},
            )
        )
        assert chunks, "应当至少召回一条"
        assert all(c.source_type not in {"project_doc", "unknown"} for c in chunks)


class TestRefusalWhenNoEvidence:
    def test_refuses_instead_of_making_things_up(self, mixed_retriever):
        """检索不到可依据的记载时必须拒绝，而不是用模型知识补一个答案。

        这是与普通 chatbot 的分界线：给它一个语料里没有的问题，
        它应该回答「不知道」，而不是回答一个听起来合理的答案。
        """
        result = asyncio.run(
            answer("量子计算机的退相干时间有多长", retriever=mixed_retriever)
        )
        # 语料里没有相关内容 → 要么拒绝，要么证据数为 0
        assert result.refused or result.evidence_count == 0
        if result.refused:
            assert result.citations == []


class TestCitationContentComesFromCorpus:
    def test_citation_fields_are_corpus_values(self, mixed_retriever):
        """引用的展示内容必须取自语料条目。

        模型可以决定「用哪一条」，但决定不了「这一条长什么样」——
        出处、原文、链接都必须是语料原值，否则引用就不再是引用。
        """
        result = asyncio.run(answer("蚕丛的眼睛有什么特别", retriever=mixed_retriever))
        assert result.citations, "应当至少有一条引用"
        for citation in result.citations:
            assert citation["doc_id"] in mixed_retriever.corpus.entries
            entry = mixed_retriever.corpus.get(citation["doc_id"])
            assert citation["quote"] == (entry.quote or entry.text)
            assert citation["title"] == entry.title
            assert citation["source_type"] == entry.source_type

    def test_note_is_carried_through(self, mixed_retriever):
        """条目的加工说明必须一路传到引用里。

        引用一部古籍却不说明它被整理过，会让读者以为看到的就是某个版本的原文。

        这里测的是**机制**（CorpusEntry.note → RetrievedChunk.note → citation.note），
        而不是当前语料的状态：仓库里那份 v2 是在实现 `note` 之前采集的，
        重采又被网络挡住，所以它的古籍条目暂时没有这个字段。
        用合成语料测机制，比让一个测试依赖「语料采到第几版」更稳 ——
        后者会在每次重采后随机变红变绿。
        """
        result = asyncio.run(answer("蚕丛的眼睛有什么特别", retriever=mixed_retriever))
        ancient = [c for c in result.citations if c["source_type"] == "ancient_text"]
        assert ancient, "应当召回到古籍条目"
        assert all(c["note"] for c in ancient), "加工说明没有传到引用里"


class TestAnswerableFlag:
    """`answerable` 是前端区分「回答了」与「明确拒绝」的唯一依据。

    这一组用例来自一次端到端暴露的问题：模型正确写出了「片段没有提供相关信息」，
    但那只是一句自由文本，`refused` 仍是 False —— 前端会把它渲染成正常回答。
    """

    def test_as_bool_accepts_common_forms(self):
        from app.qa.answerer import _as_bool

        assert _as_bool(True, default=False) is True
        assert _as_bool("true", default=False) is True
        assert _as_bool("是", default=False) is True
        assert _as_bool(False, default=True) is False
        assert _as_bool("no", default=True) is False
        assert _as_bool("不能", default=True) is False

    def test_as_bool_defaults_to_permissive_on_garbage(self):
        """解析不了时默认当作「可回答」。

        选这一侧是因为：把「其实能答」误判成拒绝，用户会以为语料里没有这条记载；
        而反过来（该拒绝却答了）还有 citations 能让人自己核对。
        """
        from app.qa.answerer import _as_bool

        assert _as_bool(None, default=True) is True
        assert _as_bool("可能吧", default=True) is True
        assert _as_bool("可能吧", default=False) is False

    def test_unanswerable_marks_refused_and_drops_citations(self, mixed_retriever, monkeypatch):
        """模型判定答不上来时：refused=True，且**不附带任何引用**。

        拒绝回答却挂着一堆出处，会让人以为那些出处支持了什么结论。
        """
        _fake_llm(monkeypatch, {"answerable": False, "answer": "片段没有提供相关信息"})

        result = asyncio.run(answer("青铜纵目面具有多宽", retriever=mixed_retriever))
        assert result.refused is True
        assert result.citations == []
        assert result.confidence == "low"
        assert "没有提供" in result.answer

    def test_answerable_true_keeps_citations(self, mixed_retriever, monkeypatch):
        _fake_llm(
            monkeypatch,
            {"answerable": True, "answer": "纵目面具宽138厘米[1]。", "confidence": "high"},
        )

        result = asyncio.run(answer("青铜纵目面具有多宽", retriever=mixed_retriever))
        assert result.refused is False
        assert len(result.citations) == 1
        assert result.citations[0]["index"] == 1

    def test_missing_answerable_defaults_to_answerable(self, mixed_retriever, monkeypatch):
        """模型没给 `answerable` 时（旧提示词、模型抽风）不能当成「拒绝」。

        解析失败时默认取「可回答」这一侧：把「其实能答」误判成拒绝，
        用户会以为语料里没有这条记载，而反过来还有引用能让人自己核对。
        """
        _fake_llm(monkeypatch, {"answer": "纵目面具宽138厘米[1]。"})

        result = asyncio.run(answer("青铜纵目面具有多宽", retriever=mixed_retriever))
        assert result.refused is False
        assert result.citations


def _fake_llm(monkeypatch, payload: dict) -> None:
    """把模型调用替换成固定返回值。

    注意 patch 的是 `settings.dashscope_api_key` 而不是 `registry.enabled` ——
    后者是只读 property，且 patch 数据来源更贴近真实路径
    （`enabled` 本身就是从 key 推出来的）。
    """
    from app.core.config import settings
    from app.models.registry import registry

    class _Result:
        model = "fake"
        degraded = False

    async def fake_call_json(role, messages, **kwargs):  # noqa: ARG001
        return payload, _Result()

    monkeypatch.setattr(settings, "dashscope_api_key", "sk-test-not-a-real-key")
    monkeypatch.setattr(registry, "call_json", fake_call_json)


class TestAskEndpoint:
    def test_endpoint_exists(self, client):
        response = client.post("/api/ask", json={"question": "青铜纵目面具有多宽", "top_n": 3})
        assert response.status_code == 200
        body = response.json()
        assert "answer" in body
        assert "citations" in body
        assert "refused" in body

    def test_rejects_too_short_question(self, client):
        response = client.post("/api/ask", json={"question": "嗯"})
        assert response.status_code == 422

    def test_smalltalk_bypasses_corpus_search(self, client):
        """「你是谁」这类闲聊不该进语料检索——截图里它拿到过「史料库中无相关记载」。"""
        for question in ("你是谁", "你好", "跟我来聊天可以吗", "多谢啦"):
            response = client.post("/api/ask", json={"question": question})
            assert response.status_code == 200, question
            body = response.json()
            assert body["refused"] is False, question
            assert body["retrieval_mode"] == "smalltalk", question
            assert body["citations"] == [], question
            assert body["answer"].strip(), question

    def test_knowledge_question_still_goes_to_qa(self, client):
        """带创作线索/知识问法的消息不受闲聊闸门影响。"""
        response = client.post("/api/ask", json={"question": "青铜纵目面具的眼睛为什么外凸"})
        assert response.status_code == 200
        assert response.json()["retrieval_mode"] != "smalltalk"
