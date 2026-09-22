"""PDF 切分：抽取文字 -> 按版面块还原阅读顺序 -> 去页眉页脚 -> 切分 -> 页码进 locator。

为什么不能"一页一块"
--------------------
探测发现每份 PDF 虽然只有 1 页，但字数高达 2427~7909 ——
而一个报纸版面装着**多篇互不相关的文章**（实测《学习时报》那页开头是思政课内容，
三星堆报道在版面另一处）。整页当一块会让"思政课"和"三星堆"混进同一条证据，
检索时必然互相干扰。所以必须按版面块拆开再切。

三个 PDF 特有的问题（都在这里处理）
----------------------------------
1. **硬换行断词**：报纸 PDF 按行切，词被拆断（"年代问题一 / 直是学界…"）。
   所以按**版面块**取原文，块内把行直接拼接（中文不需要空格）。
2. **多栏阅读顺序**：用块坐标先分栏、栏内按 y 排序，避免"横着读"串栏。
3. **页眉页脚/广告噪声**：`责编…美编…校对…`、`广告联系电话`、`刊号`、`定价`
   这类每页重复的样板文字，必须在**嵌入之前**去掉，而不是之后。

相关性闸门
----------
和维基采集同一套纪律：整份 PDF 必须提到 三星堆/古蜀/金沙，否则整份不入库 ——
否则会把一个跟三星堆无关的报纸版面整页收进来。

用法：python scripts/build_pdf_chunks.py [--apply]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.text import fold  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

PDF_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "museum"
MANIFEST = Path(__file__).resolve().parents[2] / "data" / "raw" / "manifest.csv"
OUT = Path(__file__).resolve().parents[1] / "data" / "corpus" / "v2" / "pdf_crawl.jsonl"

# 报纸名（文件名前缀 -> 来源标注）
NEWSPAPER = {
    "people_daily": "人民日报海外版",
    "cjn": "长江日报",
    "study_times": "学习时报",
    "tyrbw": "太原日报",
}

# 每页重复的样板文字：命中的块直接丢弃
BOILERPLATE = (
    "责编", "美编", "校对", "版面编辑", "值班编委", "广告", "广告联系电话",
    "办公室", "发行部", "传真", "零售", "定价", "刊号", "邮发代号",
    "订阅", "本报地址", "印刷", "印厂",
)
TOPIC_MARKERS = ("三星堆", "古蜀", "金沙")

MIN_LEN = 150
MAX_LEN = 500


def _load_manifest() -> dict[str, dict[str, str]]:
    """从 manifest.csv 取回每份 PDF 的来源 URL 与抓取日期（可追溯性靠它）。"""
    meta: dict[str, dict[str, str]] = {}
    if not MANIFEST.exists():
        return meta
    with MANIFEST.open("r", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            name = str(row.get("name") or "").strip()
            if name:
                meta[name] = {
                    "url": str(row.get("url") or "").strip(),
                    "accessed": str(row.get("fetched") or "").strip(),
                }
    return meta


def _is_boilerplate(text: str) -> bool:
    stripped = text.strip()
    # 样板文字都是短行：只丢短块，避免误伤正文
    if len(stripped) > 40:
        return False
    return any(mark in stripped for mark in BOILERPLATE)


def _blocks_in_reading_order(page) -> list[str]:
    """按版面块还原阅读顺序：先分栏（按 x 坐标），栏内按 y 排序。

    不这么做的话，报纸多栏会被"横着读"，句子串到另一栏去。
    """
    # get_text("blocks") 返回 7 元组: (x0, y0, x1, y1, text, block_no, block_type)
    # block_type 0 = 文本、1 = 图片 —— 下标是 **6**，写成 5（块编号）会只剩第一个块。
    blocks = [
        b for b in page.get_text("blocks")
        if len(b) >= 7 and int(b[6]) == 0
    ]
    # 按 x0 分栏（同一栏的 x0 相近），栏号用 x0//150 粗分
    def sort_key(block):
        x0, y0 = block[0], block[1]
        return (int(x0 // 150), y0)

    blocks.sort(key=sort_key)
    return [str(b[4]).strip() for b in blocks]


def _join_lines(block_text: str) -> str:
    """块内把硬换行拼回去。中文换行不加空格；英文/数字之间补一个空格。"""
    lines = [line.strip() for line in block_text.splitlines() if line.strip()]
    out = ""
    for line in lines:
        if not out:
            out = line
        elif line[0].isascii() and out[-1].isascii():
            out += " " + line
        else:
            out += line
    return out.strip()


def build(path: Path, meta: dict[str, str]) -> tuple[list[dict], str]:
    cards: list[dict] = []
    title = path.stem
    paper = next((v for k, v in NEWSPAPER.items() if title.startswith(k)), "报纸")
    info = meta.get(path.name, {})
    url = info.get("url", "")
    accessed = info.get("accessed") or date.today().isoformat()

    full_text: list[str] = []
    with pymupdf.open(path) as doc:
        for page_no, page in enumerate(doc, start=1):
            for block in _blocks_in_reading_order(page):
                if _is_boilerplate(block):
                    continue
                joined = fold(_join_lines(block))
                if joined:
                    full_text.append((page_no, joined))

    whole = "\n".join(text for _, text in full_text)
    if not any(marker in whole for marker in TOPIC_MARKERS):
        return [], f"与三星堆无关，整份不入库（含字 {len(whole)}）"

    # 按段落累积成块：短块并入前一块，超长按句切（与文字管线同一套规则）
    buffer_page: int | None = None
    buffer = ""
    seq = 0

    dropped = 0

    def flush() -> None:
        nonlocal buffer, buffer_page, seq, dropped
        text = buffer.strip()
        # **逐块相关性闸门**（报纸版面装多篇不相关文章，整份通过不等于每段都相关）：
        # 实测《学习时报》那页开头整段是思政课内容，与三星堆无关，必须单独丢掉。
        if len(text) >= MIN_LEN and not any(m in text for m in TOPIC_MARKERS):
            dropped += 1
            buffer = ""
            buffer_page = None
            return
        if len(text) >= MIN_LEN:
            seq += 1
            cards.append({
                "doc_id": f"news-{_slug(title)}-{seq:02d}",
                "title": f"{paper}·{title}（第{buffer_page}页·第{seq}段）",
                "text": text,
                "quote": text,
                "object": "", "era": "", "category": "新闻报道", "tags": ["pdf"],
                "source_type": "news",
                "license": "quote_only",
                "note": "由 PDF 版面块抽取并拼接硬换行；已去除页眉页脚/广告等样板文字",
                "citation": {
                    "title": f"{paper} {title}",
                    "author": paper,
                    "publisher": paper,
                    "year": "",
                    "locator": f"第{buffer_page}页",
                    "url": url,
                    "accessed": accessed,
                },
            })
        buffer = ""
        buffer_page = None

    import hashlib

    def _slug(text: str) -> str:
        return hashlib.blake2b(text.encode("utf-8"), digest_size=4).hexdigest()

    for page_no, paragraph in full_text:
        if buffer and len(buffer) + len(paragraph) > MAX_LEN:
            flush()
        if not buffer:
            buffer_page = page_no
        buffer = f"{buffer}\n{paragraph}".strip() if buffer else paragraph

    flush()

    # 超长块再按句切一次（防止单块超过 MAX_LEN 太多）
    split: list[dict] = []
    for card in cards:
        text = str(card["text"])
        if len(text) <= MAX_LEN:
            split.append(card)
            continue
        chunk = ""
        for sentence in _sentences(text):
            if len(chunk) + len(sentence) > MAX_LEN and len(chunk) >= MIN_LEN:
                new_card = dict(card)
                new_card["text"] = chunk.strip()
                new_card["quote"] = chunk.strip()
                split.append(new_card)
                chunk = sentence
            else:
                chunk += sentence
        if len(chunk.strip()) >= MIN_LEN:
            new_card = dict(card)
            new_card["text"] = chunk.strip()
            new_card["quote"] = chunk.strip()
            split.append(new_card)
    for index, card in enumerate(split, start=1):
        card["doc_id"] = f"news-{_slug(title)}-{index:02d}"

    note = f"{len(full_text)} 个版面块"
    if dropped:
        note += f"（逐块闸门丢弃 {dropped} 段不相关内容）"
    return split, note


def _sentences(text: str) -> list[str]:
    import re

    parts = re.split(r"(?<=[。！？；])", text)
    return [part for part in parts if part]


def main() -> int:
    parser = argparse.ArgumentParser(description="切分 PDF 为检索单元")
    parser.add_argument("--apply", action="store_true", help="真正写入；不加则只预演")
    args = parser.parse_args()

    meta = _load_manifest()
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    print("=" * 72)
    print(f"PDF 切分（共 {len(pdfs)} 份，min={MIN_LEN}, max={MAX_LEN}）")
    print("=" * 72)

    all_cards: list[dict] = []
    for path in pdfs:
        try:
            cards, note = build(path, meta)
        except Exception as exc:  # noqa: BLE001
            print(f"  {path.name}: 处理失败 {type(exc).__name__}: {exc}")
            continue
        print(f"  {path.name}: {note} -> {len(cards)} 张卡")
        all_cards.extend(cards)

    print("=" * 72)
    print(f"合计 {len(all_cards)} 张卡" + (" | 已写入" if args.apply else " | 预演（加 --apply）"))
    if args.apply and all_cards:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"// 由 scripts/build_pdf_chunks.py 从 PDF 切分，请勿手工编辑。\n"
            f"// 切分日期 {date.today().isoformat()}；来源与授权见每条的 citation / license。\n"
            "// 报纸内容为受版权保护的新闻报道，license=quote_only（仅作背景/线索）。\n"
        )
        with OUT.open("w", encoding="utf-8") as handle:
            handle.write(header)
            for card in all_cards:
                handle.write(json.dumps(card, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
