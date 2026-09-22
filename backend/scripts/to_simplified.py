"""把采集到的文档与语料统一转为简体中文（繁→简）。

背景
----
采集到的维基条目繁简混排：`三星堆博物館`、`魚鳧`、`蠶叢`、`青銅神樹`……
而 `fold()` 只作用于**检索层**（`searchable_text`），刻意不改数据 ——
所以 `text` 字段（会被注入 prompt、也可能直接引用给用户）仍是繁体。
本脚本把**文档正文与语料正文**统一成简体。

三条纪律
--------
1. **不动古籍原文**（`wikisource.jsonl`）。项目已明确「古籍字形本身就是史料信息，
   采集时转成简体等于篡改原文、破坏引用与原文一致」。史料原文与采集文档是两回事。
2. **不动日文**（`data/raw/ja`）。日文是汉字+假名混排，`zhconv` 只换汉字不换假名，
   结果既不是日文也不是中文。日文条目保留原样。
3. **转换留痕**。在 frontmatter 写入 `converted:` 标记 —— 否则「这份原始文档
   到底是原文还是转换过的」查不清，这对强调可追溯的语料是硬伤。

幂等：繁→简是多对一、无损，已简体的内容再转不会有变化，可反复执行。

用法
----
    python scripts/to_simplified.py            # 预演：只统计，不写
    python scripts/to_simplified.py --apply    # 真正写入
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.text import fold  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

# 见 docstring 纪律 1、2：不含 wikisource / ja
DOC_DIRS: tuple[str, ...] = ("wikipedia", "baike", "en")
CARD_FILES: tuple[str, ...] = ("wikipedia_crawl", "baike_crawl", "en_crawl", "wikipedia")
MARKER = "zh-cn（繁→简，由 scripts/to_simplified.py 转换）"


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "raw"


def _corpus_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "corpus" / "v2"


def _split_front(text: str) -> tuple[list[str], str]:
    """拆出 frontmatter 行与正文。没有 frontmatter 时返回 ([], 原文)。"""
    if not text.startswith("---\n"):
        return [], text
    end = text.find("\n---\n", 4)
    if end == -1:
        return [], text
    head = text[4:end].splitlines()
    body = text[end + 5 :]
    return head, body


def convert_docs(*, apply: bool) -> dict[str, int]:
    stats = {"files": 0, "changed": 0, "files_written": 0}
    for name in DOC_DIRS:
        directory = _data_dir() / name
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            original = path.read_text(encoding="utf-8")
            head, body = _split_front(original)
            new_head = [fold(line) for line in head]
            if not any(line.startswith("converted:") for line in new_head):
                new_head.append(f"converted: {MARKER}")
            rebuilt = "---\n" + "\n".join(new_head) + "\n---\n" + fold(body)
            stats["files"] += 1
            # 「含繁体」只看**正文本身**是否被改动：加 `converted` 标记也会让
            # `rebuilt != original` 成立，用它当判据会把全简体文件也统计进来。
            if fold(body) != body or fold("\n".join(head)) != "\n".join(head):
                stats["changed"] += 1
                if apply:
                    path.write_text(rebuilt, encoding="utf-8")
                    stats["files_written"] += 1
    return stats


def convert_cards(*, apply: bool) -> dict[str, int]:
    stats = {"cards": 0, "changed": 0, "files_written": 0}
    for name in CARD_FILES:
        path = _corpus_dir() / f"{name}.jsonl"
        if not path.exists():
            continue
        out: list[str] = []
        file_changed = False
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("//"):
                out.append(stripped)
                continue
            card = json.loads(stripped)
            stats["cards"] += 1
            before = json.dumps(card, ensure_ascii=False, sort_keys=True)
            for key in ("title", "text", "quote", "note"):
                if isinstance(card.get(key), str):
                    card[key] = fold(card[key])
            card["tags"] = [fold(str(tag)) for tag in (card.get("tags") or [])]
            citation = card.get("citation")
            if isinstance(citation, dict):
                for key in ("title", "locator", "publisher"):
                    if isinstance(citation.get(key), str):
                        citation[key] = fold(citation[key])
            after = json.dumps(card, ensure_ascii=False, sort_keys=True)
            if after != before:
                stats["changed"] += 1
                file_changed = True
            out.append(json.dumps(card, ensure_ascii=False))
        if apply and file_changed:
            path.write_text("\n".join(out) + "\n", encoding="utf-8")
            stats["files_written"] += 1
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="把文档与采集语料统一转为简体中文")
    parser.add_argument("--apply", action="store_true", help="真正写入；不加则只预演统计")
    args = parser.parse_args()

    docs = convert_docs(apply=args.apply)
    cards = convert_cards(apply=args.apply)
    mode = "已写入" if args.apply else "预演（未写入，加 --apply 生效）"
    print(f"[{mode}] 文档 : {docs['files']} 个，含繁体的 {docs['changed']} 个，实际更新 {docs['files_written']} 个")
    print(f"[{mode}] 语料卡: {cards['cards']} 张，含繁体的 {cards['changed']} 张，实际更新 {cards['files_written']} 个文件")
    print("未处理（按纪律保留原样）: wikisource.jsonl（古籍原文）、data/raw/ja（日文）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
