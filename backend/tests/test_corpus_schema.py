"""语料 schema v2 的契约测试。

这些用例守的不是「代码能不能跑」，而是**「可追溯」这个对外承诺是否成立**。
旧版把 `authority` 做成手写字段、把出处做成一句自由文本，结果是：
20/50 条写了 1.0（含「本项目内容质量规范」这类自我指涉条目），
而 author/publisher/year/page/url 全部缺失。

只要这些用例在，就没人能把 `authority` 改回手写、或让出处悄悄退化成装饰字段。
设计依据见 `docs/RAG_PLAN.md` §2。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.rag.corpus import (
    LICENSES,
    NON_EVIDENTIAL_TYPES,
    SOURCE_AUTHORITY,
    Citation,
    Corpus,
    CorpusEntry,
    authority_for,
    load_corpus,
)


def _paper_entry(**overrides) -> CorpusEntry:
    payload = {
        "doc_id": "sxd-report-0001",
        "title": "青铜纵目面具的形制",
        "text": "纵目面具双眼呈柱状向前外凸，眼球外凸约 16 厘米。",
        "source_type": "excavation_report",
        "license": "quote_only",
        "citation": {
            "title": "三星堆祭祀坑",
            "author": "四川省文物考古研究所",
            "publisher": "文物出版社",
            "year": "1999",
            "locator": "上册 第 178 页",
        },
    }
    payload.update(overrides)
    return CorpusEntry.from_dict(payload)


class TestAuthorityIsDerived:
    def test_authority_comes_from_source_type(self):
        """可信度必须由来源类型派生，而不是从别处读一个数字。"""
        assert _paper_entry().authority == SOURCE_AUTHORITY["excavation_report"]
        assert _paper_entry(source_type="news").authority == SOURCE_AUTHORITY["news"]
        assert _paper_entry(source_type="project_doc").authority == SOURCE_AUTHORITY["project_doc"]

    def test_handwritten_authority_in_payload_is_ignored(self):
        """旧语料里手写的 authority 不得生效。

        这条是回归的核心：一旦允许手写值覆盖派生值，
        「全都写 1.0」就会重新出现，可信度加权等于失效。
        """
        entry = _paper_entry(authority=1.0, source_type="news")
        assert entry.authority == SOURCE_AUTHORITY["news"]
        assert entry.authority != 1.0

    def test_unknown_source_type_falls_back_but_does_not_pretend(self):
        """未知来源按 unknown 处理 —— 既不放行成高可信，也不抹杀成 0。"""
        assert authority_for("从没见过的新类型") == SOURCE_AUTHORITY["unknown"]
        assert authority_for("") == SOURCE_AUTHORITY["unknown"]
        assert 0.0 < SOURCE_AUTHORITY["unknown"] < SOURCE_AUTHORITY["excavation_report"]

    def test_project_doc_is_lowest_and_not_evidential(self):
        """自撰内容必须是最低等级，且不得作为事实依据。"""
        assert SOURCE_AUTHORITY["project_doc"] == min(SOURCE_AUTHORITY.values())
        assert _paper_entry(source_type="project_doc").is_evidential is False
        assert _paper_entry(source_type="excavation_report").is_evidential is True

    def test_non_evidential_set_covers_self_authored_and_unknown(self):
        assert "project_doc" in NON_EVIDENTIAL_TYPES
        assert "unknown" in NON_EVIDENTIAL_TYPES


class TestTraceability:
    def test_paper_citation_needs_locator(self):
        """纸质文献：没有页码就只追到整本书，对「某句话说错了」的审计无用。"""
        base = {
            "title": "三星堆祭祀坑",
            "author": "四川省文物考古研究所",
            "publisher": "文物出版社",
            "year": "1999",
        }
        assert Citation(**base).is_traceable() is False
        assert Citation(**base, locator="上册 第 178 页").is_traceable() is True

    def test_web_citation_needs_access_date(self):
        """网络来源：网址会变，必须有抓取日期才可复核。"""
        assert Citation(title="三星堆遗址", url="https://zh.wikipedia.org/x").is_traceable() is False
        assert Citation(
            title="三星堆遗址", url="https://zh.wikipedia.org/x", accessed="2026-09-17"
        ).is_traceable() is True

    def test_empty_citation_is_not_traceable(self):
        assert Citation().is_traceable() is False
        assert CorpusEntry(doc_id="a", title="t", text="x").is_traceable is False

    def test_display_source_prefers_structured_citation(self):
        entry = _paper_entry(source="三星堆博物馆馆藏说明")
        assert "文物出版社" in entry.display_source
        assert entry.display_source != "三星堆博物馆馆藏说明"

    def test_display_source_falls_back_for_legacy_entries(self):
        """v1 条目没有 citation，展示时退回旧标签，而不是显示空。"""
        legacy = CorpusEntry.from_dict(
            {"doc_id": "old-1", "title": "t", "text": "x", "source": "某地方志"}
        )
        assert legacy.display_source == "某地方志"

    def test_citation_is_not_indexed_for_retrieval(self):
        """出处用于审计，不用于召回 ——
        「文物出版社」这类词混进索引会稀释真正的领域术语。"""
        entry = _paper_entry()
        assert "文物出版社" not in entry.searchable_text
        assert "纵目面具" in entry.searchable_text


class TestFingerprint:
    def test_same_content_same_fingerprint_regardless_of_order(self):
        first = Corpus(entries={})
        second = Corpus(entries={})
        for doc_id in ("b", "a"):
            first.entries[doc_id] = CorpusEntry(doc_id=doc_id, title="t", text=f"正文 {doc_id}")
        for doc_id in ("a", "b"):
            second.entries[doc_id] = CorpusEntry(doc_id=doc_id, title="t", text=f"正文 {doc_id}")
        assert first.fingerprint == second.fingerprint

    def test_content_change_changes_fingerprint(self):
        before = Corpus(entries={"a": CorpusEntry(doc_id="a", title="t", text="原文")})
        after = Corpus(entries={"a": CorpusEntry(doc_id="a", title="t", text="改过的正文")})
        assert before.fingerprint != after.fingerprint

    def test_fingerprint_flags_title_change_too(self):
        """指纹必须覆盖 searchable_text（真正被向量化的那份文本）。"""
        before = Corpus(entries={"a": CorpusEntry(doc_id="a", title="旧标题", text="正文")})
        after = Corpus(entries={"a": CorpusEntry(doc_id="a", title="新标题", text="正文")})
        assert before.fingerprint != after.fingerprint


class TestLoaderReporting:
    def _write(self, path: Path, rows: list[dict]) -> None:
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8"
        )

    def test_legacy_entries_load_but_are_flagged(self, tmp_path: Path):
        """v1 条目仍可装载（不能让服务起不来），但必须被**计数并告警**。

        静默降权最危险：它会让「检索质量变差」变成查不出原因的现象。
        """
        self._write(
            tmp_path / "legacy.jsonl",
            [
                {"doc_id": "old-1", "title": "旧条目", "text": "正文一", "source": "某地方志"},
                {
                    "doc_id": "new-1", "title": "新条目", "text": "正文二",
                    "source_type": "museum_official", "license": "official_public",
                    "citation": {
                        "title": "官方藏品页", "url": "https://www.sxd.cn/x", "accessed": "2026-09-17",
                    },
                },
            ],
        )
        corpus = load_corpus(tmp_path)
        assert len(corpus) == 2
        assert corpus.legacy_without_source_type == 1
        assert corpus.untraceable == 1
        assert corpus.entries["new-1"].is_traceable is True
        assert corpus.entries["old-1"].source_type == "unknown"

    def test_handwritten_authority_conflicts_are_counted(self, tmp_path: Path):
        self._write(
            tmp_path / "legacy.jsonl",
            [{"doc_id": "a", "title": "t", "text": "x", "authority": 1.0}],
        )
        corpus = load_corpus(tmp_path)
        # 源文件写着 1.0，派生值是 unknown(0.50) → 必须被记为冲突
        assert corpus.handwritten_authority == 1
        assert corpus.entries["a"].authority == SOURCE_AUTHORITY["unknown"]

    def test_comment_lines_are_skipped(self, tmp_path: Path):
        (tmp_path / "c.jsonl").write_text(
            '// 这是说明行\n{"doc_id": "a", "title": "t", "text": "x"}\n', encoding="utf-8"
        )
        assert len(load_corpus(tmp_path)) == 1

    def test_missing_directory_returns_empty_corpus(self, tmp_path: Path):
        corpus = load_corpus(tmp_path / "nope")
        assert len(corpus) == 0
        assert corpus.fingerprint  # 空语料也要有确定指纹，不能抛异常


class TestRealCorpus:
    """对仓库里那份真实语料的检查（不假设它已经迁移完成）。"""

    def test_corpus_loads_and_exposes_quality_signals(self, corpus_dir: Path):
        corpus = load_corpus(corpus_dir)
        assert len(corpus) > 0
        stats = {
            "untraceable": corpus.untraceable,
            "legacy": corpus.legacy_without_source_type,
        }
        # 迁移期：只断言「信号被如实计算出来」，不断言具体数值 ——
        # 数值会随素材补充而变化，硬编码它只会让测试变成维护负担。
        assert stats["untraceable"] <= len(corpus)
        assert stats["legacy"] <= len(corpus)
        assert corpus.fingerprint

    def test_every_entry_has_a_known_license_value(self, corpus_dir: Path):
        corpus = load_corpus(corpus_dir)
        for entry in corpus.all():
            assert entry.license in LICENSES, f"{entry.doc_id} 的 license 取值非法: {entry.license}"

    def test_every_entry_source_type_is_in_the_authority_table(self, corpus_dir: Path):
        corpus = load_corpus(corpus_dir)
        for entry in corpus.all():
            assert entry.source_type in SOURCE_AUTHORITY, (
                f"{entry.doc_id} 的 source_type={entry.source_type!r} 不在 SOURCE_AUTHORITY 表里，"
                "会被静默按 unknown 处理"
            )
