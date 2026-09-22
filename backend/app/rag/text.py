"""中文友好的轻量分词与归一化。

为什么不用 jieba：本项目检索语料是**领域专名密集**的考古文本（"青铜纵目面具"、
"鱼凫王"、"分铸法"），通用分词器的词典里未必有这些词，切错反而伤害召回。
改用**字级 unigram + bigram** 组合：中文里绝大多数领域术语是 2~4 字，
bigram 能天然覆盖（"纵目"、"神树"、"金杖"），且零依赖、零词典加载耗时。

繁简折叠（`fold`）
------------------
本项目的语料里有 83% 含繁体字（《華陽國志》等古籍原文，以及维基条目引文中保留的
繁体人名地名）。而 BM25 是**字级**的：「蠶叢」与「蚕丛」是完全不同的 token，
永远不会匹配。实测后果很严重 —— 查询「蚕丛 纵目」时，《華陽國志》里
「有蜀侯蠶叢，其目縱」这条对本项目最关键的记载，**一条都召不回来**：
古籍占语料 65%，却贡献 0 召回。

三条设计决定：

1. **方向是 繁→简，不是 简→繁。**
   繁→简是多对一（蠶→蚕），信息无损；
   简→繁是一对多（发→發/髮），有歧义、会改错字。而用户几乎只输入简体。

2. **检索层与数据层是两件事，两条都要说清楚。**

   - **检索层**：`fold()` 只作用于分词与索引文本（`tokenize` / `ngrams` /
     `searchable_text`），**不碰 `text`** —— 这样"检索时繁简互通"和
     "展示时保持原文字形"可以并存。`normalize()` 同样保持不变，理由见下。

   - **数据层（已变更）**：早期纪律是「古籍字形本身就是史料信息，采集时不转简体」，
     所以 `data/corpus/v2/wikisource.jsonl` 长期保留繁体。后来使用者明确要求
     **知识库只保留简体中文、不要繁体与外文**，这条纪律已被覆盖 ——
     `scripts/to_simplified.py` 与 `scripts/fix_raw_data.py` 把文档和卡片
     （**含古籍**）统一转为简体，并在每张古籍卡片的 `note` 里写明
     「已由繁体转为简体，与维基文库原文字形不一致，回查以 url 为准」。

   ⚠️ **代码注释必须与磁盘上的数据说同一件事**：若哪天改回"古籍保留繁体"，
   请同步改这里，否则下一个人读到这段注释会以为数据被误改了。

3. **依赖 zhconv 而不是自建映射表。**
   实测语料涉及 **717 个不同繁体字**且随采集继续增长。自建表有两个问题：
   我无法凭记忆可靠写出 717 条映射（写错就是伪造数据）；
   而将来新增语料出现未收录的字时，表现为**静默漏匹配**、不报错 ——
   正是本项目最忌讳的失败形态。zhconv 是 572 KB 纯 Python，无需编译。
   （官方 `OpenCC` 包需要 C++ 编译，本机没有编译器，装不上。）

   折叠不可用时**降级但不隐藏**：`fold()` 原样返回，`FOLD_BACKEND` 变为
   `disabled`，并通过 `/api/corpus` 暴露 —— 否则「繁简匹配又失效了」会变成
   一个查不出原因的现象。
"""

from __future__ import annotations

import re
import unicodedata

from app.core.logging import get_logger

logger = get_logger("app.rag.text")

try:
    from zhconv import convert as _zh_convert

    FOLD_BACKEND = "zhconv"
except Exception as _exc:  # noqa: BLE001 - 折叠是增强，不是硬依赖
    _zh_convert = None
    FOLD_BACKEND = "disabled"
    logger.warning(
        "zhconv 不可用（%s），繁简折叠已关闭：繁体语料将无法被简体查询召回。"
        "修复：pip install zhconv",
        _exc,
    )

_CJK = r"\u4e00-\u9fff\u3400-\u4dbf"
_CJK_RE = re.compile(f"[{_CJK}]+")
_LATIN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9+._-]*")

# 检索场景下的高频虚词，对区分度无贡献
_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "及", "或", "被", "把", "对", "为",
    "以", "于", "而", "其", "之", "等", "有", "中", "上", "下", "个", "这",
    "那", "一个", "以及", "并且", "因此", "但是", "the", "a", "an", "of", "and", "to", "in",
}


def fold(text: str) -> str:
    """繁→简折叠，用于**检索**（不用于展示）。

    幂等：已经全简体的文本调用它不会有变化，所以可以安全地在多处重复调用。

    不可用时原样返回并已在导入期告警 —— 静默失败比失败更糟，见模块 docstring。
    """
    if not text or _zh_convert is None:
        return text
    return _zh_convert(text, "zh-cn")


def normalize(text: str) -> str:
    """全角转半角 + 去除多余空白，保证「( )」与「( )」检索一致。

    **刻意不做繁简折叠。** 这个函数同时服务于展示路径 ——
    `split_sentences()` 用它切出「原文引用句」，而那些句子会进 `key_facts`、
    注入 prompt、也可能直接显示给用户。在这里折叠会让《華陽國志》的引文
    变成简体，而用户看到的出处仍是「《華陽國志》」—— 等于**篡改引文**。
    折叠要发生就发生在检索层（`tokenize` / `ngrams` / `searchable_text`）。
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """返回用于 BM25/哈希向量的词元序列。

    索引侧与查询侧都经过这里，所以繁简折叠放在这一层，
    意味着「蠶叢」与「蚕丛」会落到同一个 token —— 这是修复召回的关键一步。
    """
    # 先折叠再归一化：fold 处理的是汉字，normalize 处理的是全角/空白，两者互不干扰。
    normalized = normalize(fold(text)).lower()
    if not normalized:
        return []

    tokens: list[str] = []

    for block in _CJK_RE.findall(normalized):
        tokens.extend(block)  # unigram：保证单字召回
        tokens.extend(block[i : i + 2] for i in range(len(block) - 1))  # bigram：领域专名主力

    tokens.extend(match for match in _LATIN_RE.findall(normalized) if len(match) > 1)

    if keep_stopwords:
        return tokens
    return [token for token in tokens if token not in _STOPWORDS]


def ngrams(text: str, size: int = 3) -> list[str]:
    """用于哈希向量的字符 n-gram（比 unigram 更抗噪）。

    同样折叠：向量通道嵌的是 `CorpusEntry.searchable_text`，
    若这里不折叠，会出现「BM25 修好了、向量通道仍然匹配不上繁体」的半修状态。
    """
    normalized = normalize(fold(text)).lower().replace(" ", "")
    if len(normalized) < size:
        return [normalized] if normalized else []
    return [normalized[i : i + size] for i in range(len(normalized) - size + 1)]


def split_sentences(text: str) -> list[str]:
    """中文断句，用于抽出可引用的证据句。"""
    normalized = normalize(text)
    if not normalized:
        return []
    parts = re.split(r"(?<=[。！？!?；;\n])", normalized)
    return [part.strip() for part in parts if part and part.strip()]


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
