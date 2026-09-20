"""语料采集器（v2）—— 从公开来源生成带完整出处的史料卡片。

三条纪律
--------

1. **只走公开、有文档、有使用条款的接口。**
   本脚本只用 MediaWiki 的 `action=query&prop=extracts`（维基百科 / 维基文库共用
   同一套公开 API），不做 HTML 抓取（页面改版即断，且拿不到结构化章节），
   更不去打任何站点的私有 XHR 接口 —— 私有接口的可用性与授权边界都不是我们能决定的。

2. **必须记 `url` + `accessed`（抓取日期）。**
   没有抓取日期的网页引用无法复核：网页会变，「引用的是哪天的版本」本身就是引用的一部分。
   `Citation.is_traceable()` 对此有硬校验，缺 accessed 的卡片会被判为不可追溯。

3. **串行 + 限速。**
   对方是公益站点。采集量只有几百条，没有任何理由给它压力。

输出与 v1 的关系
----------------
写入 `data/corpus/v2/`（**子目录**）。`load_corpus` 只 glob 一层，所以采集期间
v1 语料仍是线上语料 —— `docs/RAG_PLAN.md` P5 要先用它测「改造前基线」，
不能提前换掉，否则那条基线永远测不到了。

用法
----
    python scripts/collect_corpus.py probe          # 只验证可达性与接口形态，不写文件
    python scripts/collect_corpus.py wiki           # 采集维基百科条目
    python scripts/collect_corpus.py wikisource     # 采集维基文库公版古籍
    python scripts/collect_corpus.py all
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 控制台默认是 GBK。采集脚本会打印页面标题（含生僻字、异体字），
# 任何一个字符编不出来都会让整个采集任务崩掉 —— 而崩在最后一条比崩在第一条更亏
# （已经抓到的东西全丢了）。所以把 stdout 设成容错模式，编不出的字变 `?`。
try:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

from app.core.config import settings  # noqa: E402
from app.core.logging import get_logger  # noqa: E402

logger = get_logger("scripts.collect_corpus")

# ── User-Agent ──────────────────────────────────────────────────────────────
# Wikimedia 的 WAF 要求 UA 里带**可联系的标识**（URL 或邮箱）。实测四种写法：
#
#   ".../1.0 (research corpus; contact: project owner)"  -> 403  ← 括号里没有 URL/邮箱
#   ".../1.0"                                           -> 403  ← 完全没有括号
#   ".../1.0 (https://example.org/x)"                   -> 200
#   "Mozilla/5.0 (compatible; .../1.0)"                  -> 403  ← 伪装浏览器反而更明确地被拒
#
# 结论：**「换个像浏览器的 UA」是错的方向**，它要的是能被联系到。
# 这条坑值得记：遇到 403 时很容易直觉去伪装 UA，而对方反的就是这个。
#
# ⚠️ 默认值里的地址是 **IANA 保留的占位域名**（example.org），不是真实项目地址 ——
# 之所以不用一个看起来像真的域名，是因为那会让人以为联系得上，而实际联系不上。
# 正式或规模化采集前，请把 SXD_COLLECT_UA 设成真实的项目主页或邮箱：
# 这是对方使用条款的要求，不是可选的礼貌。
DEFAULT_USER_AGENT = "SanXingDuiRAG/1.0 (+https://example.org/sanxingdui; placeholder)"


def _user_agent() -> str:
    import os

    return os.environ.get("SXD_COLLECT_UA") or DEFAULT_USER_AGENT


def _network_hint(exc: BaseException) -> str:
    """把连接类失败翻译成**可执行的**提示。

    为什么需要它：实测的失败日志是
        `维基百科采集失败: ConnectTimeout: `
    —— 异常消息是**空字符串**，看不出该做什么、甚至看不出是网络问题。
    真实原因通常是本机代理软件没开：WinINET 里虽然记着 `127.0.0.1:7897`，
    但 `ProxyEnable=0`，`urllib.getproxies()` 返回 `{}`，于是采集退化为直连，
    而维基在直连下会被阻断（不是超时就是连不上）。

    非连接类异常不做翻译，直接回显类型 —— 乱给建议比不给更浪费时间。
    """
    if not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TimeoutException)):
        return ""
    proxy = _resolve_proxy()
    if proxy:
        return (
            f"当前解析到的代理是 {proxy}，但它可能没在运行。"
            "确认代理软件已启动，或用 SXD_COLLECT_PROXY 指定可用代理。"
        )
    return (
        "未检测到可用代理，采集已退化为直连，而该站点在直连下会被阻断。"
        "请启动代理软件后重跑；或显式指定："
        "set SXD_COLLECT_PROXY=http://127.0.0.1:7897"
    )

# 串行限速：两次请求之间的最小间隔（秒）。对方是公益站点，不抢带宽。
REQUEST_INTERVAL = 1.0

OUTPUT_DIR = settings.resolve("data/corpus/v2")

# ── 采集清单 ────────────────────────────────────────────────────────────────
# 条目的挑选标准：**与复原任务和问答实际会被问到的东西直接相关**，
# 而不是「三星堆相关的都收」。收广度会稀释检索精度。
#
# 标题必须先用 `probe` 校验：这里写的是**条目规范名**，凭印象写会得到「页面不存在」。
# 实测修正过程（这些都不是拼写错误，而是条目名与俗称不同）：
#   青铜大立人像      -> 青铜立人像   （条目用「立人像」，没有「大」字）
#   三星堆祭祀坑      -> 无独立条目，内容并入「三星堆遗址」
#   黄金面具          -> 页面存在但切不出卡片（疑似消歧义页），暂不纳入，待 P3 另找
#   蜀王本纪          -> 简体、繁体两个标题在文库都不存在，暂不纳入
# `probe` 对取不到的标题会自动搜候选，就是为这类修正准备的。
WIKI_PAGES: list[str] = [
    "三星堆遗址",
    "青铜立人像",
    "青铜神树",
    "金杖",
    "青铜纵目面具",
    "三星堆博物馆",
    "金沙遗址",
    "古蜀",
    "宝墩文化",
    "十二桥文化",
]

# 维基文库按**传统字形**存古籍原文（这是原文形态，不要改成简体）。
# 注意 `蜀王本紀` 在文库是空页面，规范名是简体的 `蜀王本纪`。
WIKISOURCE_PAGES: list[str] = [
    "華陽國志/卷三",          # 蜀志 ——「其目纵」等关键记载的出处
    "山海經/海外南經",        # 与神树、神鸟形象相关
]


# ── HTTP ────────────────────────────────────────────────────────────────────
def _resolve_proxy() -> str | None:
    """决定采集要不要走代理。

    这条策略与 `app/core/http.py` **方向相反但道理一致**：那边对「本机与私有网段」
    绕开系统代理（否则连自己的后端都会 502）；这边访问的是**公网**，实测
    直连 `zh.wikipedia.org` 会 ConnectTimeout，而本机 WinINET 配了 Clash
    （`127.0.0.1:7897`），走它可以通。

    踩过的坑：先用 PowerShell 的 `Invoke-WebRequest` 测「直连」显示 200，
    就误以为公网可直连 —— 其实 `Invoke-WebRequest` 默认走 WinINET 系统代理，
    那次「直连」根本没直连。**测连通性必须用与生产相同的客户端栈**，
    否则测出来的是测试工具的属性，不是链路的属性。

    优先级：`SXD_COLLECT_PROXY` 显式指定 > `SXD_COLLECT_NO_PROXY` 强制直连 > 系统代理。
    """
    import os

    explicit = os.environ.get("SXD_COLLECT_PROXY")
    if explicit:
        return explicit or None
    if os.environ.get("SXD_COLLECT_NO_PROXY"):
        return None
    try:
        import urllib.request

        proxies = urllib.request.getproxies()
    except Exception:  # noqa: BLE001
        return None
    return proxies.get("https") or proxies.get("http") or None


def _client() -> httpx.AsyncClient:
    """采集用的 HTTP 客户端。

    `trust_env=False`：环境变量里没有代理（实测 `HTTPS_PROXY` 为空），
    代理由 `_resolve_proxy()` 显式决定并传入，避免 httpx 再去读一遍环境
    导致「到底走没走代理」说不清。探测输出会打印实际选中的代理。
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(45.0, connect=20.0),
        headers={"User-Agent": _user_agent()},
        trust_env=False,
        proxy=_resolve_proxy(),
        follow_redirects=True,
    )


def _api_url(host: str) -> str:
    return f"https://{host}/w/api.php"


async def _fetch_extracts(
    client: httpx.AsyncClient, host: str, titles: list[str]
) -> dict[str, tuple[str, str]]:
    """逐条取页面纯文本。返回 {请求标题: (最终标题, 正文)}。

    用 `explaintext=1` 而不是解析 HTML：纯文本里保留了 `== 章节 ==` 标记，
    正好作为切分的天然边界，比在 HTML 里找 <h2> 稳得多。

    **为什么必须逐条请求（不能批量）**
    TextExtracts 的 `exlimit` 在未设 `exintro`（也就是要**全文**）时上限是 **1** ——
    扩展为了防止响应过大做的硬限制，文档写在 `exlimit` 的说明下面。
    实测：一次带 12 个标题，只有 1 个返回 `extract`，其余在响应里就是一个
    没有 `extract` 字段的 page 对象，**表现与「页面不存在」几乎一样**。
    如果就这样跑采集，会安静地少掉 11/12 的页面，日志里还只会显示几句
    「页面不存在」——看起来像清单写错了，而不是代码写错了。
    （这也是「先校验整份清单再采集」的价值：它把这个错误在花钱之前暴露出来。）

    返回的 key 用**请求标题**而非最终标题：`redirects=1` 时响应里的 `title`
    已经是重定向后的目标，直接拿它当 key 会让「哪些标题没取到」的比对全部错位
    （实测「三星堆博物馆」被重定向到繁体「三星堆博物館」，于是被误报为取不到）。
    最终标题仍要保留 —— 它才是引用 URL 应该指向的规范地址。
    """
    result: dict[str, tuple[str, str]] = {}
    for title in titles:
        response = await client.get(
            _api_url(host),
            params={
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "extracts",
                "explaintext": "1",
                "exlimit": "1",  # 见 docstring：全文模式下这也只能是 1
                "redirects": "1",
                "titles": title,
            },
        )
        response.raise_for_status()
        payload = response.json()
        pages = (payload.get("query") or {}).get("pages") or []
        for page in pages:
            final_title = str(page.get("title") or title)
            if page.get("missing"):
                logger.warning("页面不存在: %s", title)
                continue
            text = str(page.get("extract") or "")
            if not text:
                logger.warning("页面存在但未返回正文（空页面）: %s", title)
                continue
            if final_title != title:
                logger.info("标题已重定向: %s -> %s", title, final_title)
            result[title] = (final_title, text)
        await _sleep()
    return result


def _page_url(host: str, title: str) -> str:
    from urllib.parse import quote

    return f"https://{host}/wiki/{quote(title.replace(' ', '_'))}"


async def _search_titles(
    client: httpx.AsyncClient, host: str, query: str, *, limit: int = 3
) -> list[str]:
    """按关键词搜页面标题，用于修正清单里写错的标题。

    为什么值得内置：清单里的标题大多是凭印象写的，写错就是「页面不存在」。
    让人去浏览器里翻正确写法很低效，而且容易把「这个主题没条目」误判成
    「我拼错了」—— 自动搜一下能立刻区分这两种情况。
    """
    try:
        response = await client.get(
            _api_url(host),
            params={
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "list": "search",
                "srsearch": query,
                "srlimit": str(limit),
            },
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("搜索失败 %s: %s", query, exc)
        return []
    hits = ((response.json().get("query") or {}).get("search")) or []
    return [str(hit.get("title") or "") for hit in hits if hit.get("title")]


# ── 正文清洗 ────────────────────────────────────────────────────────────────
# 维基文库的古籍页面把历代校勘记**内联**在正文里，用三种括号区分：
#   〈…〉 校勘注（异文讨论）      【…】 校勘者标记的删字      〔…〕 校勘者补入的字
# 实测《華陽國志》卷三：97 张卡片共 34010 字，其中 **19454 字是校勘注，占 57.2%**；
# 60/97 张卡片的校勘注占比超过一半，最高的一张 97.9%（几乎整张都是校记）。
#
# 为什么必须剥离：这些内容讨论的是版本异文（「廖本注：當脫唐虞二字」「顧觀光校云」），
# **不是古蜀史实**。留着会有两个后果：BM25 会去匹配「廖本」「當作」「脫」「衍」这类
# 校勘用语；而用户问「蚕丛是什么」时，检索会把校记当成史料返回。
#
# 三种括号不能一刀切，处理方式必须区分：
#   〈校勘注〉 -> **整段删除**（它是对正文的讨论，不是正文）
#   【删字】   -> **整段删除**（校勘者已判定此处不该有此字）
#   〔补字〕   -> **去括号、保留内容**（补入的字属通行读本，是正文的一部分）
#
# 代价要说清楚：剥离后的正文是「据校勘整理的通行读本」，与任何一个具体历史版本
# 都不完全等同。这个事实写进每张卡片的 `note`，而不是只留在这段注释里 ——
# 否则读语料的人会以为 `text` 就是某版本原文。
_APPARATUS_NOTE = re.compile(r"〈[^〉]*〉")
_APPARATUS_DELETED = re.compile(r"【[^】]*】")
_APPARATUS_SUPPLIED = re.compile(r"〔([^〕]*)〕")

APPARATUS_NOTE = (
    "维基文库版本内联了大量校勘注（〈…〉），本语料已剥离；"
    "正文为据校勘整理的通行读本，不等同于任何一个具体历史版本"
)

# 维基条目的索引类章节：永远是链接与文献列表，不构成证据。
# 留着只会让「外部链接」里的存档提示文字（「页面存档备份，存于互联网档案馆」）
# 进入索引，污染 BM25 词元。
_WIKI_SKIP_SECTIONS = frozenset(
    {
        "外部链接", "外部連結", "参考来源", "參考來源", "参考文献", "參考文獻",
        "相关条目", "相關條目", "参见", "參見", "注释", "註釋", "注釋",
        "研究书目", "研究書目", "参考资料", "參考資料", "延伸阅读", "延伸閱讀",
    }
)


def _strip_apparatus(text: str) -> str:
    """剥离古籍里的校勘记。规则与理由见上方常量处的注释。

    配对剥离之后**必须再处理残留**：实测《華陽國志》里校勘注会跨越段落
    （`〈` 开在段末、`〉` 闭在下段），配对正则在多处未配平的输入上会错配，
    剥离不干净 —— 实测 97 张古籍卡片里有 94 张残留了括号。

    残留时的处理方式很关键：**不能只把括号字符删掉**。那样会留下没有括号的
    校记文字，它与正文在形态上完全一样，却谈论着「廖本」「顧觀光」「當作」，
    检索时会被当成史料 —— 比不处理更危险。所以改为在第一个残留标记处截断，
    只保留其前的干净片段；若截断后太短，由调用方的长度阈值丢弃整张卡片。
    """
    text = _APPARATUS_NOTE.sub("", text)
    text = _APPARATUS_DELETED.sub("", text)
    text = _APPARATUS_SUPPLIED.sub(lambda match: match.group(1), text)

    cut = min((pos for pos in (text.find(ch) for ch in "〈〉【】") if pos >= 0), default=-1)
    if cut >= 0:
        text = text[:cut]
    return re.sub(r"[ \t]+", " ", text).strip()


def _strip_apparatus_ratio(raw: str) -> float:
    """校勘记占比。用于采集时如实报告「这张卡片原本有多少是校记」。"""
    if not raw:
        return 0.0
    removed = sum(len(m.group(0)) for m in _APPARATUS_NOTE.finditer(raw))
    removed += sum(len(m.group(0)) for m in _APPARATUS_DELETED.finditer(raw))
    removed += sum(len(m.group(0)) for m in _APPARATUS_SUPPLIED.finditer(raw))
    return removed / len(raw)


# ── 切分 ────────────────────────────────────────────────────────────────────
# 标题标记：`== 章节 ==` / `=== 小节 ===`，可跨 2 个以上等号。
_HEADING_RE = re.compile(r"(={2,})\s*([^=\n]+?)\s*\1")


def _split_sections(text: str) -> list[tuple[str, str]]:
    """把纯文本按 `== 章节 ==` 切成 (章节名, 正文)。

    章节边界是 MediaWiki 自己标的，比任何启发式规则都可靠；
    章节名还能直接当卡片的 `citation.locator`，让引用能定位到具体章节。

    **必须先把「行内标题」拆成独立行。**
    维基文库的古籍页面里，标题常常**与正文同一行**：
        `=== 二 === 有周之世，限以秦巴……`
    而最初的实现只认「以 `==` 开头**且**以 `==` 结尾」的整行，
    于是这类标题既不成为章节名，也不被剥离，直接混进正文 ——
    后果有两个：`=== 二 ===` 这种标记被索引进 BM25（污染词元），
    并且会出现在给用户看的**引用**里（引用里带上维基标记等于毁掉引用）。
    实测在《華陽國志》卷三上，96 张卡片里绝大多数都带着这个标记。
    """
    # 行内标题 -> 独立行，其余逻辑不变
    text = _HEADING_RE.sub(lambda match: f"\n{match.group(1)} {match.group(2)} {match.group(1)}\n", text)

    sections: list[tuple[str, str]] = []
    current_title = ""
    buffer: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("==") and stripped.endswith("=="):
            if buffer:
                sections.append((current_title, "\n".join(buffer).strip()))
                buffer = []
            current_title = stripped.strip("= ").strip()
            continue
        buffer.append(line)
    if buffer:
        sections.append((current_title, "\n".join(buffer).strip()))
    return [(name, body) for name, body in sections if body]


def _split_paragraphs(body: str, *, min_len: int = 80, max_len: int = 500) -> list[str]:
    """按空行切段，并做长度整形。

    - 短于 `min_len` 的段落与前一段合并（避免产出「另见」「注释」这类无信息卡片）；
    - 长于 `max_len` 的段落按句号切分（太长的卡片会让引用无法定位到具体主张）。

    刻意**不**做语义切分：在几百条的规模下，按段落切的噪声远小于引入一个
    切分模型带来的不可解释性。段落边界本身就是编者的语义边界。
    """
    chunks: list[str] = []
    for raw in body.split("\n\n"):
        paragraph = " ".join(raw.split())
        if not paragraph:
            continue
        if len(paragraph) < min_len and chunks:
            chunks[-1] = f"{chunks[-1]} {paragraph}".strip()
            continue
        if len(paragraph) <= max_len:
            chunks.append(paragraph)
            continue
        # 超长段落按中文句末标点切
        sentence_buffer = ""
        for sentence in _sentences(paragraph):
            if len(sentence_buffer) + len(sentence) > max_len and sentence_buffer:
                chunks.append(sentence_buffer.strip())
                sentence_buffer = sentence
            else:
                sentence_buffer += sentence
        if sentence_buffer.strip():
            chunks.append(sentence_buffer.strip())
    return [chunk for chunk in chunks if len(chunk) >= min_len]


def _sentences(text: str) -> list[str]:
    import re

    parts = re.split(r"(?<=[。！？；])", text)
    return [part for part in parts if part]


def _stable_slug(text: str) -> str:
    """由标题派生稳定的短标识。

    **为什么不能用「抽 ASCII 字符」这种常见做法**：中文标题里没有 ASCII，
    `re.sub(r"[^a-zA-Z0-9]+", "-", "三星堆遗址")` 得到的是空串，于是所有中文条目
    都退化成同一个 `page-NN` 前缀 —— doc_id 大面积撞车。
    实测后果：写出 151 条卡片，装载时只剩 129 条，**22 条被静默覆盖**
    （`load_corpus` 按 doc_id 建字典，后写覆盖先写），而日志里只有一行
    「语料库就绪: 129 条」，看不出任何异常。

    **为什么用哈希而不是拼音或序号**：同一标题必须永远得到同一个 doc_id。
    序号会随清单顺序变化，拼音需要额外依赖且「青铜立人像」这类词容易有歧义；
    哈希则稳定 —— 重采同一页面得到同一 id，向量复用才会命中，
    否则每次重采都会重复付 embedding 费用。
    """
    import hashlib

    return hashlib.blake2b(text.encode("utf-8"), digest_size=4).hexdigest()


# ── 卡片构造 ────────────────────────────────────────────────────────────────
def _wiki_cards(page_title: str, text: str, accessed: str) -> list[dict[str, Any]]:
    """维基百科条目 → encyclopedia 类型卡片（CC BY-SA，须署名）。"""
    cards: list[dict[str, Any]] = []
    sequence = 0
    for section, body in _split_sections(text):
        if section in _WIKI_SKIP_SECTIONS:
            # 索引类章节只有链接与文献列表，不构成证据；留着会让存档提示文字
            #（「页面存档备份，存于互联网档案馆」）进入 BM25 索引
            continue
        for paragraph in _split_paragraphs(body):
            sequence += 1
            cards.append(
                {
                    "doc_id": f"wiki-{_stable_slug(page_title)}-{sequence:02d}",
                    "title": f"{page_title}·{section}" if section else page_title,
                    "text": paragraph,
                    "quote": paragraph,
                    "object": "",
                    "era": "",
                    "category": "文化解读",
                    "tags": [],
                    "source_type": "encyclopedia",
                    "license": "cc_by_sa",
                    "citation": {
                        "title": page_title,
                        # 维基条目的作者是编者集体；CC BY-SA 要求署名，这里如实标明来源与许可
                        "author": "维基百科编者",
                        "publisher": "维基百科（CC BY-SA 4.0）",
                        "year": "",
                        "locator": section,
                        "url": _page_url("zh.wikipedia.org", page_title),
                        "accessed": accessed,
                    },
                }
            )
    return cards


def _wikisource_cards(
    page_title: str, text: str, accessed: str, *, author: str, era: str
) -> list[dict[str, Any]]:
    """维基文库古籍 → ancient_text 类型卡片。

    古籍的 `author` / `era` 由调用方显式给定，**不从页面猜** ——
    猜错作者等于伪造出处。
    """
    collection = page_title.split("/")[0]
    volume = page_title.split("/", 1)[1] if "/" in page_title else ""
    cards: list[dict[str, Any]] = []
    sequence = 0
    # 按章节切而不是直接切段落：章节名能进 `locator`，让引用精确到「卷三·二」，
    # 而不是只到「卷三」—— 后者对「这句话出自哪一段」的审计没有用。
    for section, body in _split_sections(text):
        # 先剥离校勘记再切段：否则切出来的段落边界由校记决定，
        # 通行读本的句子反而被校记打断（实测原始正文 57% 是校记）。
        cleaned = _strip_apparatus(body)
        if len(cleaned) < 20:
            # 整段都是校勘记 —— 剥离后没有正文可留，跳过而不是留一条空卡片
            continue
        for paragraph in _split_paragraphs(cleaned, min_len=20, max_len=400):
            sequence += 1
            locator = "·".join(part for part in (volume, section) if part)
            heading = f"《{collection}》{volume}" + (f"·{section}" if section else "")
            cards.append(
                {
                    # 用 page_title（含卷次）而非 collection 做哈希：
                    # 同一部书的不同卷必须得到不同的 id 前缀。
                    "doc_id": f"guji-{_stable_slug(page_title)}-{sequence:02d}",
                    "title": heading,
                    "text": paragraph,
                    "quote": paragraph,
                    "object": "",
                    "era": era,
                    "category": "出土记录",
                    "tags": [],
                    "source_type": "ancient_text",
                    "license": "public_domain",
                    "note": APPARATUS_NOTE,
                    "citation": {
                        "title": heading,
                        "author": author,
                        "publisher": "维基文库",
                        "year": era,
                        "locator": locator,
                        "url": _page_url("zh.wikisource.org", page_title),
                        "accessed": accessed,
                    },
                }
            )
    return cards


# ── 写出 ────────────────────────────────────────────────────────────────────
def _write(name: str, cards: list[dict[str, Any]]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{name}.jsonl"
    header = (
        f"// 由 scripts/collect_corpus.py 采集，请勿手工编辑：本文件可重新生成。\n"
        f"// 采集日期 {date.today().isoformat()}；来源与授权见每条的 citation / license 字段。\n"
        f"// 维基百科内容依 CC BY-SA 4.0 使用，展示时须保留署名与许可链接。\n"
    )
    with path.open("w", encoding="utf-8") as handle:
        handle.write(header)
        for card in cards:
            handle.write(json.dumps(card, ensure_ascii=False) + "\n")
    return path


def _dedupe(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按正文去重。

    维基条目里同一个事实常在不同章节重复出现（导语与正文），
    不去重会让检索结果出现多条近乎相同的证据，既浪费 top_n 名额，
    也会让「多路召回互证」这个信号失真。
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for card in cards:
        fingerprint = card["text"][:120]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(card)
    return unique


# ── 子命令 ──────────────────────────────────────────────────────────────────
async def cmd_probe() -> int:
    """校验**整份采集清单**：可达性、页面是否存在、每条能切出多少卡片。

    刻意一次校验全部标题，而不是先抓前两条看看 —— 「抓到一半才发现清单里
    有几个页面不存在」意味着要重跑，而重跑又要再向对方站点发一轮请求。
    一次请求可以带多个标题（MediaWiki 支持 titles=A|B|C），所以整份清单
    两个请求就够。
    """
    print("=" * 70)
    print("采集清单校验（只读，不写文件）")
    print(f"使用的代理: {_resolve_proxy() or '（无，直连）'}")
    print(f"User-Agent: {_user_agent()}")
    if _user_agent() == DEFAULT_USER_AGENT:
        print("  [注意] 用的是占位 UA。规模化采集前请设 SXD_COLLECT_UA 为真实联系地址。")
    print("=" * 70)

    total_cards = 0
    missing: list[str] = []
    async with _client() as client:
        for host, titles, label in (
            ("zh.wikipedia.org", WIKI_PAGES, "维基百科"),
            ("zh.wikisource.org", WIKISOURCE_PAGES, "维基文库"),
        ):
            print(f"\n--- {label} ({host}) ---")
            try:
                pages = await _fetch_extracts(client, host, titles)
            except Exception as exc:  # noqa: BLE001
                print(f"  [FAIL] {type(exc).__name__}: {exc}")
                missing.extend(titles)
                continue
            for title in titles:
                if title not in pages:
                    missing.append(title)
            for requested, (final_title, text) in pages.items():
                sections = _split_sections(text)
                paragraphs = [
                    paragraph for _, body in sections for paragraph in _split_paragraphs(body)
                ]
                total_cards += len(paragraphs)
                suffix = f"（重定向自 {requested}）" if final_title != requested else ""
                print(
                    f"  [OK] 《{final_title}》 {len(text)} 字 / {len(sections)} 章节 "
                    f"/ 可切出 {len(paragraphs)} 段{suffix}"
                )

    print("\n" + "=" * 70)
    print(f"预计可生成卡片: {total_cards} 条")
    if missing:
        print(f"[注意] 清单里不存在或取不到的页面 {len(missing)} 个，下面给出可能的正确标题:")
        async with _client() as client:
            for title in missing:
                # 去掉消歧义括号再搜（「黄金面具 (三星堆)」里的括号会干扰匹配）
                query = title.split("(")[0].split("（")[0].strip()
                candidates = await _search_titles(client, "zh.wikipedia.org", query)
                hint = "、".join(candidates) if candidates else "（搜不到，可能确实没有独立条目）"
                print(f"    - {title}")
                print(f"        候选: {hint}")
                await _sleep()
    else:
        print("清单全部有效。")
    return 0


async def cmd_wiki() -> int:
    async with _client() as client:
        accessed = date.today().isoformat()
        cards: list[dict[str, Any]] = []
        try:
            pages = await _fetch_extracts(client, "zh.wikipedia.org", WIKI_PAGES)
        except Exception as exc:  # noqa: BLE001
            # 一定带上异常类型：httpx 的网络类异常 str(exc) 经常是**空字符串**
            # （实测 ConnectTimeout 就是），只打 exc 会得到「维基百科采集失败: 」
            # 这种毫无指向性的日志，排查时等于没有信息。
            logger.error("维基百科采集失败: %s: %s", type(exc).__name__, exc)
            hint = _network_hint(exc)
            if hint:
                logger.error("可能的原因: %s", hint)
            return 1
        # 用**最终标题**构造引用：重定向后的地址才是内容的规范位置，
        # 引用一个会跳转的地址会让「可追溯」多一跳。
        for requested, (final_title, text) in pages.items():
            got = _wiki_cards(final_title, text, accessed)
            cards.extend(got)
            print(f"  {final_title}: {len(got)} 条" + (f"（重定向自 {requested}）" if final_title != requested else ""))
    cards = _dedupe(cards)
    path = _write("wikipedia", cards)
    print(f"\n写出 {len(cards)} 条 -> {path}")
    return 0


async def cmd_wikisource() -> int:
    # 作者与年代**显式给定**，不靠页面内容猜。猜错等于伪造出处。
    meta = {
        "華陽國志/卷三": ("常璩", "东晋"),
        "山海經/海外南經": ("佚名", "先秦"),
        # 蜀王本紀（扬雄，旧题）：简体/繁体标题在文库均不存在，暂不纳入。
        # 相关记载（蚕丛「其目纵」）已可由《華陽國志·蜀志》覆盖。
    }
    async with _client() as client:
        accessed = date.today().isoformat()
        cards: list[dict[str, Any]] = []
        try:
            pages = await _fetch_extracts(client, "zh.wikisource.org", WIKISOURCE_PAGES)
        except Exception as exc:  # noqa: BLE001
            logger.error("维基文库采集失败: %s: %s", type(exc).__name__, exc)
            hint = _network_hint(exc)
            if hint:
                logger.error("可能的原因: %s", hint)
            return 1
        for requested, (final_title, text) in pages.items():
            author, era = meta.get(requested, ("", ""))
            if not author:
                logger.warning("缺少作者/年代元数据，跳过（不得靠猜补齐出处）: %s", requested)
                continue
            got = _wikisource_cards(final_title, text, accessed, author=author, era=era)
            cards.extend(got)
            print(f"  {final_title}: {len(got)} 条")
    cards = _dedupe(cards)
    path = _write("wikisource", cards)
    print(f"\n写出 {len(cards)} 条 -> {path}")
    return 0


async def _sleep() -> None:
    import asyncio

    await asyncio.sleep(REQUEST_INTERVAL)


def cmd_check() -> int:
    """校验已采集的 v2 语料：字段完整度、可追溯率、长度分布。

    采集器必须能验收自己的产出。只报「写了 151 条」是一种粉饰 ——
    真正要回答的是「其中有多少条**能拿来当依据**」。
    用 `load_corpus` 而不是自己解析 jsonl：这样校验的是**线上装载路径的真实结果**
    （包括 v2 字段被正确识别、authority 被正确派生），而不是一个平行的解析器。
    """
    from app.rag.corpus import SOURCE_AUTHORITY, load_corpus

    corpus = load_corpus(OUTPUT_DIR)
    total = len(corpus)
    print("=" * 70)
    print(f"v2 语料校验: {OUTPUT_DIR}")
    print("=" * 70)
    if not total:
        print("没有可校验的语料。先跑 wiki / wikisource。")
        return 1

    by_type: dict[str, int] = {}
    by_license: dict[str, int] = {}
    lengths: list[int] = []
    untraceable: list[str] = []
    for entry in corpus.all():
        by_type[entry.source_type] = by_type.get(entry.source_type, 0) + 1
        by_license[entry.license] = by_license.get(entry.license, 0) + 1
        lengths.append(len(entry.text))
        if not entry.is_traceable:
            untraceable.append(entry.doc_id)

    # ── doc_id 唯一性 ───────────────────────────────────────────────────────
    # 这条检查是对一次真实事故的回归：doc_id 生成用了「抽 ASCII 字符」，
    # 中文标题全部退化成同一个前缀，151 条写出、装载时只剩 129 条被静默覆盖。
    # 只报「装载了 N 条」是看不出这个问题的，必须与**文件里的真实行数**对账。
    collisions: dict[str, int] = {}
    written = 0
    for path in sorted(OUTPUT_DIR.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("//"):
                    continue
                written += 1
                try:
                    doc_id = str(json.loads(stripped).get("doc_id") or "")
                except json.JSONDecodeError:
                    continue
                collisions[doc_id] = collisions.get(doc_id, 0) + 1
    duplicated = {key: count for key, count in collisions.items() if count > 1}

    # ── 标记泄漏 ────────────────────────────────────────────────────────────
    # `=== 二 ===` 这类维基标记若残留在正文里，会同时污染两处：
    # BM25 词元（把 `===` 当 token）与**给用户看的引用**（引用里带标记等于毁掉引用）。
    # 实测起因是维基文库的标题与正文同在一行，而最初的实现只认独占一行的标题。
    markup_leaked = [e.doc_id for e in corpus.all() if "==" in e.text or "==" in e.title]

    # ── 校勘记残留 ──────────────────────────────────────────────────────────
    # 剥离后仍出现校勘括号，说明有括号没配平（正则按配对匹配，缺一端就整段残留）。
    # 残留比「没剥离」更危险：无括号的校记文字看起来与正文一样，会被当史料索引。
    #
    # **只检查 ancient_text**：维基条目里 `〈〉` 是合法的书名号嵌套
    # （如《xx〈yy〉》），不是校勘记。第一版检查没区分来源类型，
    # 把 13 张维基卡片误报成残留。
    residual = [
        e.doc_id
        for e in corpus.all()
        if e.source_type == "ancient_text" and any(ch in e.text for ch in "〈〉【】")
    ]

    lengths.sort()
    print(f"\n总条数        {total}")
    print(f"文件行数      {written}" + ("" if written == total else "   <- 与总条数不一致"))
    print(f"语料指纹      {corpus.fingerprint}")
    print(f"可追溯        {total - len(untraceable)}/{total} "
          f"({(total - len(untraceable)) / total:.1%})")
    print(f"正文长度      min={lengths[0]} 中位={lengths[len(lengths) // 2]} "
          f"max={lengths[-1]} 合计={sum(lengths)} 字")

    print("\n-- 来源类型（authority 由它派生）--")
    for key, count in sorted(by_type.items(), key=lambda item: -item[1]):
        print(f"  {count:>4}  {key:<18} authority={SOURCE_AUTHORITY.get(key, 0):.2f}")

    print("\n-- 授权 --")
    for key, count in sorted(by_license.items(), key=lambda item: -item[1]):
        print(f"  {count:>4}  {key}")

    failed = False
    if duplicated:
        lost = sum(count - 1 for count in duplicated.values())
        print(f"\n[FAIL] 有 {len(duplicated)} 个 doc_id 重复，共 **{lost} 条会被静默覆盖**：")
        for doc_id, count in sorted(duplicated.items(), key=lambda item: -item[1])[:10]:
            print(f"    - {doc_id} x{count}")
        print("       原因通常是 doc_id 生成依赖了会被归一化掉的字符（如中文→空串）。")
        failed = True
    elif written != total:
        print(f"\n[FAIL] 文件里有 {written} 行，装载只有 {total} 条，差 {written - total} 条。")
        failed = True

    if markup_leaked:
        print(f"\n[FAIL] 有 {len(markup_leaked)} 条正文/标题残留维基标记（`==`）:")
        for doc_id in markup_leaked[:5]:
            entry = corpus.get(doc_id)
            print(f"    - {doc_id}: {entry.title[:30]} | {entry.text[:40]}")
        failed = True

    if residual:
        print(f"\n[FAIL] 有 {len(residual)} 条残留校勘括号（〈〉【】）:")
        for doc_id in residual[:5]:
            entry = corpus.get(doc_id)
            print(f"    - {doc_id}: {entry.text[:50]}")
        failed = True

    if untraceable:
        print(f"\n[FAIL] 有 {len(untraceable)} 条不可追溯（不能作为事实依据）:")
        for doc_id in untraceable[:10]:
            entry = corpus.get(doc_id)
            citation = entry.citation if entry else None
            print(f"    - {doc_id}: url={bool(citation and citation.url)} "
                  f"accessed={bool(citation and citation.accessed)}")
        failed = True

    if failed:
        return 1

    print("\n[PASS] 全部条目可追溯、字段完整、doc_id 无冲突。")
    return 0


def main() -> int:
    import asyncio

    parser = argparse.ArgumentParser(description="语料采集器（v2）")
    parser.add_argument(
        "command",
        choices=["probe", "wiki", "wikisource", "all", "check"],
        help="要执行的采集动作（check = 校验已采集产出的字段完整度与可追溯率）",
    )
    args = parser.parse_args()

    if args.command == "probe":
        return asyncio.run(cmd_probe())
    if args.command == "wiki":
        return asyncio.run(cmd_wiki())
    if args.command == "wikisource":
        return asyncio.run(cmd_wikisource())
    if args.command == "check":
        return cmd_check()

    async def _all() -> int:
        await cmd_wiki()
        await cmd_wikisource()
        return 0

    return asyncio.run(_all())


if __name__ == "__main__":
    raise SystemExit(main())
