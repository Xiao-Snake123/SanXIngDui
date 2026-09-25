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
1. 检索不到可依据的条目 → 用「大祭司」人设自然说明缺了哪类史料，**绝不**用模型的
   通用知识补一个听起来合理的答案（详见 answer() 里的 intent="refuse" 分支）。
   绝不用模型的通用知识补一个听起来合理的答案。
2. 只使用 `is_evidential` 为真的条目：自撰内容（`project_doc`）与来源不明的
   （`unknown`）**一律排除**。它们可以供图，不能当依据。
3. 模型给出的 caveat / 争议必须保留原文意思，不得在展示时抹掉。
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.models.llm import Message, _loads_lenient, _validate  # noqa: PLC2701
from app.models.registry import registry
from app.rag.corpus import NON_EVIDENTIAL_TYPES
from app.rag.store import RetrievedChunk, get_retriever

logger = get_logger("app.qa.answerer")

SYSTEM = """你是「古蜀大祭司」，三星堆问答助手。请以大祭司的口吻与用户对话——带古意但清晰易懂。

先判断用户这条消息的意图，再按对应规则作答，只输出 JSON：

1. 闲聊 / 问候 / 问你是谁 / 致谢 等不涉及史料考据的消息：
   - 直接用大祭司人设自然地回，像在对话，不要查、不要引用、不要输出编号。
   - 这类消息不需要史料，凭你的角色设定回应即可（是角色扮演，不是编造史实）。
   - intent 设为 "chat"。

2. 史实 / 文物 / 考古类问题：
   - 只能使用你提供的、带编号的史料片段作答，每个有依据的句子后标 [n]。
   - 若片段足以回答 → intent 设为 "answer"。
   - 若片段不足以回答（哪怕话题相近）→ intent 设为 "refuse"，并**用人设口吻说明**
     缺了哪类史料、建议换个问法；绝不用你的通用知识补一个看似合理的答案。

3. 不得引入片段之外的任何事实；片段里没有的就不要写。
4. 片段间有分歧或学界尚无定论，必须在 caveats 说明。
5. 只输出 JSON。

字段说明：
- answer：回答正文（chat 时是对话式口吻；refuse 时是人设化说明；answer 时带 [n] 引用）。
- intent："chat" | "answer" | "refuse"。
- answerable：布尔，表示「仅凭片段能否回答史实问题」。闲聊也填 true。
- caveats：1-3 条争议 / 证据不足 / 需注意之处；没有给空数组。
- confidence："high" | "medium" | "low"，依据片段对问题的覆盖程度。"""

TEMPLATE = """【用户问题】
{question}

【可用史料片段】
{documents}

若上方片段为空，说明未检索到相关史料：史实类问题请按 refuse 处理，闲聊则正常应答。
请输出 JSON：
{{
  "intent": "<chat|answer|refuse>",
  "answerable": <true|false，仅凭以上片段能否回答史实问题>,
  "answer": "<回答正文，每个有依据的句子后标 [n]；闲聊/拒绝时按对应口吻写>",
  "caveats": ["<争议、证据不足或需注意之处；没有就给空数组>"],
  "confidence": "<high|medium|low>"
}}"""

# 各降级场景的最小兜底文案（常态由模型生成，不靠这些）。
_NO_KEY_STATIC = "未配置模型 Key，暂无法作答。请稍后再试，或直接描述想复原的场景。"
_CHAT_FALLBACK = "吾乃古蜀大祭司。有疑尽管相询——三星堆之文物史事，凡典籍有载者，吾必据实相告。"
_REFUSE_FALLBACK = "此问典籍与史料中未查得可靠记载，吾不敢妄言。可换个说法再问，或问某件具体文物、遗址、时期。"

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
    intent: str = ""  # chat | answer | refuse，由 answerer LLM 判定

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
            "intent": self.intent,
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


async def _prepare(
    question: str,
    *,
    top_n: int | None,
    retriever: Any,
) -> list[RetrievedChunk]:
    """检索出「可以当依据」的条目：自撰内容与来源不明的一律排除。"""
    top_n = top_n or settings.qa_top_n
    retriever = retriever if retriever is not None else await get_retriever()
    chunks, _ = await retriever.search(
        question,
        top_n=top_n,
        rerank=True,
        filters={"exclude_source_types": sorted(NON_EVIDENTIAL_TYPES)},
    )
    return [chunk for chunk in chunks if chunk.final_score >= MIN_EVIDENCE_SCORE]


def _build_messages(
    question: str,
    usable: list[RetrievedChunk],
    *,
    history: list[dict[str, str]] | None,
    user_profile: str | None,
) -> list[Message]:
    """拼提示词：短期记忆（会话历史）与长期记忆（用户画像）一并注入。

    顺序刻意固定为 SYSTEM → 画像 → 历史 → 当前问题。画像是「关于这个人」的
    稳定背景，放在历史之前，才不会被历史轮次挤掉、也不受其顺序影响。
    """
    documents = _build_documents(usable)
    messages: list[Message] = [{"role": "system", "content": SYSTEM}]
    if user_profile:
        messages.append({"role": "system", "content": user_profile})
    if history:
        messages.extend({"role": turn["role"], "content": turn["content"]} for turn in history)
    messages.append(
        {"role": "user", "content": TEMPLATE.format(question=question, documents=documents)}
    )
    return messages


def _no_key_result(question: str, usable: list[RetrievedChunk]) -> AnswerResult:
    """无模型 Key 时的抽取式兜底：只给原文，不做任何组织。"""
    if usable:
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
    return AnswerResult(
        question=question,
        answer=_NO_KEY_STATIC,
        confidence="low",
        evidence_count=0,
        retrieval_mode="llm",
        degraded=True,
    )


def _finalize(
    question: str,
    payload: dict[str, Any],
    usable: list[RetrievedChunk],
    model: str,
    degraded: bool,
) -> AnswerResult:
    """把模型返回的 JSON 落成 AnswerResult（闲聊 / 拒绝 / 正常作答三分支）。"""
    answer_text = str(payload.get("answer") or "").strip()
    intent = str(payload.get("intent") or "").strip().lower()
    caveats = [str(item) for item in (payload.get("caveats") or []) if str(item).strip()]
    confidence = str(payload.get("confidence") or "medium").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    answerable = _as_bool(payload.get("answerable"), default=True)

    # 闲聊：不查史料、不挂引用，直接用人设回。模型此刻可能带了一堆弱相关
    # 片段，但 intent=chat 时应当忽略它们，像真人一样对话。
    if intent == "chat":
        return AnswerResult(
            question=question,
            answer=answer_text or _CHAT_FALLBACK,
            caveats=caveats,
            confidence=confidence or "high",
            evidence_count=len(usable),
            model=model,
            degraded=degraded,
            retrieval_mode="chat",
            intent="chat",
        )

    # 拒绝：片段不足以支撑史实回答。不挂任何引用（拒绝还挂着出处会误导）。
    if intent == "refuse" or (not answerable and intent != "chat"):
        metrics.inc("sxd_qa_refused_total")
        return AnswerResult(
            question=question,
            answer=answer_text or _REFUSE_FALLBACK,
            caveats=caveats,
            confidence="low",
            refused=True,
            evidence_count=len(usable),
            model=model,
            degraded=degraded,
            retrieval_mode="llm",
            intent="refuse",
        )

    # 正常作答：从正文抽取引用编号，映射到语料条目（模型看不到它没见过的 id）。
    cited = _extract_indices(answer_text, len(usable))
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
        model=model,
        degraded=degraded,
        retrieval_mode="llm",
        intent="answer",
    )


_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class _AnswerStreamer:
    """从**尚未写完**的 JSON 里增量抽出某个字符串字段的可见文本。

    JSON 模式下模型是逐 token 吐的，但整份 JSON 要到最后才合法、才能解析。
    若傻等解析完再显示，用户看到的就是「等几秒，整段蹦出来」。
    这里做一个极小的状态机，边收边把目标字段的新增字符解码出来。

    刻意做得保守：解不出来就什么都不吐（宁可少显示，也不要显示错的），
    最终文案一律以收尾时 `AnswerResult.answer` 的权威值为准覆盖。
    """

    def __init__(self, key: str) -> None:
        self._needle = f'"{key}"'
        self._tail = ""
        self._state = "seek"  # seek → colon → quote → string → done
        self._escaping = False
        self._escape = ""

    def feed(self, chunk: str) -> str:
        out: list[str] = []
        for char in chunk:
            if self._state == "seek":
                self._tail += char
                if len(self._tail) > len(self._needle) + 1:
                    self._tail = self._tail[-(len(self._needle) + 1) :]
                if self._needle in self._tail:
                    self._state = "colon"
                    self._tail = ""
            elif self._state == "colon":
                if char == ":":
                    self._state = "quote"
                elif not char.isspace():
                    self._state = "seek"
            elif self._state == "quote":
                if char == '"':
                    self._state = "string"
                elif not char.isspace():
                    self._state = "seek"
            elif self._state == "string":
                if self._escaping:
                    self._escape += char
                    head = self._escape[0]
                    if head in _SIMPLE_ESCAPES:
                        out.append(_SIMPLE_ESCAPES[head])
                        self._escaping, self._escape = False, ""
                    elif head == "u":
                        # \uXXXX：凑满 4 位十六进制才敢解码
                        if len(self._escape) == 5:
                            try:
                                out.append(chr(int(self._escape[1:], 16)))
                            except ValueError:
                                out.append("?")
                            self._escaping, self._escape = False, ""
                    else:
                        out.append(head)
                        self._escaping, self._escape = False, ""
                elif char == "\\":
                    self._escaping, self._escape = True, ""
                elif char == '"':
                    self._state = "done"
                else:
                    out.append(char)
            else:  # done：字段已结束，后续内容一律忽略
                break
        return "".join(out)


async def stream_answer(
    question: str,
    *,
    top_n: int | None = None,
    retriever: Any = None,
    history: list[dict[str, str]] | None = None,
    user_profile: str | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """流式问答：正文边生成边下发，结构化字段在收尾时一次性给出。

    产出若干 `("delta", 片段)`，最后产出 `("result", AnswerResult)`。
    intent / confidence / 引用映射仍以收尾的 AnswerResult 为准——
    它们不参与正文展示，晚一点到达不影响「逐字出现」的观感。

    任何环节失败都整体回退到 `answer()`（非流式），保证答案不丢。
    """
    usable = await _prepare(question, top_n=top_n, retriever=retriever)
    if not registry.enabled:
        result = _no_key_result(question, usable)
        yield ("delta", result.answer)
        yield ("result", result)
        return

    messages = _build_messages(question, usable, history=history, user_profile=user_profile)
    spec = registry.spec("qa")
    client = registry.client_for_model(spec.primary)
    streamer = _AnswerStreamer("answer")
    raw: list[str] = []

    async def _fallback(reason: str) -> AsyncIterator[tuple[str, Any]]:
        logger.warning("问答流式回退非流式: %s", reason)
        result = await answer(
            question, top_n=top_n, retriever=retriever, history=history, user_profile=user_profile
        )
        yield ("delta", result.answer)
        yield ("result", result)

    try:
        async for delta in client.stream_chat(
            messages, temperature=0.3, max_tokens=1600, tag="qa_stream", json_mode=True
        ):
            raw.append(delta)
            piece = streamer.feed(delta)
            if piece:
                yield ("delta", piece)
    except Exception as exc:  # noqa: BLE001 - 流式失败不应让答案丢失
        metrics.inc("sxd_qa_stream_failed_total")
        async for item in _fallback(f"stream:{exc}"):
            yield item
        return

    try:
        payload = _validate(
            _loads_lenient("".join(raw)), required_keys=("answer", "answerable", "intent")
        )
    except Exception as exc:  # noqa: BLE001
        metrics.inc("sxd_qa_stream_parse_failed_total")
        async for item in _fallback(f"parse:{exc}"):
            yield item
        return

    yield ("result", _finalize(question, payload, usable, spec.primary, bool(getattr(client, "degraded", False))))


async def answer(
    question: str,
    *,
    top_n: int | None = None,
    retriever: Any = None,
    history: list[dict[str, str]] | None = None,
    user_profile: str | None = None,
) -> AnswerResult:
    """回答一个领域问题，返回带引用的答案。

    闲聊 / 问候 / 身份 / 致谢等消息也走这里：LLM 在 SYSTEM 提示词里被要求
    先判意图（intent=chat/answer/refuse），闲聊时用人设自然回、不查史料、
    不挂引用；史实问题只依据检索到的片段作答。意图判定取代原来「关键词表
    预判闲聊」的脆弱做法。

    `retriever` 可注入：单测要能在**不碰数据库与网络**的前提下验证
    「自撰内容不作依据」「引用取自语料条目」这些契约，
    否则每个用例都得依赖全局检索器（而它连着 pgvector）。
    """
    usable = await _prepare(question, top_n=top_n, retriever=retriever)

    # 无 Key：抽取式兜底，不做生成。宁可给原文片段也不凭空组织。
    if not registry.enabled:
        return _no_key_result(question, usable)

    messages = _build_messages(question, usable, history=history, user_profile=user_profile)
    payload, result = await registry.call_json(
        "qa",
        messages,
        # intent 是必填项：chat / answer / refuse 三态，由模型判定意图，
        # 取代原来「关键词表预判闲聊」的脆弱做法；refused 仍由 intent=refuse
        # 或 answerable=false 推导，是前端区分「回答了」与「明确拒绝」的依据。
        required_keys=("answer", "answerable", "intent"),
        temperature=0.3,
        max_tokens=1600,
        tag="qa",
    )

    return _finalize(question, payload, usable, result.model, result.degraded)


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


__all__ = ["AnswerResult", "answer", "stream_answer"]
