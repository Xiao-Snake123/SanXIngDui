"""原始数据体检：把「看起来没问题」变成「逐条列出的检查项」。

为什么要它：数据问题大多是**静默**的 —— 少一篇文档不会报错、frontmatter 缺字段
不会报错、索引里堆了三遍重复行也不会报错。不主动查，这些问题会一路带进检索，
最后表现为「回答不准」，而那时已经很难回溯是哪一步坏的。

检查项（按"会伤到什么"排序）：
  1. 语料卡可追溯性  —— 缺 url/accessed 的卡片不能当事实依据（Citation.is_traceable）
  2. 索引重复行      —— `_append_index` 是追加写，重跑会累积（实测百科重跑过 4 次）
  3. frontmatter 完整性 —— 缺 title/url/license 的文档无法审计
  4. 残留标记/HTML   —— `<sup>`、`[[`、`{{` 会被 BM25 当词元索引
  5. 残留繁体        —— 已转换目录不该再有繁体
  6. 极短/零卡文档   —— 收进来却不贡献任何证据，白占位置
  7. 重复正文        —— 跨文件重复会让同一条证据反复占 top_n 名额

用法：python scripts/audit_raw.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.text import fold  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

REQUIRED_FRONT = ("title", "url", "source", "license", "accessed")
MARKUP_RE = re.compile(r"<[^>]+>|\[\[|\{\{")
SHORT_DOC = 100  # 正文短于此视为「极短」

# 来源 -> (原始文档目录, 语料卡文件)
SOURCES: dict[str, tuple[str, str | None]] = {
    "wikipedia": ("wikipedia", "wikipedia_crawl"),
    "baike": ("baike", "baike_crawl"),
    "en": ("en", "en_crawl"),
    "ja": ("ja", "ja_crawl"),
}
LEGACY_CARDS = ("wikipedia", "wikisource")  # collect_corpus 早期产物（无对应 raw 目录）
EXTRA_CARDS = ("pdf_crawl",)  # PDF 切分产物（原文是 PDF，不在 md 体检范围内）


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _raw(name: str) -> Path:
    return _root() / "data" / "raw" / name


def _cards_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "corpus" / "v2"


def _split_front(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields, text[end + 5 :]


def _load_cards(path: Path) -> list[dict]:
    if not path.exists():
        return []
    cards = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        try:
            cards.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    return cards


def audit_docs() -> None:
    print("\n【1】原始文档：目录与 frontmatter")
    for name in SOURCES:
        directory = _raw(name)
        if not directory.exists():
            print(f"  {name:10s} 目录不存在")
            continue
        docs = sorted(directory.glob("*.md"))
        missing: list[str] = []
        short: list[tuple[str, int]] = []
        traditional: list[str] = []
        markup: list[str] = []
        for path in docs:
            fields, body = _split_front(path.read_text(encoding="utf-8"))
            absent = [key for key in REQUIRED_FRONT if not fields.get(key)]
            if absent:
                missing.append(f"{path.name}(缺{'/'.join(absent)})")
            if len(body.strip()) < SHORT_DOC:
                short.append((path.name, len(body.strip())))
            if fold(body) != body:
                traditional.append(path.name)
            if MARKUP_RE.search(body):
                markup.append(path.name)
        print(f"  {name:10s} {len(docs):>3} 篇 | frontmatter 缺字段 {len(missing)}"
              f" | 极短(<{SHORT_DOC}字) {len(short)} | 残留繁体 {len(traditional)} | 残留标记 {len(markup)}")
        for label, items in (("缺字段", missing), ("极短", [f"{n}({c}字)" for n, c in short]),
                             ("残留繁体", traditional), ("残留标记", markup)):
            if items:
                print(f"       {label}: {', '.join(items[:8])}{' …' if len(items) > 8 else ''}")


def audit_index() -> None:
    print("\n【2】索引 _index.jsonl：重复行（重跑累积）")
    for name in SOURCES:
        path = _raw(name) / "_index.jsonl"
        if not path.exists():
            print(f"  {name:10s} 无索引")
            continue
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        counter = Counter(str(row.get("requested") or "") for row in rows)
        dupes = {key: count for key, count in counter.items() if count > 1}
        status = Counter(str(row.get("status") or "") for row in rows)
        print(f"  {name:10s} {len(rows):>4} 行 | 唯一 requested {len(counter)}"
              f" | 重复 {len(dupes)} 个 | 状态 {dict(status)}")
        if dupes:
            sample = list(dupes.items())[:5]
            print(f"       重复示例: {sample}")


def audit_cards() -> None:
    print("\n【3】语料卡：可追溯性 / 长度 / 标记 / 重复")
    all_fingerprints: dict[str, list[str]] = {}
    for name in (
        list(SOURCES.values())
        + [(None, legacy) for legacy in LEGACY_CARDS]
        + [(None, extra) for extra in EXTRA_CARDS]
    ):
        folder, card_name = name
        path = _cards_dir() / f"{card_name}.jsonl"
        cards = _load_cards(path)
        if not cards:
            print(f"  {card_name:18s} 无卡片")
            continue
        untraceable = [c for c in cards if not (str((c.get("citation") or {}).get("url")) and
                                                str((c.get("citation") or {}).get("accessed")))]
        leaked = [c for c in cards if MARKUP_RE.search(json.dumps(c, ensure_ascii=False))]
        lengths = sorted(len(str(c.get("text") or "")) for c in cards)
        empty = [c for c in cards if not str(c.get("text") or "").strip()]
        traditional = [c for c in cards if fold(str(c.get("text") or "")) != str(c.get("text") or "")]
        for card in cards:
            fingerprint = hashlib.blake2b(str(card.get("text") or "")[:120].encode("utf-8"),
                                          digest_size=8).hexdigest()
            all_fingerprints.setdefault(fingerprint, []).append(card_name)
        zero_cards = sum(1 for c in cards if len(str(c.get("text") or "")) == 0)
        print(f"  {card_name:18s} {len(cards):>4} 张 | 不可追溯 {len(untraceable)}"
              f" | 残留标记 {len(leaked)} | 残留繁体 {len(traditional)} | 空正文 {len(empty) + zero_cards}"
              f" | 长度 min/中位/max = {lengths[0]}/{lengths[len(lengths)//2]}/{lengths[-1]}")
        if untraceable:
            print(f"       不可追溯示例: {[c.get('title') for c in untraceable[:5]]}")
        if leaked:
            print(f"       残留标记示例: {[c.get('title') for c in leaked[:5]]}")

    print("\n【4】跨文件重复正文（同一段证据出现在多个来源文件）")
    dupes = {fp: names for fp, names in all_fingerprints.items() if len(set(names)) > 1}
    print(f"  重复指纹 {len(dupes)} 个")
    for fp, names in list(dupes.items())[:5]:
        print(f"       {sorted(set(names))}")


def audit_zero_card_docs() -> None:
    print("\n【5】收进来但 0 卡的文档（白占位置，不贡献证据）")
    for name, (folder, card_name) in SOURCES.items():
        if not card_name:
            continue
        index_path = _raw(folder) / "_index.jsonl"
        cards_path = _cards_dir() / f"{card_name}.jsonl"
        if not index_path.exists() or not cards_path.exists():
            print(f"  {name:10s} 缺少索引或卡片文件，跳过")
            continue
        # 从**卡片**反推「哪些标题真的产出了证据」，而不是读索引里的 `cards` 字段：
        # 百科的索引行根本没有这个字段，写成 `or 0` 会把全部 kept 误判成 0 卡
        #（初版就踩了这个坑，报出 260 篇 0 卡，实际一篇都不缺）。
        # 比对前必须 fold：索引记的是**源站标题**（可能是繁体，因为它记录的是
        # "我们向对方要了什么"），而文档与卡片已经转简体 —— 不折叠会把
        # 「三星堆博物館」「魚鳧」这类已转换的条目误判成 0 卡。
        produced: set[str] = set()
        for card in _load_cards(cards_path):
            produced.add(fold(str(card.get("title") or "").split("·")[0]))
            produced.add(fold(str((card.get("citation") or {}).get("title") or "")))
        zero: list[str] = []
        seen: set[str] = set()
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") != "kept":
                continue
            title = str(row.get("title") or "")
            if title in seen:
                continue
            seen.add(title)
            if fold(title) not in produced:
                zero.append(f"{title}({row.get('chars')}字)")
        print(f"  {name:10s} {len(zero)} 篇" + (f": {', '.join(zero[:8])}" if zero else ""))


def main() -> int:
    print("=" * 78)
    print("原始数据体检")
    print("=" * 78)
    audit_docs()
    audit_index()
    audit_zero_card_docs()
    audit_cards()
    print("\n" + "=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
