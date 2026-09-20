"""图像修复 Worker（VLM 理解 + 图像生成）。

它承担两件事，且顺序不能颠倒：

1. **参考图理解（VLM）**：如果用户上传了残缺文物的实物照片，先用视觉模型读出
   「这是什么器类、残损在哪些部位、病害类型、可用的形制特征」。
   这一步是「修复」与「随便生成一张」的分水岭 —— 没有它，所谓修复本质上只是文生图。
   生产路径使用本地 `Qwen2.5-VL-7B + QLoRA`（见 `training/`），
   以获得对三星堆专有纹饰更高的辨识粒度；API 通道（qwen3-vl / GLM-4.5V）作为热备。

2. **出图**：把「规划层的形制要点 + 检索层的史料视觉线索 + 参考图的残损报告 +
   上一轮质检的修改指令」四路信息合成最终 prompt，交给出图 provider 链。

Self-Correction 的关键就在第 2 步的第四路：质检 Agent 输出的 `feedback`
不是「不合格」三个字，而是「把镜面高光降下来 / 补足锈绿占比 / 移除铁器元素」
这类可直接转成 prompt 修改的指令。没有这一步，回炉重做只会得到同一张图。
"""

from __future__ import annotations

from typing import Any

from app.agents.retrieval_worker import build_evidence_prompt_block
from app.core.blobs import blobs
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.core.tracing import TraceRecorder
from app.imagegen.base import ImageRequest, ImageResult, fetch_image_bytes
from app.imagegen.service import get_image_service
from app.models.llm import Message, image_part, text_part
from app.models.registry import registry
from app.quality.style_profiles import PROFILES, DEFAULT_PROFILE, StyleProfile
from app.rag.text import truncate

logger = get_logger("app.agents.restoration")

# revision_history 是「当时到底给模型喂了什么」的唯一审计凭证，必须完整。
# 这里曾经写死 600 字，后果有二：
#   1. 尾部被切掉的恰好是史料线索与 Final style anchor —— 决定风格忠实度的两块；
#   2. 与用户选定提示词逐字比对时对不上，看起来像「锁定失效」，实际只是记录失真。
# 仅保留一个防爆上限，避免异常长的 prompt 把状态与响应撑爆。
AUDIT_PROMPT_LIMIT = 4000

REFERENCE_SYSTEM = """你是三星堆文物修复的**视觉分析专家**。
你会看到一张待修复文物的照片或示意图，请客观描述你**观察到的**内容，不要推测画面之外的信息。
只输出 JSON。"""

REFERENCE_TEMPLATE = """请分析这张待修复的三星堆文物影像。

用户说明：{note}
目标器类（用户选择）：{artifact}

输出 JSON：
{{
  "observed_object": "<你实际看到的器类；若与用户选择不符，如实写出>",
  "form_features": ["<观察到的形制特征，3-5 条>"],
  "ornament_features": ["<观察到的纹饰特征，1-4 条；没有就给空数组>"],
  "damage": [{{"location": "<残损位置>", "type": "<残损类型，如断裂/缺失/锈蚀/粉状锈>", "severity": "<轻度/中度/重度>"}}],
  "material_cues": ["<材质与表面状态线索，如「哑光深绿锈层」「局部粉状浅绿锈」，2-4 条>"],
  "restoration_risks": ["<修复时最容易臆造错的地方，1-3 条>"],
  "image_quality": "<对这张输入影像质量的评价，一句话>"
}}"""

REVISION_TEMPLATE = """【上一轮质检未通过，必须在本轮修正】
质检总分：{score:.2f}（阈值 {threshold:.2f}）
判定理由：{reasoning}

必须执行的修改指令：
{directives}

请把这些要求转化为具体的画面调整，不要简单重复上一轮的构图。"""


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from app.core.context import get_run_context

    context = get_run_context()
    tracer = context.tracer if context else None

    request = dict(state.get("request") or {})
    plan = state.get("plan") or {}
    retrieval = state.get("retrieval") or {}
    qa = state.get("qa") or {}
    revision = int(state.get("revisions") or 0)
    task_id = str(state.get("task_id") or "task")

    profile = PROFILES.get(str(plan.get("profile_key") or ""), DEFAULT_PROFILE)

    # ── 1) 参考图理解 ───────────────────────────────────────────────────────
    reference_images: list[str] = list(request.get("reference_images") or [])
    reference_analysis: dict[str, Any] | None = None
    if reference_images:
        reference_analysis = await analyze_reference(
            reference_images[0],
            note=str(request.get("note") or ""),
            artifact=str(request.get("artifact") or request.get("item") or ""),
            tracer=tracer,
        )

    # ── 2) 组装 prompt ──────────────────────────────────────────────────────
    prompt = _compose_prompt(plan, retrieval, reference_analysis, qa, revision, profile)
    negative = _compose_negative(plan, profile, reference_analysis)

    image_spec = plan.get("image_spec") or {}
    image_request = ImageRequest(
        prompt=prompt,
        negative_prompt=negative,
        width=int(image_spec.get("width") or profile.target_size[0]),
        height=int(image_spec.get("height") or profile.target_size[1]),
        seed=request.get("seed"),
        reference_images=reference_images[:3] if reference_images else [],
        style_tokens=[str(item) for item in (image_spec.get("style_tokens") or [])],
        kind=str(request.get("kind") or "scene"),
        prompt_extend=bool(image_spec.get("prompt_extend") or False),
        model=request.get("model_image"),
    )

    if tracer is not None:
        tracer.emit(
            "image_request",
            "restoration",
            round_index=revision,
            # 用服务实际构建出来的链路，而不是写死一串名字。
            # 原先这里是 `"comfyui" if True else ""` 这类硬编码，
            # 结果 trace 会对着一个根本没启用的通道报告链路。
            provider_chain=get_image_service().chain(),
            prompt=truncate(prompt, 900),
            negative_prompt=truncate(negative, 400),
            spec=image_request.snapshot(),
            revised=revision > 0,
        )

    # ── 3) 出图 ─────────────────────────────────────────────────────────────
    result: ImageResult = await get_image_service().generate(image_request, tracer, "restoration")

    image_bytes: bytes | None = None
    try:
        image_bytes, content_type = await fetch_image_bytes(result.image_url)
        result.content_type = content_type
        blobs.put(task_id, image_bytes, content_type)
    except Exception as exc:  # noqa: BLE001 - 取像素失败不应中断，质检会降级
        logger.warning("获取生成图像像素失败，质检将降级: %s", exc)
        metrics.inc("sxd_image_fetch_failed_total")

    image_payload = {
        **result.to_dict(),
        "prompt": prompt,
        "negative_prompt": negative,
        "round_index": revision,
        "reference_analysis": reference_analysis,
        "profile_key": profile.key,
        "profile_label": profile.label,
        "bytes_available": image_bytes is not None,
    }

    if tracer is not None:
        tracer.emit(
            "image_ready",
            "restoration",
            round_index=revision,
            provider=result.provider,
            degraded=result.degraded,
            image_url=result.image_url,
            latency_ms=round(result.latency_ms, 1),
            seed=result.seed,
        )

    history = list(state.get("revision_history") or [])
    history.append(
        {
            "round_index": revision,
            "image_url": result.image_url,
            "provider": result.provider,
            "seed": result.seed,
            "prompt": truncate(prompt, AUDIT_PROMPT_LIMIT),
            "degraded": result.degraded,
        }
    )

    return {
        "image": image_payload,
        "revision_history": history,
        "next_agent": "quality",
        "completed": _append(state, "restoration"),
        "steps": int(state.get("steps") or 0) + 1,
        "degraded_components": ["image_provider"] if result.degraded else [],
    }


async def analyze_reference(
    image_url: str,
    *,
    note: str,
    artifact: str,
    tracer: TraceRecorder | None = None,
) -> dict[str, Any] | None:
    if not registry.enabled:
        return None

    messages: list[Message] = [
        {"role": "system", "content": REFERENCE_SYSTEM},
        {
            "role": "user",
            "content": [
                text_part(REFERENCE_TEMPLATE.format(note=note or "（无）", artifact=artifact or "（未指定）")),
                image_part(image_url),
            ],
        },
    ]

    try:
        payload, result = await registry.call_json(
            "vlm",
            messages,
            required_keys=("observed_object",),
            temperature=0.1,
            max_tokens=1200,
            tag="reference_analysis",
        )
    except ProviderUnavailable as exc:
        logger.warning("参考图理解不可用: %s", exc)
        metrics.record_degradation(component="vlm", reason=str(exc), fallback="skip_reference")
        if tracer is not None:
            tracer.degraded("restoration", reason=str(exc), fallback="skip_reference_analysis")
        return None

    if tracer is not None:
        tracer.llm(
            "restoration", model=result.model, latency_ms=result.latency_ms,
            usage=result.usage, tag="reference_analysis",
        )
        tracer.emit(
            "reference_analysis",
            "restoration",
            observed_object=payload.get("observed_object"),
            damage_count=len(payload.get("damage") or []),
            risks=payload.get("restoration_risks") or [],
        )
    return payload


# ── prompt 组装 ─────────────────────────────────────────────────────────────
def _compose_prompt(
    plan: dict[str, Any],
    retrieval: dict[str, Any],
    reference: dict[str, Any] | None,
    qa: dict[str, Any],
    revision: int,
    _profile: StyleProfile,
) -> str:
    image_spec = plan.get("image_spec") or {}
    base_prompt = str(image_spec.get("prompt") or "").strip()

    # 用户选定方案时，prompt 就是「契约」：首轮必须逐字下发。
    # 理由：策略层已经把它能拿到的史料线索（Evidence-based visual cues）写进提示词了，
    # 再由这里补一遍检索块只会重复文字、稀释用户真正想表达的内容；
    # 而前端向用户承诺的是「本次出图已锁定为你选定的提示词」，行为必须与承诺一致。
    # 注意：只锁首轮。回炉重做时必须注入质检意见，否则 Self-Correction 会失效。
    if plan.get("prompt_locked") and revision == 0:
        return base_prompt

    blocks: list[str] = []

    # ⚠️ 回炉修正指令必须放在**最前面**，绝不能追加到末尾 —— 这是「回炉无效」的修复点。
    #
    # 实测（`scripts/probe_freeimage_tail.py`，2026-09）：出图通道对提示词有**有效窗口**。
    # 构造两个头部完全相同、只差结尾一句的提示词，逐像素比较：
    #   288 字 -> 尾部生效（81.7% 像素不同）
    #   436 字 -> 尾部被**静默丢弃**（最大像素差 = 0，589824 个像素全同）
    #   584 字 -> 同样被丢弃
    # 而生产提示词有 1200~2000 字，远超该窗口。修正指令写在末尾时**从来没有到达模型**，
    # 表现出来就是「回炉了但两轮画面逐像素完全相同、质检分数一模一样」。
    # 放到开头则无论窗口多小都一定在窗口内，且模型对开头指令的注意力权重也更高。
    #
    # 措辞刻意压到最短：「apply:」之前只有约 40 个字符。
    # 因为免费通道的预算已收紧到几百字（见 settings.freeimage_max_prompt_chars），
    # 头部窗口很窄，客套的引导语会把真正要执行的修改指令挤出窗口。
    if revision > 0 and qa:
        score = float(qa.get("score") or 0)
        threshold = float(qa.get("threshold") or 0)
        directives = [str(item) for item in (qa.get("feedback") or []) if str(item).strip()]
        if directives:
            blocks.append(
                f"REVISION {revision} (score {score:.2f} < {threshold:.2f}) - apply: "
                + " | ".join(directives[:5])
                + ". Do not repeat the previous composition."
            )
        anachronisms = qa.get("anachronisms") or []
        if anachronisms:
            blocks.append(
                "Remove these out-of-period elements: "
                + ", ".join(str(item) for item in anachronisms[:6])
                + "."
            )

    blocks.append(base_prompt)

    evidence_block = build_evidence_prompt_block(retrieval)
    if evidence_block:
        blocks.append(evidence_block)

    if reference:
        observed = str(reference.get("observed_object") or "").strip()
        features = [str(item) for item in (reference.get("form_features") or []) if str(item).strip()]
        ornaments = [str(item) for item in (reference.get("ornament_features") or []) if str(item).strip()]
        materials = [str(item) for item in (reference.get("material_cues") or []) if str(item).strip()]
        damage = reference.get("damage") or []
        risks = [str(item) for item in (reference.get("restoration_risks") or []) if str(item).strip()]

        parts = [f"Reference image analysis — observed object: {observed}." if observed else ""]
        if features:
            parts.append("Observed form features: " + "; ".join(features[:5]) + ".")
        if ornaments:
            parts.append("Observed ornament: " + "; ".join(ornaments[:3]) + ".")
        if materials:
            parts.append("Surface and material: " + "; ".join(materials[:4]) + ".")
        if damage:
            damage_desc = "; ".join(
                f"{item.get('location', 'unknown')} {item.get('type', '')} ({item.get('severity', '')})"
                for item in damage[:5]
                if isinstance(item, dict)
            )
            if damage_desc:
                parts.append(
                    "Damage to address explicitly: " + damage_desc + ". "
                    "Keep damage evidence visible; reconstructed areas must be separable."
                )
        if risks:
            parts.append("Do NOT invent: " + "; ".join(risks[:3]) + ".")
        blocks.extend(part for part in parts if part)

    # 这里曾经追加过 `Final style anchor: {profile.label}. {profile.rubric}`，已删除。三个理由：
    #   1. 受众错位 —— `label` 与 `rubric` 都是**中文的评价标准**（"1) 器表是否与材质一致…"），
    #      把它塞进英文出图 prompt 是让"评审口径"去干扰"生成决策"；
    #   2. 重复 —— 风格已经由 base_prompt 里的 `Style: {prompt_tokens}` 表达过一遍；
    #   3. 到不了模型 —— 它是整段 prompt 的**结尾**，而本文件开头已实测出图通道有
    #      约 288~436 字的有效窗口，末尾内容会被静默丢弃。留着只是让审计记录变长。
    # rubric 的正确受众是质检裁判（style_guard），那边单独注入。
    return " ".join(block for block in blocks if block)


def _compose_negative(
    plan: dict[str, Any], profile: StyleProfile, reference: dict[str, Any] | None
) -> str:
    image_spec = plan.get("image_spec") or {}
    parts = [str(image_spec.get("negative_prompt") or profile.negative_prompt())]
    if reference and reference.get("restoration_risks"):
        parts.append(
            ", ".join(str(item) for item in reference["restoration_risks"][:3])
        )
    return ", ".join(part for part in parts if part)


def _append(state: dict[str, Any], mark: str) -> list[str]:
    completed = list(state.get("completed") or [])
    if mark not in completed:
        completed.append(mark)
    return completed
