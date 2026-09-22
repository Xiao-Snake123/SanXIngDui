"""切分（chunking）步骤：把原始文档切成检索单元，产出 v2 语料卡。

为什么独立成一步
----------------
采集脚本把「抓取」和「切分」绑在一起了：想改切分参数就得重新向维基发一遍请求
（对方是公益站点，为调参数反复打扰不合适）。但**全文已经存进 data/raw** 了 ——
切分完全可以从本地原文重跑。拆开之后：
  - 调参数不再打扰对方站点；
  - 同一份原文可以反复试不同参数，这才谈得上"用评测驱动调参"。

切法（沿用 collect_corpus.py 的既有实现，不另起一套规则）
--------------------------------------------------------
1. 按 `== 章节 ==` 切：这是 MediaWiki 自己标的边界，比任何启发式都可靠；
   章节名直接进 `citation.locator`，让引用能定位到具体章节。
2. 段落整形：短于 `min_len` 的并入前一段，长于 `max_len` 的按 `。！？；` 切断。
3. 丢掉"外部链接/参考资料/注释"这类索引章节（否则"页面存档备份，存于
   互联网档案馆"会进 BM25 词元）。

**百科摘要不参与切分**：一条摘要本身就是一个完整检索单元（中位数约 320 字），
切碎反而破坏语义 —— 直接 1 文档 = 1 卡。

参数为什么是 150/500
--------------------
对照 2026 年主流实践：事实型查询建议 256~512 token，起步值 400~512。
中文 1 字≈1 token，原采集用的 `min_len=80` 会产出 83 字的信息量过低的小块，
把 top_n 名额浪费掉。提到 150 后块更"能回答一个问题"，又不至于超长
（超过 500 字会让引用无法定位到具体主张，所以 max_len 保持 500）。

用法
----
    python scripts/build_chunks.py --apply                       # 默认 150/500
    python scripts/build_chunks.py --min-len 150 --max-len 500 --apply
    python scripts/build_chunks.py --source wikipedia --apply    # 只切某个来源
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.text import fold  # noqa: E402
from collect_corpus import (  # noqa: E402  (复用同一套切分规则，避免两份实现漂移)
    _split_paragraphs,
    _split_sections,
    _stable_slug,
    _WIKI_SKIP_SECTIONS,
)

try:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

# 百科 frontmatter 里的基本信息字段 -> 语料元数据（与 crawl_sanxingdui._baike_meta 一致）
_BAIKE_META_KEYS = ("中文名", "馆藏地点", "所属年代", "类别", "出土地点", "出土年代", "文物级别", "保护级别")
_BAIKE_TAG_KEYS = ("出土地点", "出土年代", "文物级别", "馆藏地点", "保护级别", "中文名")
BAIKE_NOTE = "百度百科词条摘要（受版权约束，仅保留摘要与链接，未存全文）"

SOURCES: dict[str, dict[str, str]] = {
    "wikipedia": {
        "dir": "wikipedia",
        "cards": "wikipedia_crawl",
        "source_type": "encyclopedia",
        "license": "cc_by_sa",
        "author": "维基百科编者",
        "publisher": "维基百科（CC BY-SA 4.0）",
    },
    "baike": {
        "dir": "baike",
        "cards": "baike_crawl",
        "source_type": "encyclopedia",
        "license": "quote_only",
        "author": "百度百科编者",
        "publisher": "百度百科（仅摘要引用）",
    },
}


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


def _baike_metadata(fields: dict[str, str]) -> dict[str, Any]:
    tags = [f"{key}：{fields[key]}" for key in _BAIKE_TAG_KEYS if fields.get(key)]
    return {
        "object": fields.get("中文名", ""),
        "era": next((fields[key] for key in ("所属年代", "年代", "时期", "时代") if fields.get(key)), ""),
        "category": fields.get("类别") or "文物",
        "tags": tags,
    }


def build_source(
    name: str,
    cfg: dict[str, str],
    *,
    min_len: int,
    max_len: int,
    fallback_min: int,
    lead_min: int,
    section_min: int,
    apply: bool,
) -> dict[str, int]:
    directory = _raw(cfg["dir"])
    if not directory.exists():
        print(f"  {name}: 目录不存在，跳过")
        return {"docs": 0, "cards": 0}

    cards: list[dict[str, Any]] = []
    zero_card: list[str] = []
    for path in sorted(directory.glob("*.md")):
        fields, body = _split_front(path.read_text(encoding="utf-8"))
        title = fields.get("title") or path.stem
        url = fields.get("url", "")
        accessed = fields.get("accessed", "")
        body = body.strip()

        if name == "baike":
            # 摘要本身就是一个完整检索单元，不切
            if not body:
                zero_card.append(title)
                continue
            meta = _baike_metadata(fields)
            cards.append({
                "doc_id": f"baike-{_stable_slug(title)}-01",
                "title": title,
                "text": body,
                "quote": body,
                "object": meta["object"],
                "era": meta["era"],
                "category": meta["category"],
                "tags": meta["tags"],
                "source_type": cfg["source_type"],
                "license": cfg["license"],
                "note": BAIKE_NOTE,
                "citation": {
                    "title": title, "author": cfg["author"], "publisher": cfg["publisher"],
                    "year": "", "locator": "", "url": url, "accessed": accessed,
                },
            })
            continue

        produced = 0
        for section_index, (section, section_body) in enumerate(_split_sections(body)):
            if section in _WIKI_SKIP_SECTIONS:
                continue
            # **导语永远保留**，不受 min_len 约束。
            # 实测《三星堆出土玉边璋》：导语 90 字，含"通长54.5厘米""一号祭祀坑出土"
            # 这类问答最常问的事实，按 min_len=130 被整段丢弃 —— 事后检索"54.5厘米"
            # 命中 0 条。百科条目的导语是价值密度最高的部分，短≠没用。
            # 所以导语单独用一个更低的阈值（lead_min）。
            is_lead = (not section) and section_index == 0
            effective_min = lead_min if is_lead else min_len
            paragraphs = list(_split_paragraphs(section_body, min_len=effective_min, max_len=max_len))
            # **短章节兜底**：整节不足 min_len 时，_split_paragraphs 会把它整节丢掉。
            # 实测后果（评测集校验直接抓到）：《古蜀·秦灭古蜀》的"粮仓"、
            # 《古蜀·三星堆文化》的"鱼凫朝"、《金沙遗址·玉器》的"22厘米"——
            # 三处原文都有，卡片里 0 命中。短章节往往就是一个完整事实单元，
            # 和导语同理：短 ≠ 没用。所以整节保留为一块。
            if not paragraphs and len(" ".join(section_body.split())) >= section_min:
                paragraphs = [" ".join(section_body.split())]
            for paragraph in paragraphs:
                produced += 1
                locator = "导语" if is_lead else section
                cards.append({
                    "doc_id": f"wiki-{_stable_slug(title)}-{produced:02d}",
                    "title": title if is_lead else (f"{title}·{section}" if section else title),
                    "text": paragraph,
                    "quote": paragraph,
                    "object": "", "era": "", "category": "文化解读", "tags": [],
                    "source_type": cfg["source_type"],
                    "license": cfg["license"],
                    "citation": {
                        "title": title, "author": cfg["author"], "publisher": cfg["publisher"],
                        "year": "", "locator": locator, "url": url, "accessed": accessed,
                    },
                })
        if not produced:
            # 整篇都切不出 >= min_len 的一段 —— 这**不等于没有内容**，只是条目短。
            # 实测 min_len=150 时有 14 篇因此归零，其中包含 493 字的《宝墩文化》：
            # 直接丢弃等于把有实质内容的条目整篇作废。
            # min_len 的用途是"合并碎片"，不是"删掉短文档"，所以这里兜底：
            # 整篇作为一张卡保留。只有正文短到实在无信息（< fallback_min）才放弃。
            if len(body) >= fallback_min:
                produced += 1
                cards.append({
                    "doc_id": f"wiki-{_stable_slug(title)}-01",
                    "title": title,
                    "text": body,
                    "quote": body,
                    "object": "", "era": "", "category": "文化解读", "tags": [],
                    "source_type": cfg["source_type"],
                    "license": cfg["license"],
                    "citation": {
                        "title": title, "author": cfg["author"], "publisher": cfg["publisher"],
                        "year": "", "locator": "", "url": url, "accessed": accessed,
                    },
                })
            else:
                zero_card.append(f"{title}({len(body)}字)")

    lengths = sorted(len(str(card["text"])) for card in cards)
    print(f"  {name}: {len(list(directory.glob('*.md')))} 篇 -> {len(cards)} 张卡"
          + (f" | 长度 min/中位/max = {lengths[0]}/{lengths[len(lengths)//2]}/{lengths[-1]}" if lengths else ""))
    if zero_card:
        print(f"      切不出 ≥{min_len} 字段落的文档 {len(zero_card)} 篇: {', '.join(zero_card[:6])}")

    if apply and cards:
        path = _cards_dir() / f"{cfg['cards']}.jsonl"
        header = (
            f"// 由 scripts/build_chunks.py 切分（min_len={min_len}, max_len={max_len}），请勿手工编辑。\n"
            f"// 切分日期 {date.today().isoformat()}；来源与授权见每条的 citation / license 字段。\n"
            "// 维基百科内容依 CC BY-SA 4.0 使用，展示时须保留署名与许可链接。\n"
        )
        with path.open("w", encoding="utf-8") as handle:
            handle.write(header)
            for card in cards:
                handle.write(json.dumps(card, ensure_ascii=False) + "\n")
    return {"docs": len(list(directory.glob("*.md"))), "cards": len(cards)}


def main() -> int:
    parser = argparse.ArgumentParser(description="把原始文档切成检索单元（v2 语料卡）")
    parser.add_argument("--source", help="只切某个来源（wikipedia / baike）")
    parser.add_argument("--min-len", type=int, default=150, help="最小块长度（默认 150 字）")
    parser.add_argument("--max-len", type=int, default=500, help="最大块长度（默认 500 字）")
    parser.add_argument("--fallback-min", type=int, default=80,
                        help="整篇兜底成卡所需的最小正文长度（默认 80 字）")
    parser.add_argument("--lead-min", type=int, default=40,
                        help="导语的最小长度（默认 40 字，独立于 min_len）")
    parser.add_argument("--section-min", type=int, default=60,
                        help="短章节整节保留所需的最小长度（默认 60 字）")
    parser.add_argument("--apply", action="store_true", help="真正写入；不加则只预演")
    args = parser.parse_args()

    targets = {args.source: SOURCES[args.source]} if args.source else SOURCES
    print("=" * 74)
    print(f"切分 chunk（min_len={args.min_len}, max_len={args.max_len}, "
          f"fallback={args.fallback_min}, lead={args.lead_min}, section={args.section_min}）")
    print("=" * 74)
    total = Counter()
    for name, cfg in targets.items():
        total.update(
            build_source(
                name, cfg,
                min_len=args.min_len, max_len=args.max_len,
                fallback_min=args.fallback_min, lead_min=args.lead_min,
                section_min=args.section_min, apply=args.apply,
            )
        )
    print("=" * 74)
    print(f"合计 {total['docs']} 篇 -> {total['cards']} 张卡"
          + (" | 已写入" if args.apply else " | 预演（加 --apply 生效）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
