"""三星堆专题爬虫 —— 批量扩充原始文档库与 v2 语料。

与 `scripts/collect_corpus.py` 的分工
------------------------------------
`collect_corpus.py` 采集的是一份**人工挑选的短清单**（10 个条目）：每条都要
人工确认标题写法与相关性，所以规模上不去。本脚本负责**批量发现 + 批量采集**，
把「三星堆 / 古蜀 / 金沙」主题从个位数扩到 100+ 篇。

发现策略（两级，互补）
----------------------
1. **分类树 BFS**：从种子分类出发递归子分类（默认深度 2），取分类内条目。
   分类成员天然相关，不需要逐条判断相关性。
2. **关键词搜索**：用一批高特异度检索词（青铜神树、纵目面具、蚕丛……）拉搜索结果。
   它补齐的是分类树覆盖不到的东西 —— 例如「黄金面具」这类可能没被归类的条目。

采集纪律（与 collect_corpus.py 完全一致，不重复解释，只列条目）
---------------------------------------------------------------
- 只走公开 MediaWiki API（`action=query`），不做 HTML 抓取，不碰任何私有 XHR 接口；
- 串行 + 限速（沿用 `REQUEST_INTERVAL`）——对方是公益站点，不抢带宽；
- 每条都记 `url` + `accessed`（抓取日期）；
- 排版用的「索引类章节」由 `_wiki_cards` 统一跳过。

**保底过滤**：抓到全文后，正文必须提到「三星堆 / 古蜀 / 金沙遗址」之一才收录。
搜索是按标题匹配的，「商代青铜器」「玉璋」这类词会命中大量与三星堆无关的条目；
如果只靠标题相关性，语料会被泛青铜器条目稀释，检索精度反而下降。代价是
少数抓了却没用的请求 —— 相比把跑题内容灌进证据库，这个代价是值得的。

产物
----
- 原始文档：`data/raw/wikipedia/<标题>.md`（一篇一个文件，YAML 头带出处，供人工查阅）
- 语料卡：`backend/data/corpus/v2/wikipedia_crawl.jsonl`（v2 schema，可被 RAG 装载）
- 索引：`data/raw/wikipedia/_index.jsonl`（每篇一行，含收录/拒绝原因，可审计）

用法
----
    python scripts/crawl_sanxingdui.py probe               # 只发现，不抓取，看规模
    python scripts/crawl_sanxingdui.py crawl               # 发现 + 抓取 + 写出
    python scripts/crawl_sanxingdui.py crawl --target 120  # 目标篇数（默认 100）
    python scripts/crawl_sanxingdui.py crawl --max-fetch 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 控制台默认 GBK：条目标题含生僻字时，打印一个编不出的字就会让整个
# 采集任务崩掉（沿用 collect_corpus.py 的处理，理由见那边第 47 行注释）。
try:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

from collect_corpus import (  # noqa: E402  (同目录脚本，复用采集纪律与卡片构造)
    _client,
    _fetch_extracts,
    _resolve_proxy,
    _sleep,
    _stable_slug,
    _user_agent,
    _wiki_cards,
)
from app.core.logging import get_logger  # noqa: E402
from app.rag.text import fold  # noqa: E402  (繁简折叠：维基标题繁简混排)

logger = get_logger("scripts.crawl_sanxingdui")

HOST = "zh.wikipedia.org"

# ── 种子分类 ────────────────────────────────────────────────────────────────
# 需要**先用 probe 验证存在性**：维基分类是社区手工维护的，凭印象写的分类名
# 会静默返回 0 个成员 —— 看起来像「这个主题没条目」，实际只是分类名不存在。
SEED_CATEGORIES: list[str] = [
    "Category:三星堆",
    "Category:三星堆遗址",
    "Category:三星堆文物",
    "Category:三星堆博物馆",
    "Category:金沙遗址",
    "Category:古蜀",
    "Category:蜀國",
    "Category:巴蜀文化",
    "Category:四川考古遗址",
    "Category:蜀汉",  # 观察项：属「蜀」但时代不符，靠正文过滤兜住
]

# ── 搜索种子 ────────────────────────────────────────────────────────────────
# 选词标准：**高特异度**。搜「青铜器」会拉回整个商周青铜器世界，搜「青铜神树」
# 只可能命中三星堆。泛词故意不放进来，宁可少收也不稀释。
SEARCH_SEEDS: list[str] = [
    "三星堆",
    "三星堆遗址",
    "三星堆祭祀坑",
    "三星堆文物",
    "三星堆博物馆",
    "青铜神树",
    "青铜立人像",
    "青铜纵目面具",
    "青铜人头像",
    "青铜太阳轮",
    "青铜神坛",
    "黄金面具",
    "金杖",
    "金面罩",
    "玉璋",
    "玉琮",
    "玉璧",
    "陶盉",
    "象牙",
    "海贝",
    "蚕丛",
    "鱼凫",
    "柏灌",
    "杜宇",
    "开明",
    "鳖灵",
    "古蜀",
    "蜀王本纪",
    "金沙遗址",
    "太阳神鸟",
    "宝墩文化",
    "十二桥文化",
    "巴蜀文化",
    "广汉",
    "鸭子河",
    "月亮湾",
    "长江文明",
    "中华文明探源工程",
]

# 标题优先词：命中越早抓，越可能在有限次请求内凑够目标数。
# 用**排序**而不是过滤 —— 排序只是把把握不大的排后面，轮不到也不损失。
STRONG_TITLE_KEYWORDS: tuple[str, ...] = (
    "三星堆", "古蜀", "金沙", "蚕丛", "鱼凫", "柏灌", "杜宇", "开明", "鳖灵",
    "宝墩", "十二桥", "蜀王", "巴蜀", "广汉", "月亮湾", "鸭子河", "太阳神鸟",
    "青铜神树", "立人像", "纵目", "青铜面具", "黄金面具", "金杖", "金面罩",
    "太阳轮", "神坛", "人头像",
)

# ── 相关性判定（收录闸门）────────────────────────────────────────────────────
# 教训（实测）：初版只判「正文是否提到 三星堆/古蜀/金沙遗址」，结果
# `中国历史`（150 卡）、`2022年中国大陆`、`中华文化`、`2022年央視春晚` 这类
# **顺带提一句**的通用大盘全被收进来，语料被稀释成「什么都有一点、什么都不精」。
# 判据必须落在**标题**：标题讲的就是三星堆/古蜀，正文才可能是相关详细资料。
#
# 标题先 `fold()` 繁简折叠再匹配 —— 维基繁简混排（`魚鳧`/`鱼凫`、`青銅`/`青铜`），
# 不折叠会漏掉一半条目。
SUBJECT_KEYWORDS: tuple[str, ...] = (
    "三星堆", "古蜀", "金沙遗址", "蚕丛", "鱼凫", "柏灌", "杜宇", "鳖灵",
    "开明", "宝墩", "十二桥", "蜀王", "巴蜀", "蜀", "太阳神鸟", "月亮湾", "鸭子河",
)
ARTIFACT_KEYWORDS: tuple[str, ...] = (
    "青铜神树", "青铜立人", "青铜纵目", "青铜大面具", "青铜大鸟头", "青铜太阳轮",
    "青铜神坛", "青铜人头像", "青铜人身形器", "青铜扭头跪坐人像", "铜小立人",
    "金杖", "金面罩", "黄金面罩", "黄金面具", "面具", "神树", "立人", "纵目",
    "太阳轮", "神坛", "人头像", "跪坐人像", "人身形器", "大鸟头", "鸟形饰",
    "玉边璋", "玉璋", "玉琮", "玉璧", "陶盉", "象牙",
)
# 硬噪声：标题命中即丢弃。这些页面即使正文提到三星堆，主题也不是三星堆。
NOISE_SUBSTRINGS: tuple[str, ...] = (
    "站", "街道", "镇", "列表", "消歧义", "硬币", "共和国", "台风", "肺炎",
    "冠状病毒", "疫情", "综合征", "动画", "漫画", "游戏", "小说", "电视剧",
    "电影", "传奇", "蜀道", "蜀锦", "蜀绣", "蜀汉",
)
_YEAR_RE = re.compile(r"\d{4}\s*年")
MAX_CARDS_PER_PAGE = 20


def _is_relevant(title: str, text: str) -> bool:
    """收录闸门：标题命中三星堆/古蜀主题词，**且**正文确实在讲三星堆/古蜀。

    为什么要**正文二次确认**：主题词里有大量常见人名/地名（杜宇、开明、十二桥），
    同名页面会误收 —— 实测「杜宇 (射击运动员)」「开明书店」「沈开明」
    「成都十二桥惨案（1949 惨案）」「太阳神鸟属（恐龙）」全都因标题含关键词混了进来。
    要求正文提到 三星堆/古蜀/金沙遗址 就能挡掉这些同名异物；而真正的相关条目
    （文物、古蜀君主、遗址）正文必然提到它们。
    """
    folded = fold(title)
    if _YEAR_RE.search(folded) or any(noise in folded for noise in NOISE_SUBSTRINGS):
        return False
    if not (
        any(keyword in folded for keyword in SUBJECT_KEYWORDS)
        or any(keyword in folded for keyword in ARTIFACT_KEYWORDS)
    ):
        return False
    return any(marker in text for marker in ("三星堆", "古蜀", "金沙遗址"))

# 明显跑题的分类（时代不符）：即使出现在分类树里也不进候选。
# 「蜀汉」「刘备」这类词在分类名里出现时，主题是三国而非三星堆。
EXCLUDE_CATEGORY_MARKERS: tuple[str, ...] = ("蜀汉", "三國", "三国", "刘备", "諸葛")

def _priority(title: str, reasons: set[str]) -> int:
    """抓取优先级：数字越小越先抓。

    为什么需要：probe 实测候选 1386 篇，其中大量是搜索词按**标题**命中的泛条目
    （搜「象牙」会拉回「象牙」「象牙贸易」「象牙海岸」）。而抓取是串行限速的，
    不可能全抓。按档位排序后，前几档就能凑够目标数，泛条目排在后面自然轮不到。
    """
    if "三星堆" in title:
        return 0
    if any(not reason.startswith("search:") for reason in reasons):
        return 1  # 分类成员：分类树是人工维护的，成员天然相关
    if any(keyword in title for keyword in STRONG_TITLE_KEYWORDS):
        return 2
    return 3


_WINDOWS_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(title: str) -> str:
    """把条目标题变成 Windows 可用的文件名。

    用可读的中文标题而不是 `_stable_slug` 的哈希：原始文档库是给人翻的，
    一个叫 `3f2a1b9c.md` 的文件等于没有文件名。哈希只用在 doc_id（要稳定），
    文件名要求可读，两者目的不同，不必统一。
    """
    name = _WINDOWS_BAD.sub("_", title).strip().rstrip(".")
    return (name or _stable_slug(title))[:120]


# ── MediaWiki API：发现 ─────────────────────────────────────────────────────
async def _api(
    client: httpx.AsyncClient, params: dict[str, str], *, host: str = HOST
) -> dict[str, Any]:
    response = await client.get(
        f"https://{host}/w/api.php",
        params={"action": "query", "format": "json", "formatversion": "2", **params},
    )
    response.raise_for_status()
    return response.json()


async def _category_members(
    client: httpx.AsyncClient,
    category: str,
    ctype: str,
    *,
    limit: int = 2000,
    host: str = HOST,
) -> list[str]:
    """取一个分类的成员（ctype="page" 条目 / "subcat" 子分类）。分页取全量。"""
    members: list[str] = []
    params: dict[str, str] = {
        "list": "categorymembers",
        "cmtitle": category,
        "cmtype": ctype,
        "cmlimit": "500",
    }
    if ctype == "page":
        params["cmnamespace"] = "0"
    while True:
        try:
            payload = await _api(client, params, host=host)
        except Exception as exc:  # noqa: BLE001
            logger.warning("分类 %s 取成员失败: %s: %s", category, type(exc).__name__, exc)
            break
        members.extend(
            str(item.get("title") or "")
            for item in ((payload.get("query") or {}).get("categorymembers") or [])
            if item.get("title")
        )
        cont = payload.get("continue") or {}
        if "cmcontinue" not in cont or len(members) >= limit:
            break
        params["cmcontinue"] = str(cont["cmcontinue"])
        await _sleep()
    return members[:limit]


async def _search_titles(
    client: httpx.AsyncClient, query: str, *, limit: int = 50, host: str = HOST
) -> list[str]:
    """搜索条目标题（namespace 0）。与 collect_corpus 的同名函数不同：那个是
    「清单写错了帮我找候选」，这里的用途是**批量发现**，所以取满 limit。"""
    hits: list[str] = []
    params: dict[str, str] = {
        "list": "search",
        "srsearch": query,
        "srlimit": "50",
        "srnamespace": "0",
    }
    while len(hits) < limit:
        try:
            payload = await _api(client, params, host=host)
        except Exception as exc:  # noqa: BLE001
            logger.warning("搜索失败 %s: %s: %s", query, type(exc).__name__, exc)
            break
        batch = ((payload.get("query") or {}).get("search")) or []
        hits.extend(str(hit.get("title") or "") for hit in batch if hit.get("title"))
        cont = payload.get("continue") or {}
        if "sroffset" not in cont or not batch:
            break
        params["sroffset"] = str(cont["sroffset"])
        await _sleep()
    return hits[:limit]


async def discover(
    client: httpx.AsyncClient, *, depth: int = 2, per_search: int = 50
) -> tuple[dict[str, set[str]], dict[str, Any]]:
    """两级发现。返回 (候选标题 -> 来源理由集合, 统计信息)。"""
    reasons: dict[str, set[str]] = {}
    stats: dict[str, Any] = {"categories": {}, "search": {}}

    # ① 分类树 BFS
    seen_cats: set[str] = set()
    queue: list[tuple[str, int]] = [(cat, 0) for cat in SEED_CATEGORIES]
    while queue:
        category, cat_depth = queue.pop(0)
        if category in seen_cats:
            continue
        seen_cats.add(category)
        if any(marker in category for marker in EXCLUDE_CATEGORY_MARKERS):
            continue
        pages = await _category_members(client, category, "page")
        stats["categories"][category] = len(pages)
        for title in pages:
            reasons.setdefault(title, set()).add(category)
        await _sleep()
        if cat_depth < depth:
            subs = await _category_members(client, category, "subcat")
            queue.extend((sub, cat_depth + 1) for sub in subs)
            await _sleep()

    # ② 关键词搜索
    for seed in SEARCH_SEEDS:
        titles = await _search_titles(client, seed, limit=per_search)
        stats["search"][seed] = len(titles)
        for title in titles:
            reasons.setdefault(title, set()).add(f"search:{seed}")
        await _sleep()

    return reasons, stats


# ── 产物写出 ────────────────────────────────────────────────────────────────
def _raw_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "raw" / "wikipedia"


def _cards_dir() -> Path:
    # 与 collect_corpus 一致写到 v2/ **子目录**：load_corpus 只 glob 一层，
    # 所以这里的文件在「提升」为线上语料前不会影响检索基线。
    return Path(__file__).resolve().parents[1] / "data" / "corpus" / "v2"


def _write_raw(
    title: str,
    text: str,
    accessed: str,
    *,
    host: str = HOST,
    out_dir: Path | None = None,
    source_label: str = "中文维基百科",
) -> Path:
    target = out_dir or _raw_dir()
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{_safe_filename(title)}.md"
    url = f"https://{host}/wiki/{quote(title.replace(' ', '_'))}"
    header = (
        "---\n"
        f"title: {title}\n"
        f"url: {url}\n"
        f"source: {source_label}\n"
        "license: CC BY-SA 4.0\n"
        f"accessed: {accessed}\n"
        "---\n\n"
    )
    path.write_text(header + text.strip() + "\n", encoding="utf-8")
    return path


def _load_index(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rows.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    return rows


def _append_index(path: Path, row: dict[str, Any]) -> None:
    """索引**逐条追加**而不是最后统一写。

    理由：全量抓取要几分钟（串行限速），中途超时/断网会丢掉「抓到哪了」这个状态，
    下次只能从头重跑 —— 对公益站点重复发请求是浪费。逐条追加后，任何中断都
    留下可续采的现场。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _append_cards(path: Path, cards: list[dict[str, Any]], *, with_header: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        if with_header:
            handle.write(
                "// 由 scripts/crawl_sanxingdui.py 采集，请勿手工编辑：本文件可重新生成。\n"
                f"// 采集日期 {date.today().isoformat()}；来源与授权见每条的 citation / license 字段。\n"
                "// 维基百科内容依 CC BY-SA 4.0 使用，展示时须保留署名与许可链接。\n"
            )
        for card in cards:
            handle.write(json.dumps(card, ensure_ascii=False) + "\n")


def _dedupe_cards_file(path: Path) -> int:
    """对已写出的卡片文件做一次全局去重（按正文前 120 字），返回最终条数。

    跨条目也会重复：维基条目之间大量共用导语（如「三星堆遗址位于……」
    在多篇里出现），不去重会让同一条证据在检索结果里反复占名额。
    """
    if not path.exists():
        return 0
    seen: set[str] = set()
    unique: list[str] = []
    header: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("//"):
            header.append(stripped)
            continue
        try:
            card = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        fingerprint = str(card.get("text") or "")[:120]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(json.dumps(card, ensure_ascii=False))
    path.write_text("\n".join(header + unique) + "\n", encoding="utf-8")
    return len(unique)


# ── 子命令 ──────────────────────────────────────────────────────────────────
async def cmd_probe() -> int:
    print("=" * 72)
    print("三星堆专题发现（只读，不抓取、不写文件）")
    print(f"使用的代理: {_resolve_proxy() or '（无，直连）'}")
    print(f"User-Agent: {_user_agent()}")
    print("=" * 72)
    async with _client() as client:
        reasons, stats = await discover(client)

    print("\n--- 分类树（成员数；0 = 分类名不存在或为空）---")
    for category, count in stats["categories"].items():
        flag = "" if count else "  ← 无成员，检查分类名"
        print(f"  {count:>4}  {category}{flag}")

    print("\n--- 关键词搜索（命中数，截断到 50/词）---")
    for seed, count in stats["search"].items():
        print(f"  {count:>4}  {seed}")

    in_cat = sum(1 for v in reasons.values() if any(not r.startswith("search:") for r in v))
    in_search = sum(1 for v in reasons.values() if any(r.startswith("search:") for r in v))
    print("\n" + "=" * 72)
    print(f"候选条目去重后: {len(reasons)} 篇（分类 {in_cat} / 搜索 {in_search}）")
    print("说明：这是**抓取前**的规模，抓回正文后还会按主题词过滤，实际入库数会少于此值。")
    return 0


async def cmd_crawl(*, target: int, max_fetch: int) -> int:
    accessed = date.today().isoformat()
    index_path = _raw_dir() / "_index.jsonl"
    cards_path = _cards_dir() / "wikipedia_crawl.jsonl"

    # 断点续采：读上次索引，已处理过的标题不再重复请求。
    # 全量抓取要几分钟，中途超时不可避免（实测本机网络就会重置长连接）；
    # 没有续采的话每次中断都要从头发一遍请求 —— 对公益站点是纯浪费。
    existing = _load_index(index_path)
    done: set[str] = set()
    kept_before = 0
    for row in existing:
        status = str(row.get("status") or "")
        requested = str(row.get("requested") or "")
        if requested and status.startswith(("kept", "missing", "rejected")):
            done.add(requested)
        if status == "kept":
            kept_before += 1
    if kept_before:
        print(f"检测到已有索引：上次收录 {kept_before} 篇，本次续采（跳过 {len(done)} 条已处理标题）。")

    async with _client() as client:
        reasons, _stats = await discover(client)
        ranked = sorted(
            (title for title in reasons if title not in done),
            key=lambda title: (_priority(title, reasons[title]), title),
        )
        print(f"发现候选 {len(reasons)} 篇；待处理 {len(ranked)} 篇，按相关性排序抓取（目标 {target} 篇）。")

        batch = ranked[:max_fetch]
        kept = kept_before
        cards_header_needed = not cards_path.exists() or cards_path.stat().st_size == 0

        # 分块抓取：`_fetch_extracts` 是整批抓完才返回的，一次抓 200 条的话
        # 中途断网等于什么都没留下。分块到 10 条，中断损失上限就是 10 条。
        for start in range(0, len(batch), 10):
            chunk = batch[start : start + 10]
            pages = await _fetch_extracts(client, HOST, chunk)
            for requested in chunk:
                got = pages.get(requested)
                if not got:
                    _append_index(
                        index_path,
                        {
                            "requested": requested,
                            "title": requested,
                            "status": "missing",
                            "reasons": sorted(reasons[requested]),
                        },
                    )
                    continue
                final_title, text = got
                if not _is_relevant(final_title, text):
                    # 标题即跑题（通用大盘/同名异物）：记录拒绝原因，便于复核闸门是否过严
                    _append_index(
                        index_path,
                        {
                            "requested": requested,
                            "title": final_title,
                            "status": "rejected:off-topic",
                            "chars": len(text),
                            "reasons": sorted(reasons[requested]),
                        },
                    )
                    print(f"  [跳过·跑题] {final_title}")
                    continue
                path = _write_raw(final_title, text, accessed)
                got_cards = _wiki_cards(final_title, text, accessed)[:MAX_CARDS_PER_PAGE]
                _append_cards(cards_path, got_cards, with_header=cards_header_needed)
                cards_header_needed = False
                kept += 1
                _append_index(
                    index_path,
                    {
                        "requested": requested,
                        "title": final_title,
                        "status": "kept",
                        "chars": len(text),
                        "cards": len(got_cards),
                        "file": path.name,
                        "url": f"https://{HOST}/wiki/{final_title.replace(' ', '_')}",
                        "accessed": accessed,
                        "reasons": sorted(reasons[requested]),
                    },
                )
                print(f"  [{kept:>3}] {final_title}: {len(text)} 字 / {len(got_cards)} 卡")
            if kept >= target:
                print(f"已达目标 {target} 篇，停止抓取。")
                break

    total_cards = _dedupe_cards_file(cards_path)
    print("\n" + "=" * 72)
    print(f"收录 {kept} 篇；卡片去重后 {total_cards} 张")
    print(f"原始文档: {_raw_dir()}")
    print(f"语料卡:   {cards_path}")
    print(f"索引:     {index_path}")
    if kept < target:
        print(f"[注意] 只收到 {kept} 篇，未达目标 {target} —— 再跑一次会续采，或扩充种子分类/关键词。")
    return 0


# ── 英文 / 日文维基 ─────────────────────────────────────────────────────────
# 中文维基的天花板约 40 篇且多为短桩，因此把同一套采集机制复用到其他语种。
# 相关性判据按站点分别配置：英文条目名不含汉字，关键词表必须换一套；
# 且**不能**对英文标题套 `fold()`（它是中文繁简折叠）。
MULTILINGUAL: dict[str, dict[str, Any]] = {
    "en": {
        "host": "en.wikipedia.org",
        "label": "英文维基百科",
        "seeds": [
            "Sanxingdui", "Jinsha site", "Baodun culture", "Shu (state)",
            "Ancient Shu", "Sanxingdui bronze", "Sanxingdui mask",
            "Bronze Age Sichuan", "Chengdu Plain", "Shu civilization",
        ],
        # 标题词必须**高特异**：初版放了 "shu"/"chengdu"/"sichuan"，结果
        # Chengdu（79k 字的城市条目）、成都的机场与火车站、Conquest of Shu by Wei、
        # Former Shu 全被收进来 —— 与中文版收进「中国历史」是同一类错误。
        "title_keywords": [
            "sanxingdui", "jinsha site", "baodun", "ancient shu", "ba–shu", "ba-shu",
        ],
        "markers": ["Sanxingdui", "Jinsha", "Baodun", "Ancient Shu"],
        "noise": [
            "airport", "railway", "station", "metro", "dialect", "language",
            "list of", "migration", "cuisine", "university", "province", "prefecture",
            "anime", "manga", "film", "television", "novel", "video game", "series",
            "fantasy", "kukuriraige",
        ],
    },
    "ja": {
        "host": "ja.wikipedia.org",
        "label": "日文维基百科",
        "seeds": [
            "三星堆", "三星堆遺跡", "古蜀", "金沙遺跡", "青銅神樹", "蜀 (古代国家)",
            "巴蜀", "三星堆博物館", "長江文明",
        ],
        # 同样去掉泛词「蜀」（会命中蜀漢/前蜀/後蜀）；靠「古蜀」「三星堆」兜住。
        "title_keywords": ["三星堆", "古蜀", "金沙遺跡", "巴蜀", "青銅神樹"],
        "markers": ["三星堆", "古蜀", "金沙", "蜀"],
        "noise": [
            "伝奇", "伝記", "アニメ", "漫画", "小説", "ゲーム", "映画", "ドラマ",
            "駅", "空港", "語", "列車",
        ],
    },
}


async def cmd_multilingual(*, langs: list[str], per_search: int, max_fetch: int) -> int:
    accessed = date.today().isoformat()
    grand = 0
    async with _client() as client:
        for lang in langs:
            cfg = MULTILINGUAL[lang]
            host = str(cfg["host"])
            print(f"\n=== {cfg['label']} ({host}) ===")
            found: dict[str, str] = {}
            for seed in cfg["seeds"]:
                titles = await _search_titles(client, seed, limit=per_search, host=host)
                for title in titles:
                    found.setdefault(title, seed)
                await _sleep()
            print(f"  候选 {len(found)} 篇，开始抓取...")

            out_dir = _raw_dir().parent / lang
            cards_path = _cards_dir() / f"{lang}_crawl.jsonl"
            cards_first = not cards_path.exists() or cards_path.stat().st_size == 0
            kept = 0
            batch = sorted(found)[:max_fetch]
            for start in range(0, len(batch), 10):
                chunk = batch[start : start + 10]
                pages = await _fetch_extracts(client, host, chunk)
                for requested in chunk:
                    got = pages.get(requested)
                    if not got:
                        continue
                    final_title, text = got
                    lowered = final_title.lower()
                    if any(n in lowered for n in cfg.get("noise", [])):
                        continue
                    if not any(k.lower() in lowered for k in cfg["title_keywords"]):
                        continue
                    if not any(m in text for m in cfg["markers"]):
                        continue
                    _write_raw(
                        final_title,
                        text,
                        accessed,
                        host=host,
                        out_dir=out_dir,
                        source_label=str(cfg["label"]),
                    )
                    got_cards = _wiki_cards(final_title, text, accessed)[:MAX_CARDS_PER_PAGE]
                    _append_cards(cards_path, got_cards, with_header=cards_first)
                    cards_first = False
                    kept += 1
                    print(f"  [{kept:>3}] {final_title}: {len(text)} 字 / {len(got_cards)} 卡")
            _dedupe_cards_file(cards_path)
            print(f"  {cfg['label']} 收录 {kept} 篇 -> {out_dir}")
            grand += kept
    print(f"\n多语言合计收录 {grand} 篇")
    return 0


# ── 百度百科（只存摘要 + 链接）────────────────────────────────────────────────
# 为什么只存摘要：百科正文有版权，规模化抓取整页也违反站点条款。摘要是一个词条的
# 定义性概述，足以充当「这是什么东西」的线索；同时保留 url 供人工回访原文。
# license 记为 `quote_only`（与 collect_corpus 的许可枚举一致）表示「只保留引文片段」。
BAIKE_API = "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"
BAIKE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
BAIKE_NOTE = "百度百科词条摘要（受版权约束，仅保留摘要与链接，未存全文）"
BAIKE_TERMS: list[str] = [
    # 遗址与格局
    "三星堆遗址", "三星堆", "三星堆祭祀坑", "三星堆一号坑", "三星堆二号坑",
    "三星堆三号坑", "三星堆四号坑", "三星堆五号坑", "三星堆六号坑", "三星堆七号坑",
    "三星堆八号坑", "三星堆城墙", "月亮湾城墙", "青关山台地", "三星堆祭祀区",
    "三星堆博物馆", "三星堆研究院", "金沙遗址", "金沙遗址博物馆", "宝墩文化",
    "十二桥文化", "古蜀", "古蜀文明", "巴蜀文化", "巴蜀图语", "蜀王本纪",
    "华阳国志", "蚕丛", "鱼凫", "柏灌", "杜宇", "鳖灵", "开明",
    # 青铜器
    "青铜神树", "青铜大立人像", "青铜立人像", "青铜纵目面具", "青铜面具",
    "青铜大面具", "戴金面罩青铜人头像", "青铜人头像", "青铜太阳轮", "青铜神坛",
    "青铜神兽", "青铜大鸟头", "青铜人身形器", "青铜扭头跪坐人像", "青铜跪坐人像",
    "顶尊跪坐人像", "青铜蛇", "青铜龙", "青铜尊", "青铜罍", "铜小立人", "铜铃",
    "铜挂饰", "圆口方尊", "爬龙器盖", "青铜眼形器", "青铜车轮形器", "太阳形器",
    # 金器 / 玉器
    "金杖", "金面罩", "黄金面具", "金箔", "金鸟形饰", "玉璋", "玉边璋", "玉琮",
    "玉璧", "玉戈", "玉凿", "玉石器",
    # 陶器 / 石器 / 动植物遗存
    "陶盉", "陶三足炊器", "陶高柄豆", "陶小平底罐", "石璧", "石跪坐人像",
    "象牙", "象牙雕", "海贝", "虎牙",
    # 文化与研究
    "三星堆文化", "三星堆文明", "三星堆青铜文化", "三星堆考古",
]


async def cmd_baike(*, max_terms: int) -> int:
    accessed = date.today().isoformat()
    out_dir = _raw_dir().parent / "baike"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "_index.jsonl"
    cards_path = _cards_dir() / "baike_crawl.jsonl"
    cards_first = not cards_path.exists() or cards_path.stat().st_size == 0

    kept = 0
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=15.0),
        headers={"User-Agent": BAIKE_UA, "Referer": "https://baike.baidu.com/"},
        trust_env=False,
        proxy=_resolve_proxy(),
        follow_redirects=True,
    ) as client:
        for term in BAIKE_TERMS[:max_terms]:
            try:
                response = await client.get(
                    BAIKE_API,
                    params={
                        "scope": "103",
                        "format": "json",
                        "appid": "379020",
                        "bk_key": term,
                        "bk_length": "600",
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                _append_index(
                    index_path,
                    {"requested": term, "title": term, "status": f"fail:{type(exc).__name__}"},
                )
                print(f"  [失败] {term}: {type(exc).__name__}")
                await _sleep()
                continue
            abstract = str(
                payload.get("abstract") or payload.get("abstractPlainText") or ""
            ).strip()
            if not abstract:
                _append_index(index_path, {"requested": term, "title": term, "status": "missing"})
                print(f"  [无摘要] {term}")
                await _sleep()
                continue
            url = f"https://baike.baidu.com/item/{quote(term)}"
            meta = _baike_meta(payload)
            path = out_dir / f"{_safe_filename(term)}.md"
            meta_lines = "".join(f"{key}: {value}\n" for key, value in meta["fields"].items())
            path.write_text(
                "---\n"
                f"title: {term}\n"
                f"url: {url}\n"
                "source: 百度百科\n"
                "license: quote_only（仅摘要，未存全文）\n"
                f"accessed: {accessed}\n"
                + meta_lines
                + "---\n\n"
                + abstract
                + "\n",
                encoding="utf-8",
            )
            card = {
                "doc_id": f"baike-{_stable_slug(term)}-01",
                "title": term,
                "text": abstract,
                "quote": abstract,
                "object": meta["object"],
                "era": meta["era"],
                "category": meta["category"],
                "tags": meta["tags"],
                "source_type": "encyclopedia",
                "license": "quote_only",
                "note": BAIKE_NOTE,
                "citation": {
                    "title": term,
                    "author": "百度百科编者",
                    "publisher": "百度百科（仅摘要引用）",
                    "year": "",
                    "locator": "",
                    "url": url,
                    "accessed": accessed,
                },
            }
            _append_cards(cards_path, [card], with_header=cards_first)
            cards_first = False
            kept += 1
            _append_index(
                index_path,
                {
                    "requested": term,
                    "title": term,
                    "status": "kept",
                    "chars": len(abstract),
                    "file": path.name,
                    "url": url,
                    "accessed": accessed,
                },
            )
            print(f"  [{kept:>3}] {term}: {len(abstract)} 字")
            await _sleep()

    total = _dedupe_cards_file(cards_path)
    print("\n" + "=" * 72)
    print(f"百度百科：收录 {kept} 条摘要（去重后卡片 {total} 张）")
    print(f"摘要文档: {out_dir}")
    print(f"语料卡:   {cards_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="三星堆专题爬虫（批量扩充语料）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe", help="只发现候选条目，不抓取")
    crawl = sub.add_parser("crawl", help="中文维基：发现 + 抓取 + 写出")
    crawl.add_argument("--target", type=int, default=100, help="目标收录篇数（默认 100）")
    crawl.add_argument("--max-fetch", type=int, default=220, help="最多抓取的候选条数（默认 220）")
    multi = sub.add_parser("multi", help="英文/日文维基：发现 + 抓取 + 写出")
    multi.add_argument("--langs", default="en,ja", help="语种，逗号分隔（默认 en,ja）")
    multi.add_argument("--per-search", type=int, default=30, help="每个种子取多少条搜索结果")
    multi.add_argument("--max-fetch", type=int, default=80, help="每个语种最多抓取候选条数")
    baike = sub.add_parser("baike", help="百度百科：只抓摘要 + 链接")
    baike.add_argument("--max-terms", type=int, default=200, help="最多查询的词条数")
    args = parser.parse_args()

    if args.command == "probe":
        return asyncio.run(cmd_probe())
    if args.command == "baike":
        return asyncio.run(cmd_baike(max_terms=args.max_terms))
    if args.command == "multi":
        langs = [lang.strip() for lang in str(args.langs).split(",") if lang.strip()]
        return asyncio.run(
            cmd_multilingual(langs=langs, per_search=args.per_search, max_fetch=args.max_fetch)
        )
    return asyncio.run(cmd_crawl(target=args.target, max_fetch=args.max_fetch))


if __name__ == "__main__":
    raise SystemExit(main())
