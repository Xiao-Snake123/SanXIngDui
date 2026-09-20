"""史料语料库（v2）。

数据形态：`backend/data/corpus/*.jsonl`，每行一条史料卡片。

为什么会有 v2：v1 只有 `source: str` 一个自由文本字段（形如「三星堆博物馆馆藏说明」），
既没有作者/出版者/年份/页码，也没有链接，而本文件的 docstring 却承诺
「`doc_id` 可追溯回原始报告页码」—— **承诺无法兑现**。实测 v1 语料 50 条中，
author / publisher / year / page / url 全部缺失，且其中 4 条出自「本项目内容质量规范」、
2 条出自「三星堆主题视觉设计指引」——那不是史料来源，是项目自己的文档。

对一个对外声称「有据可依」的系统来说，「来源字段填了个像来源的字符串」是最危险的状态：
它让审计者以为可追溯，实际追不到任何东西。

字段（v2）：

    doc_id        稳定主键（永不变，用于引用与增量判断）
    title         卡片标题（参与 BM25 索引，权重天然更高）
    text          证据正文（150~400 字，一个语义完整的**证据单元**）
    quote         原文摘录（来源受版权约束时只保留这一段）
    citation      出处 dict：title/author/publisher/year/locator/url/accessed
    source_type   来源类型 —— **决定 authority**，不再手写
    license       授权：public_domain | cc_by_sa | official_public | quote_only | internal | unknown
    object/era/category/tags   检索过滤与展示用元数据

**`authority` 由 `source_type` 派生**（见 `SOURCE_AUTHORITY`）。v1 把它做成手写常量，
结果是 20/50 条写了 1.0（最高可信度），其中包含上面那些自我指涉条目 ——
手写可信度最终一定会退化成「全都 1.0」，等于没有可信度。派生规则可复现、可审计，
且新增来源时只需加一行表。

兼容：v1 条目（无 citation / source_type）仍可装载，按 `unknown` 处理，
但装载时会**汇总警告**，逼迫迁移而不是静默降权 —— 静默降权会让「检索质量变差」
变成一个查不出原因的现象。

规模上支持千级~万级：语料装载 + BM25 建索引在千级规模下 <100ms，
向量通道走批量 + 进程内缓存。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger
from app.rag.text import fold, normalize

logger = get_logger("app.rag.corpus")


# ── 来源类型与可信度 ────────────────────────────────────────────────────────
# authority 的**单一事实源**。改这里就等于改所有条目的可信度，不需要碰 jsonl。
#
# 取值理由：考古场景下「一手程度」是可信度的主要决定因素 ——
# 发掘报告是实物与地层的一手记录，二手汇编（维基）只能当线索。
SOURCE_AUTHORITY: dict[str, float] = {
    "excavation_report": 1.00,   # 正式发掘报告、考古简报（一手）
    "museum_official": 0.95,     # 博物馆官方藏品说明、展陈说明
    "ancient_text": 0.90,        # 公版古籍原文（《华阳国志》《山海经》）
    "academic_paper": 0.85,      # 同行评审论文、研究专著
    "encyclopedia": 0.70,        # 维基百科等二手汇编（CC BY-SA，须署名）
    "news": 0.50,                # 媒体报道、纪录片文案（仅作背景）
    "project_doc": 0.30,         # **本项目自撰内容** —— 禁止作为史料依据
    "unknown": 0.50,             # v1 迁移残留：来源不明，按「不可信也不可弃」处理
}

# 问答链路**强制排除**的类型：自撰内容不能当史料，来源不明的不能当依据。
# 检索供图（复原任务）不受此限制 —— 那时自撰内容作为风格提示是可接受的。
NON_EVIDENTIAL_TYPES: frozenset[str] = frozenset({"project_doc", "unknown"})

# 授权取值。`quote_only` 表示只允许保留引文片段，不得存全文。
LICENSES: frozenset[str] = frozenset(
    {"public_domain", "cc_by_sa", "official_public", "quote_only", "internal", "unknown"}
)


def authority_for(source_type: str) -> float:
    """按来源类型派生可信度。未知类型按 `unknown`（0.50）处理，不放行也不抹杀。"""
    return SOURCE_AUTHORITY.get(str(source_type or "").strip(), SOURCE_AUTHORITY["unknown"])


@dataclass(slots=True)
class Citation:
    """出处。**这是「可追溯」的载体，不是装饰性字段。**

    判定「可追溯」的标准刻意分两种，因为纸质文献与网络来源的追溯方式不同：

    - 纸质（发掘报告/专著）：需要 author + publisher + year，且**必须有 locator**（页码）。
      没有页码就只能追到整本书，对「某句话说错了」这种审计没有用。
    - 网络（官网/维基）：需要 url + accessed（抓取日期）。
      网址会变，抓取日期让引用可复核。
    """

    title: str = ""
    author: str = ""
    publisher: str = ""
    year: str = ""
    locator: str = ""
    url: str = ""
    accessed: str = ""

    def is_traceable(self) -> bool:
        if self.url:
            return bool(self.title and self.accessed)
        return bool(self.title and self.author and self.publisher and self.year and self.locator)

    def as_text(self) -> str:
        """给人看的短引用，用于卡片副标题与答案引用块。"""
        if self.url:
            parts = [part for part in (self.author, self.title) if part]
            head = "，".join(parts) or self.title or self.url
            tail = f"（抓取于 {self.accessed}）" if self.accessed else ""
            return f"{head}{tail}"
        parts = [part for part in (self.author, self.title, self.publisher, self.year) if part]
        text = "，".join(parts)
        if self.locator:
            text = f"{text}：{self.locator}" if text else self.locator
        return text

    @classmethod
    def from_dict(cls, payload: dict | None) -> "Citation":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            title=str(payload.get("title") or ""),
            author=str(payload.get("author") or ""),
            publisher=str(payload.get("publisher") or ""),
            year=str(payload.get("year") or ""),
            locator=str(payload.get("locator") or ""),
            url=str(payload.get("url") or ""),
            accessed=str(payload.get("accessed") or ""),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "author": self.author,
            "publisher": self.publisher,
            "year": self.year,
            "locator": self.locator,
            "url": self.url,
            "accessed": self.accessed,
        }


@dataclass(slots=True)
class CorpusEntry:
    doc_id: str
    title: str
    text: str
    object: str = ""
    era: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    # `source` 是 v1 遗留的自由文本标签。v2 一律用 `citation`；
    # 保留它是为了不必一次性重写所有 jsonl，展示时由 display_source 优先取 citation。
    source: str = ""
    quote: str = ""
    citation: Citation = field(default_factory=Citation)
    source_type: str = "unknown"
    license: str = "unknown"
    # 本条目的加工说明。用于记录「正文经过了什么处理」——
    # 这类信息如果只写在采集脚本的注释里，看卡片的人（和将来读语料的人）
    # 会以为 `text` 就是原文。实测最需要用它的地方：维基文库的古籍原文
    # 内联了大量校勘注（〈…〉占 57%），本语料已剥离，正文因此是「通行读本」
    # 而非任何一个具体版本。不说明这一点，引用会被误读为「某版本原文如此」。
    note: str = ""
    image_refs: list[str] = field(default_factory=list)

    # ── 派生 ────────────────────────────────────────────────────────────────
    @property
    def authority(self) -> float:
        """可信度**由 source_type 派生**，不接受手写值。

        做成 property 而不是字段，是为了让「手写一个 1.0」在语法上就不可能：
        旧语料里 20/50 条写了 1.0（含自我指涉条目），就是手写字段的必然结局。
        """
        return authority_for(self.source_type)

    @property
    def display_source(self) -> str:
        """展示/注入 prompt 用的出处文字。有结构化引用时优先，否则退回 v1 标签。"""
        if self.citation and (self.citation.title or self.citation.url):
            return self.citation.as_text()
        return self.source

    @property
    def is_evidential(self) -> bool:
        """能否作为「事实依据」用于问答。自撰与来源不明的都不行。"""
        return self.source_type not in NON_EVIDENTIAL_TYPES

    @property
    def is_traceable(self) -> bool:
        return self.citation.is_traceable()

    @property
    def searchable_text(self) -> str:
        """BM25 索引字段：标题与标签重复一次，抬高其权重。

        刻意**不**把 citation 纳入索引：出处是用来「审计」的，不是用来「召回」的。
        把「文物出版社」这类词混进索引会稀释真正的领域术语。

        **必须折叠繁简**：这个属性同时是 BM25 的索引文本**和**向量通道嵌的文本
        （`storage/vectors.py` 里两个索引实现都嵌入 `entry.searchable_text`）。
        若只折叠分词器而不折叠这里，会出现「BM25 修好了、向量通道仍在嵌繁体」
        的半修状态。折叠进这里还有一个直接后果：`content_hash` 随之改变，
        于是向量会**全量重算** —— 这正是应该发生的，因为检索表示变了。

        `normalize()` 保持不变（它服务于展示路径），折叠由 `fold()` 单独承担。
        """
        parts = [
            self.title,
            self.title,
            self.object,
            self.era,
            self.category,
            " ".join(self.tags),
        ]
        body = normalize(" ".join(part for part in parts if part)) + " " + normalize(self.text)
        return fold(body)

    @property
    def content_hash(self) -> str:
        """内容指纹：用来判断「库里那批向量还算不算数」。

        指纹覆盖 `searchable_text`（也就是真正被向量化的那份文本），
        而不是原始 text —— 如果标题/标签改了，向量同样需要重算。

        为什么必须有它：`doc_id` 是**稳定主键**（要能追溯回报告页码），
        所以修史料时人们会保留 id 只改正文。若复用判断只看 id，
        改过的条目会继续用旧向量，检索结果静默失真。
        """
        return hashlib.blake2b(self.searchable_text.encode("utf-8"), digest_size=16).hexdigest()

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "text": self.text,
            "quote": self.quote,
            "object": self.object,
            "era": self.era,
            "category": self.category,
            "tags": self.tags,
            "source": self.source,
            "citation": self.citation.to_dict(),
            "source_type": self.source_type,
            "authority": self.authority,
            "license": self.license,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "CorpusEntry":
        return cls(
            doc_id=str(payload.get("doc_id") or payload.get("id") or ""),
            title=str(payload.get("title") or ""),
            text=str(payload.get("text") or ""),
            object=str(payload.get("object") or ""),
            era=str(payload.get("era") or ""),
            category=str(payload.get("category") or ""),
            tags=[str(tag) for tag in (payload.get("tags") or [])],
            source=str(payload.get("source") or ""),
            quote=str(payload.get("quote") or ""),
            citation=Citation.from_dict(payload.get("citation")),
            source_type=str(payload.get("source_type") or "unknown").strip(),
            license=str(payload.get("license") or "unknown").strip(),
            note=str(payload.get("note") or ""),
            image_refs=[str(item) for item in (payload.get("image_refs") or [])],
        )


@dataclass
class Corpus:
    entries: dict[str, CorpusEntry] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    # 装载期的质量信号，供 /api/corpus 如实暴露（而不是只报「50 条」）
    legacy_without_source_type: int = 0
    untraceable: int = 0
    handwritten_authority: int = 0

    def __len__(self) -> int:
        return len(self.entries)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def get(self, doc_id: str) -> CorpusEntry | None:
        return self.entries.get(doc_id)

    def all(self) -> list[CorpusEntry]:
        return list(self.entries.values())

    @property
    def fingerprint(self) -> str:
        """语料整体指纹：**答案基于哪一版语料**的可复核凭证。

        只用 (doc_id, content_hash) 排序后求哈希，所以与装载顺序、文件名无关，
        只随内容变化。对外暴露它的意义是：当有人质疑某个历史回答时，
        能确认「当时用的是哪一版语料」，而不是只能回答「大概是那阵子的」。
        """
        digest = hashlib.blake2b(digest_size=16)
        for doc_id, content_hash in sorted(
            (entry.doc_id, entry.content_hash) for entry in self.entries.values()
        ):
            digest.update(doc_id.encode("utf-8"))
            digest.update(content_hash.encode("utf-8"))
        return digest.hexdigest()

    def facets(self) -> dict[str, list[str]]:
        """给前端做筛选器用的字段取值集合。"""
        return {
            "objects": sorted({entry.object for entry in self.entries.values() if entry.object}),
            "eras": sorted({entry.era for entry in self.entries.values() if entry.era}),
            "categories": sorted(
                {entry.category for entry in self.entries.values() if entry.category}
            ),
            "source_types": sorted(
                {entry.source_type for entry in self.entries.values() if entry.source_type}
            ),
            "tags": sorted({tag for entry in self.entries.values() for tag in entry.tags}),
        }


def load_corpus(directory: Path) -> Corpus:
    corpus = Corpus()
    if not directory.exists():
        logger.warning("语料目录不存在: %s（检索将返回空证据）", directory)
        return corpus

    for path in sorted(directory.glob("*.jsonl")):
        loaded = 0
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("//"):
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    logger.warning("%s:%d JSON 解析失败: %s", path.name, line_no, exc)
                    continue
                entry = CorpusEntry.from_dict(payload)
                if not entry.doc_id or not entry.text:
                    continue
                if entry.doc_id in corpus.entries:
                    entry.doc_id = f"{entry.doc_id}#{path.stem}"
                corpus.entries[entry.doc_id] = entry
                loaded += 1

                # 质量信号：只统计、不在这里刷日志（50 条会刷 50 行），最后汇总一次
                if "source_type" not in payload:
                    corpus.legacy_without_source_type += 1
                if not entry.is_traceable:
                    corpus.untraceable += 1
                # v1 手写的 authority 与派生值不一致 → 说明它曾经是「拍出来的」
                if "authority" in payload and abs(float(payload["authority"] or 0) - entry.authority) > 1e-6:
                    corpus.handwritten_authority += 1

        corpus.files.append(path.name)
        logger.info("装载史料 %s: %d 条", path.name, loaded)

    logger.info("语料库就绪: %d 条，来自 %d 个文件", len(corpus), len(corpus.files))

    # 汇总警告：迁移期间必须看得见，否则「检索质量下降」会变成查不出原因的现象
    if corpus.legacy_without_source_type:
        logger.warning(
            "有 %d 条史料没有 source_type（按 unknown=%.2f 处理，问答链路会排除它们）。"
            "请迁移到 v2 格式，见 docs/RAG_PLAN.md §2",
            corpus.legacy_without_source_type,
            SOURCE_AUTHORITY["unknown"],
        )
    if corpus.untraceable:
        logger.warning(
            "有 %d/%d 条史料**不可追溯**（缺少 url+抓取日期，或缺少 作者+出版者+年份+页码）。"
            "不可追溯的条目不能作为事实依据，见 docs/RAG_PLAN.md §2.1",
            corpus.untraceable,
            len(corpus),
        )
    return corpus


__all__ = [
    "Citation",
    "Corpus",
    "CorpusEntry",
    "LICENSES",
    "NON_EVIDENTIAL_TYPES",
    "SOURCE_AUTHORITY",
    "authority_for",
    "load_corpus",
]
