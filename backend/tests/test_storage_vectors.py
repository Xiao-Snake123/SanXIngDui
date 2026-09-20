"""向量复用判定的离线单测。

为什么单独测这一条：真实存储链路被 `conftest.py` 钉在进程内实现上，
pgvector 的分支没有单测覆盖（由 `scripts/check_storage.py` 显式验证）。
但「哪些条目要重新向量化」这个**判断**是纯逻辑，不该跟着 I/O 一起被排除在单测之外 ——
它恰好又是最容易写错、错了还不报错的一处：

  * 只看 doc_id  → 改了史料正文的条目会继续用旧向量，检索静默变差；
  * 不看 embedder → 换了 embedding 模型后会拿两个语义空间的向量做余弦。

这两种错误都不会抛异常、不会打日志，只会让「召回好像不太准」，
所以必须有断言把它钉住。
"""

from __future__ import annotations

from app.rag.corpus import CorpusEntry
from app.storage.vectors import entries_needing_embedding

EMBEDDER = "hashing-ngram-1024"


def _entry(doc_id: str, text: str = "青铜大立人的形制与纹饰") -> CorpusEntry:
    return CorpusEntry(doc_id=doc_id, title="青铜大立人", text=text)


def test_all_matching_entries_are_reused():
    entries = [_entry("a"), _entry("b")]
    existing = {entry.doc_id: (EMBEDDER, entry.content_hash) for entry in entries}
    assert entries_needing_embedding(entries, existing, EMBEDDER) == []


def test_new_doc_id_needs_embedding():
    entries = [_entry("a"), _entry("b")]
    existing = {"a": (EMBEDDER, entries[0].content_hash)}
    pending = entries_needing_embedding(entries, existing, EMBEDDER)
    assert [entry.doc_id for entry in pending] == ["b"]


def test_changed_body_needs_embedding_even_though_id_is_the_same():
    entry = _entry("a")
    stale = {entry.doc_id: (EMBEDDER, entry.content_hash)}

    entry.text = "青铜大立人的形制与纹饰（修订：补充了分铸接缝的描述）"
    pending = entries_needing_embedding([entry], stale, EMBEDDER)

    assert [item.doc_id for item in pending] == ["a"], (
        "正文改了但 doc_id 没变时必须重算 —— 否则检索会继续用旧向量，且没有任何报错"
    )


def test_changed_title_or_tags_also_invalidate_the_vector():
    # 指纹覆盖的是 searchable_text（真正被向量化的那份文本），
    # 所以标题/标签这类参与索引的字段改了同样要重算
    entry = _entry("a")
    stale = {entry.doc_id: (EMBEDDER, entry.content_hash)}

    entry.tags = ["新标签"]
    assert len(entries_needing_embedding([entry], stale, EMBEDDER)) == 1


def test_different_embedder_invalidates_everything():
    entries = [_entry("a"), _entry("b")]
    existing = {entry.doc_id: ("dashscope-embedding", entry.content_hash) for entry in entries}
    pending = entries_needing_embedding(entries, existing, EMBEDDER)
    assert len(pending) == 2, "换了 embedding 模型后旧向量必须整体重算"


def test_legacy_rows_without_fingerprint_are_recomputed():
    # 加 content_hash 列之前写入的行是 NULL。它们天然不等于任何指纹，
    # 于是会被重算一遍 —— 这正是迁移时期望的行为（宁可多花一次钱）
    entries = [_entry("a")]
    pending = entries_needing_embedding(entries, {"a": (EMBEDDER, None)}, EMBEDDER)
    assert len(pending) == 1


def test_empty_database_returns_everything():
    entries = [_entry("a"), _entry("b"), _entry("c")]
    assert len(entries_needing_embedding(entries, {}, EMBEDDER)) == 3


def test_fingerprint_is_stable_across_instances():
    """同一份文本在不同进程里必须算出同一个指纹（否则每次启动都会全量重算）。"""
    first = _entry("a").content_hash
    second = CorpusEntry(doc_id="a", title="青铜大立人", text="青铜大立人的形制与纹饰").content_hash
    assert first == second
    assert len(first) == 32, "blake2b-128 的十六进制固定 32 字符，与 String(32) 列宽一致"
