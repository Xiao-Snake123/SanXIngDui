"""科普文案 Worker。

三个设计要点：

1. **流式输出**：文案是用户唯一会逐字阅读的产物，用 `stream_chat` 把 delta 实时
   推到前端，首字延迟从「等完整篇」变成 300~600ms。SSE 帧类型为 `copy_delta`。

2. **史实约束前置**：system prompt 明确要求「只能使用给定史料」，
   并把检索层的 `cautions`（争议点）作为必须体现的内容 —— 三星堆大量问题尚无定论，
   把假说写成定论是这个项目最不可接受的错误类型（比画面跑偏更严重）。

3. **不掩饰降级**：质检未通过时，文案里会明确写出「本次结果为未达标的初稿」，
   而不是若无其事地宣布成功。可信度优先于观感。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.core.tracing import TraceRecorder
from app.models.llm import Message
from app.models.registry import registry
from app.rag.text import truncate

logger = get_logger("app.agents.copywriting")

SYSTEM = """你是三星堆博物馆的**科普内容主笔**，同时具备考古学素养与面向大众的表达能力。

写作纪律（违反即视为不合格）：
1. 只能使用用户提供的【史料片段】中的事实，不得补充片段之外的具体数字、年代或器物名称；
2. 学术界尚存争议的内容，必须写成「目前学界尚无定论，主流观点认为……」这类表述，
   绝不能写成确定性结论；
3. 文风克制、有画面感，不堆砌形容词，不使用夸张宣传语（如「震惊」「逆天」「震撼全网」）；
4. 直接输出正文，不要标题、不要 markdown 标记、不要任何解释性前后缀。"""

TEMPLATE = """请为下面这张三星堆复原影像撰写一段科普文案。

【复原目标】{goal}
【画面风格】{style_label}
【使用的文物】{artifact}
【质检结论】{qa_summary}

【史料片段】
{evidence}

【必须体现的争议点或证据边界】
{cautions}

写作要求：
- 篇幅 {length} 字左右；
- 面向{audience}，语气{tone}；
- 开篇用一个具体的画面细节切入，不要从「三星堆是……」这种百科式开头起笔；
- 结尾用一句话说明这是 AI 数字复原而非实物照片；
- 若质检结论为未达标，需在结尾如实说明本轮结果仍在迭代中。"""

DEFAULT_LENGTH = 220


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from app.core.context import get_run_context

    context = get_run_context()
    tracer = context.tracer if context else None

    plan = state.get("plan") or {}
    retrieval = state.get("retrieval") or {}
    qa = state.get("qa") or {}
    request = dict(state.get("request") or {})
    copy_brief = plan.get("copy_brief") or {}

    text, mode = await _write(state, plan, retrieval, qa, request, copy_brief, tracer)

    payload = {
        "text": text,
        "mode": mode,
        "length": len(text),
        "highlights": _highlights(retrieval),
        "cautions": list(retrieval.get("cautions") or [])[:3],
        "sources": _sources(state.get("evidence") or []),
        "qa_disclosure": _qa_disclosure(qa),
        "disclaimer": "本内容由 AI 基于公开史料与数字复原技术生成，属示意性复原，不代表文物真实历史面貌。",
    }

    if tracer is not None:
        tracer.emit(
            "copy_ready",
            "copywriting",
            mode=mode,
            length=len(text),
            preview=truncate(text, 160),
            highlights=payload["highlights"][:3],
            sources=payload["sources"][:3],
        )

    return {
        "copy": payload,
        "next_agent": "supervisor",
        "completed": _append(state, "copywriting"),
        "steps": int(state.get("steps") or 0) + 1,
    }


async def _write(
    state: dict[str, Any],
    plan: dict[str, Any],
    retrieval: dict[str, Any],
    qa: dict[str, Any],
    request: dict[str, Any],
    copy_brief: dict[str, Any],
    tracer: TraceRecorder | None,
) -> tuple[str, str]:
    evidence = state.get("evidence") or []
    evidence_block = (
        "\n\n".join(
            f"[{index + 1}] 《{item.get('title')}》（{item.get('source')}）：{truncate(str(item.get('text') or ''), 480)}"
            for index, item in enumerate(evidence[:5])
        )
        or "（本次未检索到可用史料，请只做画面描述，不要引入任何具体史实）"
    )

    cautions = retrieval.get("cautions") or []
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": TEMPLATE.format(
                goal=str(plan.get("goal") or ""),
                style_label=str(plan.get("profile_label") or ""),
                artifact=str(request.get("item") or request.get("artifact") or "三星堆文物"),
                qa_summary=_qa_summary(qa),
                evidence=evidence_block,
                cautions="；".join(str(item) for item in cautions[:3]) or "（无特别标注）",
                length=int(copy_brief.get("length") or DEFAULT_LENGTH),
                audience=str(copy_brief.get("audience") or "博物馆观众"),
                tone=str(copy_brief.get("tone") or "专业但通俗"),
            ),
        },
    ]

    if registry.enabled:
        try:
            return await _stream(messages, tracer)
        except ProviderUnavailable as exc:
            logger.warning("文案 LLM 不可用，使用模板兜底: %s", exc)
            metrics.record_degradation(component="copywriter", reason=str(exc), fallback="template")
            if tracer is not None:
                tracer.degraded("copywriting", reason=str(exc), fallback="template")
        except Exception as exc:  # noqa: BLE001 - 流式中断后回退到非流式
            logger.warning("文案流式生成失败，回退非流式: %s", exc)
            try:
                result = await registry.call(
                    "copywriter", messages, temperature=0.6, max_tokens=1200, tag="copy_fallback"
                )
                if tracer is not None:
                    tracer.llm(
                        "copywriting", model=result.model, latency_ms=result.latency_ms,
                        usage=result.usage, tag="copy_fallback",
                    )
                return result.text.strip(), "llm-fallback"
            except Exception as inner:  # noqa: BLE001
                logger.warning("文案非流式回退亦失败: %s", inner)

    return _template_copy(plan, retrieval, qa, request, copy_brief), "template"


async def _stream(messages: list[Message], tracer: TraceRecorder | None) -> tuple[str, str]:
    spec = registry.spec("copywriter")
    client = registry.client_for_model(spec.primary)
    chunks: list[str] = []

    async for delta in client.stream_chat(messages, temperature=0.6, max_tokens=1200, tag="copywriter"):
        chunks.append(delta)
        if tracer is not None:
            tracer.emit("copy_delta", "copywriting", delta=delta)

    text = "".join(chunks).strip()
    if not text:
        raise ProviderUnavailable("文案流式返回为空")

    if tracer is not None:
        tracer.llm("copywriting", model=spec.primary, latency_ms=0.0, usage={}, tag="copywriter_stream")
    return text, "llm-stream"


def _template_copy(
    plan: dict[str, Any],
    retrieval: dict[str, Any],
    qa: dict[str, Any],
    request: dict[str, Any],
    copy_brief: dict[str, Any],
) -> str:
    """无 LLM 时的模板兜底：只用结构化事实拼装，零幻觉风险。"""
    artifact = str(request.get("item") or request.get("artifact") or "三星堆文物")
    facts = [str(item) for item in (retrieval.get("key_facts") or [])[:3]]
    cues = [str(item) for item in (retrieval.get("image_cues") or [])[:3]]

    segments = [
        f"画面聚焦于{artifact}，{cues[0] if cues else '器表覆着一层致密的哑光锈色'}，"
        f"轮廓在侧光下显出沉稳的体量。",
    ]
    if facts:
        segments.append("据公开史料记载，" + "；".join(facts) + "。")
    else:
        segments.append("本次未能检索到对应史料，画面细节仅依据形制通识推定。")

    for caution in (retrieval.get("cautions") or [])[:1]:
        segments.append(str(caution) + "。")

    segments.append(_qa_disclosure(qa))
    segments.append(
        f"（目标风格：{plan.get('profile_label') or '通用复原'}；"
        f"本段由模板兜底生成，未使用语言模型。）"
    )
    return "".join(segments)


def _highlights(retrieval: dict[str, Any]) -> list[str]:
    facts = [str(item) for item in (retrieval.get("key_facts") or []) if str(item).strip()]
    cues = [str(item) for item in (retrieval.get("image_cues") or []) if str(item).strip()]
    return [*facts[:3], *cues[:2]][:4]


def _sources(evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "doc_id": str(item.get("doc_id") or ""),
            "title": str(item.get("title") or ""),
            "source": str(item.get("source") or ""),
        }
        for item in evidence[:5]
    ]


def _qa_summary(qa: dict[str, Any]) -> str:
    if not qa:
        return "未执行质检"
    return (
        f"总分 {float(qa.get('score') or 0):.2f} / 阈值 {float(qa.get('threshold') or 0):.2f}，"
        f"{'通过' if qa.get('passed') else '未通过'}（{qa.get('decision_reason') or ''}）"
    )


def _qa_disclosure(qa: dict[str, Any]) -> str:
    if qa and qa.get("passed"):
        return "本次复原已通过风格一致性质检。"
    if qa and qa.get("anachronisms"):
        return "本次复原的质检仍未达标，画面中存在与年代不符的元素，结果仅供内部参考。"
    return "本次复原的质检未达阈值，当前结果属于迭代中的初稿，请以实物资料为准。"


def _append(state: dict[str, Any], mark: str) -> list[str]:
    completed = list(state.get("completed") or [])
    if mark not in completed:
        completed.append(mark)
    return completed
