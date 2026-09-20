"""领域问答：检索 → 生成 → **代码校验引用** → 返回。

为什么不直接让模型标引用
--------------------------
让模型自己输出 `doc_id` 是不可靠的：它可能编造一个看起来合理的 id，也可能把 A 段
的内容归给 B 段。而这类错误**在界面上完全看不出来** —— 用户点开引用还能看到一段话，
只是那段话并不支持那句结论。这比「没有引用」更危险，因为它看起来是对的。

所以这里给模型的不是 doc_id，而是编号 `[1]..[N]`：

    检索出候选（代码定） → 把编号和正文一起给模型 → 模型在编号内作答 →
    代码把答案里的 [n] 映射回 doc_id

**模型没有机会引用它没见过的东西**：它只拿到 [1]..[N]，映射表在代码手里。
引用的**显示内容**（出处、原文、链接）也不经过模型，直接取自语料条目。

与「不编造」有关的三条硬约束
----------------------------
1. 检索不到可依据的条目 → 直接拒绝作答，并说明「语料里没有相关记载」。
   绝不用模型的通用知识补一个听起来合理的答案。
2. 只使用 `is_evidential` 为真的条目：自撰内容（`project_doc`）与来源不明的
   （`unknown`）**一律排除**。它们可以供图，不能当依据。
3. 模型给出的 caveat / 争议必须保留原文意思，不得在展示时抹掉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.models.llm import Message
from app.models.registry import registry
from app.rag.corpus import NON_EVIDENTIAL_TYPES
from app.rag.store import RetrievedChunk, get_retriever

logger = get_logger("app.qa.answerer")

SYSTEM = """你是三星堆考古领域的**问答助手**。

你只能使用用户提供的、带编号的史料片段作答。纪律：
1. **不得引入片段之外的任何事实**。片段里没有的，就不要写。
2. 每个有事实依据的句子后面标上来源编号，形如 [1]、[2]。
   依据来自多个片段时写 [1][3]。
3. 如果片段不足以回答，就把 `answerable` 设为 false，
   并在 `answer` 里说明「片段没有提供相关信息」。
   **不要**用你自己的知识补一个听起来合理的答案 —— 那是最严重的错误。
4. 若片段之间存在分歧或学界尚无定论，必须在 caveats 里说明。
5. 只输出 JSON。

关于 `answerable`：它必须是布尔值，表示「**仅凭这些片段**能否回答问题」。
片段只是话题相近但答不上来，也要填 false。宁可说不知道，也不要给一个
没有依据的答案 —— 用户看到的每个事实都应当能点开溯源。"""

TEMPLATE = """【用户问题】
{question}

【可用史料片段】
{documents}

请输出 JSON：
{{
  "answerable": <true|false，仅凭以上片段能否回答问题>,
  "answer": "<回答正文，每个有依据的句子后标 [n]；无法回答时说明原因>",
  "caveats": ["<1-3 条争议、证据不足或需注意之处；没有就给空数组>"],
  "confidence": "<high|medium|low，依据片段对问题的覆盖程度>"
}}"""

# 答案正文里的引用标记
CITATION_RE = re.compile(r"\[(\d+)\]")

# 低于这个融合分就认为「没检索到可用依据」。
# 取一个很宽松的值：宁可多答一次（带 caveats），也不要因为阈值过严而把
# 明明有的记载判成「没有」。真要收紧应该在评估集上调，而不是拍脑袋。
MIN_EVIDENCE_SCORE = 0.02


@dataclass(slots=True)
class AnswerResult:
    question: str
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    confidence: str = "low"
    refused: bool = False
    evidence_count: int = 0
    model: str = ""
    degraded: bool = False
    retrieval_mode: str = "llm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "citations": self.citations,
            "caveats": self.caveats,
            "confidence": self.confidence,
            "refused": self.refused,
            "evidence_count": self.evidence_count,
            "model": self.model,
            "degraded": self.degraded,
            "retrieval_mode": self.retrieval_mode,
        }


def _citation_payload(index: int, chunk: RetrievedChunk) -> dict[str, Any]:
    """引用的展示内容**直接取自语料条目**，不经过模型。

    这是刻意的：模型可以决定「用哪一条」，但决定不了「这一条长什么样」。
    出处、原文、链接都必须是语料里的原值，否则引用就不再是引用。
    """
    citation = chunk.citation
    return {
        "index": index,
        "doc_id": chunk.doc_id,
        "title": chunk.title,
        "source": chunk.display_source,
        "source_type": chunk.source_type,
        "authority": round(chunk.authority, 4),
        "license": chunk.license,
        "url": citation.url,
        "locator": citation.locator,
        "quote": chunk.quote or chunk.text,
        # 加工说明随引用一并展示。古籍的「校勘注已剥离」这类信息
        # 不写进引用，读者就会把整理本当成某个版本的原文。
        "note": chunk.note,
        "score": round(chunk.final_score, 4),
        "channels": list(chunk.channels),
    }


def _build_documents(chunks: list[RetrievedChunk]) -> str:
    return "\n\n".join(
        f"[{index}] 《{chunk.title}》\n出处：{chunk.display_source}"
        f"（类型 {chunk.source_type}，可信度 {chunk.authority:.2f}）\n{chunk.text}"
        for index, chunk in enumerate(chunks, start=1)
    )


def _extract_indices(answer: str, limit: int) -> list[int]:
    """从答案正文里抽出引用编号。

    **以答案正文为准**，而不是以模型另外返回的 used 列表为准 ——
    两者不一致时（模型说用了 [1][3] 但正文只标了 [1]），应当按照正文里
    实际出现的标记来展示引用，否则会出现「列了一条引用，但正文没标它」的错位。
    """
    found = [int(matched) for matched in CITATION_RE.findall(answer)]
    return sorted({index for index in found if 1 <= index <= limit})


async def answer(
    question: str,
    *,
    top_n: int | None = None,
    retriever: Any = None,
) -> AnswerResult:
    """回答一个领域问题，返回带引用的答案。

    `retriever` 可注入：单测要能在**不碰数据库与网络**的前提下验证
    「自撰内容不作依据」「引用取自语料条目」这些契约，
    否则每个用例都得依赖全局检索器（而它连着 pgvector）。
    """
    top_n = top_n or settings.qa_top_n
    retriever = retriever if retriever is not None else await get_retriever()

    # 只保留可当依据的条目：自撰与来源不明的一律排除
    chunks, _ = await retriever.search(
        question,
        top_n=top_n,
        rerank=True,
        filters={"exclude_source_types": sorted(NON_EVIDENTIAL_TYPES)},
    )
    usable = [chunk for chunk in chunks if chunk.final_score >= MIN_EVIDENCE_SCORE]

    if not usable:
        # 检索不到可依据的记载就直说。用模型的通用知识补一个「听起来合理」的
        # 答案，正是这套系统要消灭的行为 —— 它会让编造看起来像有出处。
        metrics.inc("sxd_qa_refused_total")
        return AnswerResult(
            question=question,
            answer=(
                "语料里没有找到与这个问题相关的可靠记载，因此我不能回答它 —— "
                "给你一个听起来合理但没有出处的答案，比不回答更糟。"
                "可以换个说法再问，或者问某个具体文物/遗址/时期。"
            ),
            refused=True,
            confidence="low",
            evidence_count=0,
        )

    documents = _build_documents(usable)

    if not registry.enabled:
        # 无 Key 时不生成答案，直接把检索到的原文给出去。
        # 宁可给一堆原文片段，也不让模型在没依据的情况下组织语言。
        return AnswerResult(
            question=question,
            answer=_extractive_fallback(usable),
            citations=[_citation_payload(i, c) for i, c in enumerate(usable, start=1)],
            caveats=["未配置模型 Key，以下为检索到的原文片段，未经组织"],
            confidence="low",
            evidence_count=len(usable),
            retrieval_mode="extractive",
            degraded=True,
        )

    messages: list[Message] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": TEMPLATE.format(question=question, documents=documents)},
    ]

    payload, result = await registry.call_json(
        "qa",
        messages,
        # `answerable` 是必填项：靠读自然语言去猜「它是不是在拒绝」不可靠，
        # 实测模型会正确写出「片段没有提供相关信息」，但那句话是自由文本 ——
        # 而 `refused` 是前端用来区分「回答了」与「明确拒绝」的唯一依据。
        # 缺了它，拒绝会被渲染成一个正常回答。
        required_keys=("answer", "answerable"),
        temperature=0.2,
        max_tokens=1600,
        tag="qa",
    )

    answer_text = str(payload.get("answer") or "").strip()
    caveats = [str(item) for item in (payload.get("caveats") or []) if str(item).strip()]
    confidence = str(payload.get("confidence") or "medium").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"

    # 模型判定「仅凭片段答不上来」。此时**不附带任何引用** ——
    # 拒绝回答却挂着一堆出处，会让人以为那些出处支持了什么结论。
    if not _as_bool(payload.get("answerable"), default=True):
        metrics.inc("sxd_qa_refused_total")
        return AnswerResult(
            question=question,
            answer=answer_text or "检索到的片段不足以回答这个问题。",
            caveats=caveats,
            confidence="low",
            refused=True,
            evidence_count=len(usable),
            model=result.model,
            degraded=result.degraded,
            retrieval_mode="llm",
        )

    cited = _extract_indices(answer_text, len(usable))

    # 模型可能一个标记都没写。这时**不猜它用了哪些** ——
    # 没有标记就是没有依据，退化为只给原文片段并说明情况。
    if not cited:
        cited = list(range(1, len(usable) + 1))
        caveats.insert(0, "回答未能标注具体依据，以下列出本次检索到的全部片段供核对")

    return AnswerResult(
        question=question,
        answer=answer_text,
        citations=[_citation_payload(i, usable[i - 1]) for i in cited],
        caveats=caveats,
        confidence=confidence,
        evidence_count=len(usable),
        model=result.model,
        degraded=result.degraded,
        retrieval_mode="llm",
    )


def _as_bool(value: Any, *, default: bool) -> bool:
    """宽容地解析布尔值。

    模型可能返回 `true` / `"true"` / `"是"` / `"yes"`。解析失败时用 `default`，
    且默认值要选**保守的那一侧** —— 这里默认 True（当作可回答），
    因为把「其实能答」误判成拒绝，会让用户以为语料里没这条记载。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "是", "能", "1"}:
            return True
        if lowered in {"false", "no", "否", "不能", "0"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _extractive_fallback(chunks: list[RetrievedChunk]) -> str:
    """抽取式兜底：只给原文，不做任何组织。"""
    parts = []
    for index, chunk in enumerate(chunks[:4], start=1):
        text = chunk.text[:300]
        parts.append(f"[{index}] 《{chunk.title}》{chunk.display_source}：{text}")
    return "\n\n".join(parts)


__all__ = ["AnswerResult", "answer"]
