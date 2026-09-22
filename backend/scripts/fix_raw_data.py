"""数据体检之后的修复：把 audit_raw.py 查出的问题一次性清掉。

对应关系（每条都对应体检里的一条发现）：
  A. `baike/_index.jsonl` 重复堆积   —— 348 行 -> 唯一 requested
  B. en / ja 移出知识库             —— 政策：只保留简体中文，不要外语
  C. `wikipedia.jsonl` 与新采集重叠   —— 跨文件去重合并，旧文件退役
  D. `wikisource` 繁体转简体         —— 政策：不要繁体

关于 D 需要说明一句：项目原本**刻意**不改古籍字形（`app/rag/text.py` 第 22~26 行：
「古籍的字形本身就是史料信息」）。这条政策是使用者明确要求的，脚本照做，
但会把「已由繁体转为简体、与维基文库原文字形不一致」写进卡片的 `note` ——
转换可以，但不能让后来读语料的人以为这就是文库原文。

用法：python scripts/fix_raw_data.py [--apply]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.text import fold  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

SIMPLIFY_NOTE = "本卡片正文已由繁体转为简体（与维基文库原文字形不一致，回查以 url 为准）"


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _raw(name: str) -> Path:
    return _root() / "data" / "raw" / name


def _cards_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "corpus" / "v2"


def _load_cards(path: Path) -> list[dict]:
    cards: list[dict] = []
    if not path.exists():
        return cards
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        try:
            cards.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    return cards


def _write_cards(path: Path, cards: list[dict], header: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(header)
        for card in cards:
            handle.write(json.dumps(card, ensure_ascii=False) + "\n")


def fix_baike_index(*, apply: bool) -> None:
    """A. 索引去重：`_append_index` 是追加写，重跑会累积，唯一化后重写。"""
    path = _raw("baike") / "_index.jsonl"
    if not path.exists():
        print("  A. baike 索引不存在，跳过")
        return
    rows: dict[str, dict] = {}
    order: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = f"{row.get('requested')}|{row.get('status')}"
        if key not in rows:
            order.append(key)
        rows[key] = row  # 后写的覆盖先写的：保留最后一次结果
    unique = [rows[key] for key in order]
    print(f"  A. baike 索引 {len(rows)} 条唯一（原文件多行重复）；写回后行数 {len(unique)}")
    if apply:
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in unique) + "\n",
            encoding="utf-8",
        )


def fix_foreign(*, apply: bool) -> None:
    """B. 外语移出知识库（政策：只要简体中文）。删掉即可 —— 采集脚本可随时重跑取回。"""
    targets = [_raw("en"), _raw("ja")]
    card_targets = [_cards_dir() / "en_crawl.jsonl", _cards_dir() / "ja_crawl.jsonl"]
    removed: list[str] = []
    for path in targets:
        if path.exists():
            docs = len(list(path.glob("*.md")))
            removed.append(f"{path.name}/ ({docs} 篇)")
            if apply:
                shutil.rmtree(path)
    for path in card_targets:
        if path.exists():
            removed.append(path.name)
            if apply:
                path.unlink()
    print(f"  B. 外语移除: {removed if removed else '（无）'}")


def fix_wikipedia_merge(*, apply: bool) -> None:
    """C. 跨文件去重：`wikipedia.jsonl` 与新采集有 50 段重复。

    不直接删旧文件 —— 它含有新采集没有的少量卡片（早期清单里的条目）。
    正确做法是**合并后按正文去重**，再让旧文件退役到 `deprecated/`。
    """
    legacy = _cards_dir() / "wikipedia.jsonl"
    current = _cards_dir() / "wikipedia_crawl.jsonl"
    if not legacy.exists():
        print("  C. wikipedia.jsonl 不存在，跳过")
        return
    merged: list[dict] = []
    seen: set[str] = set()
    for source, cards in (("crawl", _load_cards(current)), ("legacy", _load_cards(legacy))):
        for card in cards:
            fingerprint = str(card.get("text") or "")[:120]
            if not fingerprint or fingerprint in seen:
                continue
            seen.add(fingerprint)
            merged.append(card)
    # doc_id 可能撞车（同一页面两批采集用的是同一套 slug 规则）：
    # 撞车的旧条目加后缀，否则装载时会被 `load_corpus` 静默覆盖。
    taken: set[str] = set()
    for card in merged:
        doc_id = str(card.get("doc_id") or "")
        if doc_id in taken:
            card["doc_id"] = f"{doc_id}-legacy"
        taken.add(str(card.get("doc_id") or ""))
    legacy_count = len(_load_cards(legacy))
    before = len(_load_cards(current))
    print(f"  C. 维基卡片合并: crawl {before} + legacy {legacy_count} -> 去重后 {len(merged)}"
          f"（去掉 {before + legacy_count - len(merged)} 段重复）")
    if apply:
        header = (
            "// 由 scripts/crawl_sanxingdui.py 采集 + scripts/fix_raw_data.py 跨文件去重，请勿手工编辑。\n"
            f"// 合并日期 {date.today().isoformat()}；来源与授权见每条的 citation / license 字段。\n"
            "// 维基百科内容依 CC BY-SA 4.0 使用，展示时须保留署名与许可链接。\n"
        )
        _write_cards(current, merged, header)
        deprecated = _cards_dir() / "deprecated"
        deprecated.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(deprecated / "wikipedia.jsonl"))


def fix_wikisource_simplify(*, apply: bool) -> None:
    """D. 古籍卡片繁体转简体（政策：不要繁体），并记录该转换。

    前提：先跑 `python scripts/collect_corpus.py wikisource` 重新生成
    —— 盘上那份是旧版脚本产物（93/97 卡含校勘注、0 张有 note）。
    """
    path = _cards_dir() / "wikisource.jsonl"
    cards = _load_cards(path)
    if not cards:
        print("  D. wikisource 无卡片，跳过")
        return
    traditional = sum(1 for c in cards if fold(str(c.get("text") or "")) != str(c.get("text") or ""))
    changed = 0
    for card in cards:
        for key in ("title", "text", "quote"):
            if isinstance(card.get(key), str):
                card[key] = fold(card[key])
        citation = card.get("citation")
        if isinstance(citation, dict):
            for key in ("title", "locator", "publisher"):
                if isinstance(citation.get(key), str):
                    citation[key] = fold(citation[key])
        if SIMPLIFY_NOTE not in str(card.get("note") or ""):
            note = str(card.get("note") or "").strip()
            card["note"] = f"{note}；{SIMPLIFY_NOTE}" if note else SIMPLIFY_NOTE
            changed += 1
    print(f"  D. wikisource 繁体转简体: {len(cards)} 张卡，其中 {traditional} 张原含繁体；"
          f"{changed} 张补写转换说明")
    if apply:
        header = (
            "// 由 scripts/collect_corpus.py 采集 + scripts/fix_raw_data.py 转简体，请勿手工编辑。\n"
            f"// 转换日期 {date.today().isoformat()}；古籍原文以 citation.url 为准。\n"
        )
        _write_cards(path, cards, header)


def main() -> int:
    parser = argparse.ArgumentParser(description="数据体检后的修复")
    parser.add_argument("--apply", action="store_true", help="真正写入；不加则只预演")
    args = parser.parse_args()
    print("=" * 72)
    print("数据修复（A 索引去重 / B 外语移除 / C 维基合并去重 / D 古籍转简）")
    print("=" * 72)
    fix_baike_index(apply=args.apply)
    fix_foreign(apply=args.apply)
    fix_wikipedia_merge(apply=args.apply)
    fix_wikisource_simplify(apply=args.apply)
    print("=" * 72)
    print("已写入" if args.apply else "预演（未写入，加 --apply 生效）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
