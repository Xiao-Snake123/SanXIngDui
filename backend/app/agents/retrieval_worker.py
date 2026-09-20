"""史料检索 Worker。

职责边界刻意收窄：**只负责「找到并压缩证据」，不做创作**。

为什么不让它顺手把 prompt 也写了？因为一旦检索 Worker 开始「发挥」，它引入的
无出处内容就会与真实史料混杂在一起，而下游的质检 Agent 无法区分二者
（质检只看画面，看不到信息来源）。把「有据可依」和「表达」分开，
是保证整个系统可审计的前提。

输出三样东西：
- `evidence`：重排后的史料卡片（含 doc_id / 出处 / 分数），可追溯到原始报告；
- `brief`：供文案 Worker 使用的史料摘要；
- `image_cues`：从史料中抽出的**视觉线索**（锈色、质感、纹饰），注入图像 prompt。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.core.tracing import TraceRecorder
from app.models.llm import Message
from app.models.registry import registry
from app.rag.store import RetrievedChunk, get_retriever
from app.rag.text import split_sentences, truncate

logger = get_logger("app.agents.retrieval")

SYSTEM = """你是三星堆考古文献的**检索与摘录助手**。
你的工作纪律：
1. 只能使用用户提供的史料片段，**不得引入片段之外的任何事实**；
2. 如果史料中没有直接依据，就不要写进 key_facts，改写到 cautions 里说明「缺乏依据」；
3. 学术争议必须在 cautions 中显式标注，不得用确定语气表述；
4. 只输出 JSON。"""

TEMPLATE = """【检索意图】
{intent}

【候选史料片段】
{documents}

请输出如下 JSON：
{{
  "brief": "<120-200 字，客观概述与本次复原相关的史实，必须能在片段中找到对应依据>",
  "key_facts": ["<3-6 条最可靠的事实，每条不超过 40 字>"],
  "image_cues": ["<3-6 条可直接用于图像 prompt 的视觉线索，中文，如「哑光锈绿表层」「纵目呈柱状外凸」>"],
  "cautions": ["<1-3 条需要注意的争议点或证据不足之处；没有就给空数组>"],
  "citation_ids": ["<引用的 doc_id 列表>"]
}}"""


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from app.core.context import get_run_context

    context = get_run_context()
    tracer = context.tracer if context else None
    plan = state.get("plan") or {}
    queries: list[str] = list(plan.get("retrieval_queries") or [])
    filters = plan.get("retrieval_filters") or None

    retriever = await get_retriever()
    chunks, diagnostics = await retriever.search_multi(
        queries, top_n=6 if len(queries) > 3 else 4
    )

    if not chunks:
        logger.warning("检索无命中，素材将仅依赖规划层先验")
        metrics.inc("sxd_retrieval_empty_total")

    evidence = [chunk.to_dict() for chunk in chunks]
    summary, mode = await _synthesize(plan, chunks, tracer)

    retrieval_result = {
        "queries": queries,
        "filters": filters,
        "hits": len(chunks),
        "diagnostics": diagnostics.to_dict(),
        "synthesis_mode": mode,
        "brief": summary.get("brief", ""),
        "key_facts": summary.get("key_facts", []),
        "image_cues": summary.get("image_cues", []),
        "cautions": summary.get("cautions", []),
    }

    if tracer is not None:
        tracer.emit(
            "retrieval_ready",
            "retrieval",
            hits=len(chunks),
            queries=queries,
            mode=mode,
            brief=truncate(str(summary.get("brief", "")), 300),
            image_cues=summary.get("image_cues", []),
            channels=[chunk.channels for chunk in chunks][:6],
            top_titles=[chunk.title for chunk in chunks][:4],
        )

    return {
        "evidence": evidence,
        "retrieval": retrieval_result,
        "next_agent": "supervisor",
        "completed": _append(state, "retrieval"),
        "steps": int(state.get("steps") or 0) + 1,
    }


async def _synthesize(
    plan: dict[str, Any], chunks: list[RetrievedChunk], tracer: TraceRecorder | None
) -> tuple[dict[str, Any], str]:
    if not chunks:
        return {
            "brief": "本次未检索到相关史料，内容将基于项目内置的三星堆通识与风格档案生成。",
            "key_facts": [],
            "image_cues": [],
            "cautions": ["缺少史料支撑，建议人工复核"],
        }, "empty"

    documents = "\n\n".join(
        f"[{index + 1}] doc_id={chunk.doc_id} 《{chunk.title}》"
        f"（出处：{chunk.display_source or '未标注'}；"
        f"类型 {chunk.source_type}，可信度 {chunk.authority:.2f}，融合分 {chunk.final_score:.3f}）\n"
        f"{truncate(chunk.text, 700)}"
        for index, chunk in enumerate(chunks)
    )

    if registry.enabled:
        messages: list[Message] = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": TEMPLATE.format(
                    intent=str(plan.get("goal") or ""), documents=documents
                ),
            },
        ]
        try:
            payload, result = await registry.call_json(
                "retrieval",
                messages,
                required_keys=("brief", "key_facts"),
                temperature=0.2,
                max_tokens=1400,
                tag="retrieval",
            )
            if tracer is not None:
                tracer.llm(
                    "retrieval", model=result.model, latency_ms=result.latency_ms,
                    usage=result.usage, tag="retrieval",
                )
            payload.setdefault("image_cues", [])
            payload.setdefault("cautions", [])
            return payload, "llm"
        except ProviderUnavailable as exc:
            logger.warning("检索摘要 LLM 不可用，改用抽取式摘要: %s", exc)
            metrics.record_degradation(component="retrieval", reason=str(exc), fallback="extractive")

    return _extractive(chunks), "extractive"


def _extractive(chunks: list[RetrievedChunk]) -> dict[str, Any]:
    """抽取式兜底：直接取高可信度史料的原文句，零幻觉风险。"""
    top = sorted(chunks, key=lambda item: item.final_score, reverse=True)[:3]
    sentences: list[str] = []
    for chunk in top:
        for sentence in split_sentences(chunk.text)[:2]:
            if len(sentence) >= 18:
                sentences.append(truncate(sentence, 120))
        if len(sentences) >= 5:
            break

    cues: list[str] = []
    for chunk in top:
        for tag in chunk.tags[:2]:
            if tag not in cues and len(tag) <= 8:
                cues.append(tag)

    return {
        "brief": "".join(sentences[:4]) or "史料片段较短，建议结合馆藏说明使用。",
        "key_facts": sentences[:4],
        "image_cues": cues[:5],
        "cautions": ["本条摘要由抽取式策略生成（未使用语言模型），语义连贯性有限"],
        "citation_ids": [chunk.doc_id for chunk in top],
    }


def build_evidence_prompt_block(retrieval: dict[str, Any], *, limit: int = 900) -> str:
    """把检索证据压缩成可注入图像 prompt 的一段文字。"""
    parts: list[str] = []
    cues = retrieval.get("image_cues") or []
    if cues:
        parts.append("Evidence-based visual cues: " + "; ".join(str(item) for item in cues[:5]) + ".")
    facts = retrieval.get("key_facts") or []
    if facts:
        parts.append("Documented facts: " + " / ".join(str(item) for item in facts[:3]) + ".")
    # `cautions`（史料争议、摘要局限）是**给文案层的认知提示**，不是画面内容 —— 已删除。
    # 它们原本被写成 "Must not be presented as settled fact: <中文元信息>" 注进英文出图 prompt：
    #   1. 受众错位 —— 出图模型无法"表现"一个不确定性，只能产出混语噪音或凭空加戏；
    #   2. 内容本就不是视觉的 —— 典型值是"本条摘要由抽取式策略生成，语义连贯性有限"。
    # 需要它的地方是科普文案（copywriting_worker 直接读 retrieval["cautions"]），那边不受影响。
    return truncate(" ".join(parts), limit)


def _append(state: dict[str, Any], mark: str) -> list[str]:
    completed = list(state.get("completed") or [])
    if mark not in completed:
        completed.append(mark)
    return completed


__all__ = ["run", "build_evidence_prompt_block"]
