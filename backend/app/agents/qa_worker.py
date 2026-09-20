"""质检 Agent（Quality Gate）—— Self-Correction 的判定中枢。

它是整条链路里唯一有权力「打回重做」的节点，因此设计上刻意保守：

- **异构校验**：出图侧是 Diffusion，质检侧是判别式 VLM + 确定性图像统计，
  避免「自己判自己的卷」；
- **一票否决**：时代错配（铁器、汉字、明清服饰…）直接封顶分数，
  不参与加权平均 —— 这类错误不存在「其他维度好就可以放过」的余地；
- **收敛保护**：`MAX_REVISIONS` 与「收益递减早停」双重限制。
  如果新一轮分数相比上一轮提升不足 `MIN_GAIN`，即使仍未达标也停止回炉，
  直接放行并如实标注「未达标」。这比无限回炉烧 token 更工程化；
- **可解释输出**：`feedback` 里的每一条都是可直接转成 prompt 的修改指令，
  这是回炉能真正改变结果、而非重复同一张图的前提。
"""

from __future__ import annotations

from typing import Any

from app.agents import lexicon
from app.core.blobs import blobs
from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.imagegen.base import fetch_image_bytes
from app.quality.objective import effective_family_min
from app.quality.style_guard import guard
from app.quality.style_profiles import DEFAULT_PROFILE, PROFILES, StyleProfile

logger = get_logger("app.agents.qa")

MIN_GAIN = 0.02


def decide_quality(
    *,
    passed: bool,
    score: float,
    previous_score: float | None,
    revision: int,
    max_revisions: int,
) -> tuple[str, str]:
    """质检决策的唯一实现（纯函数，便于单测穷举所有分支）。

    返回 (decision, reason)，decision ∈ {accept, revise, give_up}。

    收敛保护有两道：
    1. `max_revisions` —— 硬预算；
    2. `MIN_GAIN` —— 收益递减早停。回炉两次分数只从 0.60 挪到 0.61，
       说明剩下的差距不是「再画一次」能解决的（多半是语料或模型能力上限），
       继续烧预算没有意义。工程上宁可如实交付「未达标」，也不要假装修好了。
    """
    if passed:
        return "accept", "质检通过"

    improved = previous_score is None or (score - float(previous_score)) >= MIN_GAIN

    if not improved:
        return "give_up", (
            f"回炉收益递减（本轮 {score:.2f} vs 上轮 {float(previous_score or 0):.2f}，"
            f"提升不足 {MIN_GAIN}），停止回炉"
        )

    if revision >= max_revisions:
        return "give_up", f"已达回炉上限（{max_revisions} 次），以当前最优结果放行"

    return "revise", f"未达阈值，触发第 {revision + 1} 次回炉"


def _unjudgeable(
    *,
    state: dict[str, Any],
    plan: dict[str, Any],
    image: dict[str, Any],
    revision: int,
    max_revisions: int,
    tracer: TraceRecorder | None,
) -> dict[str, Any]:
    """占位图/非生成图：如实标注「未质检」，并且不回炉。

    为什么决策是 skipped 而不是 give_up：
    `give_up` 的语义是「试过了，没救」。这里根本不是「没救」，而是**没有可判定的对象**。
    混为一谈会让用户以为模型能力不行，从而去调提示词或换模型 —— 方向完全错了。
    所以单列一个 `skipped` 决策，且不消耗回炉预算（回炉也只是重画同一张示意图）。

    返回值结构与正常路径逐字段对齐，避免上游（nodes / runner / 前端）
    因为「字段缺失」而走进意料之外的分支。
    """
    provider = str(image.get("provider") or "unknown")
    reason = (
        f"本次输出由「{provider}」生成，是程序绘制的示意图而非模型生成图，"
        "无法进行风格一致性质检；不存在可判定的对象，因此不触发回炉。"
    )
    qa_payload = {
        "passed": False,
        "skipped": True,
        "skip_reason": reason,
        "score": None,
        "objective_score": None,
        "judge_score": None,
        "threshold": None,
        "round_index": revision,
        "dimensions": {},
        "violations": [],
        "anachronisms": [],
        "feedback": [],
        "reasoning": reason,
        "degraded": True,
        "decision": "skipped",
        "decision_reason": reason,
        "max_revisions": max_revisions,
        "profile_key": plan.get("profile_key"),
        "material_key": plan.get("material_key"),
        "material_label": plan.get("material_label"),
        "expected_family_min": {},
        "acceptance_criteria": plan.get("acceptance_criteria") or [],
        "image_provider": provider,
    }

    metrics.record_degradation(
        component="quality", reason="placeholder_image_not_judgeable", fallback="skipped"
    )
    if tracer is not None:
        tracer.degraded("quality", reason=reason, fallback="skipped")
        tracer.emit(
            "qa_verdict",
            "quality",
            round_index=revision,
            decision="skipped",
            passed=False,
            skipped=True,
            score=None,
            provider=provider,
        )

    logger.info("跳过质检", extra={"provider": provider, "reason": "非生成图不可判定"})

    completed = list(state.get("completed") or [])
    if "quality" not in completed:
        completed.append("quality")

    return {
        "qa": qa_payload,
        # 不回炉：revisions 保持不变，且路由直接去汇总而不是回 restoration
        "revisions": revision,
        "next_agent": "supervisor",
        "supervisor_reason": reason,
        "completed": completed,
        "steps": int(state.get("steps") or 0) + 1,
        "degraded_components": ["quality:placeholder"],
    }


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from app.core.context import get_run_context

    context = get_run_context()
    tracer = context.tracer if context else None

    task_id = str(state.get("task_id") or "task")
    plan = state.get("plan") or {}
    request = dict(state.get("request") or {})
    image = state.get("image") or {}
    revision = int(state.get("revisions") or 0)
    max_revisions = int(state.get("max_revisions") or settings.max_revisions)

    profile: StyleProfile = PROFILES.get(str(plan.get("profile_key") or ""), DEFAULT_PROFILE)

    # 材质优先取计划里已解析好的 key（`build_rule_plan` 已统一注入），
    # 再退回到文本解析 —— 后者不可靠：客户端只传 prompt_override 时，
    # request 里没有 item，文本只剩「三星堆文物」这种泛称，猜不出材质。
    artifact_name = str(request.get("item") or request.get("artifact") or "三星堆文物")
    relic = None
    material_key = plan.get("material_key")
    if material_key and material_key in lexicon.MATERIALS:
        material = lexicon.MATERIALS[str(material_key)]
        relic = lexicon.RELIC_BY_KEY.get(str(plan.get("relic_key") or ""))
    else:
        relic = lexicon.resolve_relic(
            artifact_name,
            str(request.get("subject") or ""),
            str(plan.get("subject") or ""),
            str((plan.get("image_spec") or {}).get("proposal_title") or ""),
        )
        material = relic.material_spec if relic is not None else None

    image_bytes = await _load_pixels(task_id, str(image.get("image_url") or ""))

    # ── 占位图直接短路 ──────────────────────────────────────────────────────
    # 事故复盘：出图通道不可用时落到本地占位图，系统仍然老老实实跑完质检 + 2 轮回炉，
    # 结果必然是 give_up（「回炉收益递减」）。但那不是模型画不好，而是：
    #   * 占位图的像素只由调色板 + 提示词前 9 行文字决定，与文物内容无关；
    #   * 客观指标量到的是那张示意图的属性，所以它**永远**不达标；
    #   * 回炉虽然真的改了提示词、也真的重渲染了，指标却几乎不动 → 边际收益早停。
    # 对一张程序画的示意图做「三星堆风格一致性」质检，结论在物理上就没有意义。
    # 正确做法是如实标注「本次未质检」，而不是伪造一个低分再假装回炉失败。
    if image.get("generative") is False:
        return _unjudgeable(
            state=state,
            plan=plan,
            image=image,
            revision=revision,
            max_revisions=max_revisions,
            tracer=tracer,
        )

    fast = bool(request.get("fast", False))
    verdict = await guard.evaluate(
        image_bytes=image_bytes,
        profile=profile,
        artifact_name=relic.label if relic is not None else artifact_name,
        user_intent=str(plan.get("goal") or ""),
        round_index=revision,
        material=material,
        tracer=tracer,
        skip_judge=fast,
    )

    previous = state.get("qa") or {}
    previous_score = previous.get("score")

    if fast:
        # 快速模式：质检只做客观指标（本地、快），不回炉——首图优先。
        # verdict.passed 如实反映客观指标结果，但 decision 恒为 accept。
        decision, reason = "accept", "快速模式：跳过 VLM 质检与回炉，客观指标仅供参考"
    else:
        decision, reason = decide_quality(
            passed=verdict.passed,
            score=verdict.score,
            previous_score=float(previous_score) if previous_score is not None else None,
            revision=revision,
            max_revisions=max_revisions,
        )

    qa_payload = {
        **verdict.to_dict(),
        "decision": decision,
        "decision_reason": reason,
        "max_revisions": max_revisions,
        "profile_key": profile.key,
        "material_key": material.key if material is not None else None,
        "material_label": material.label if material is not None else None,
        # 对外暴露质检实际采用的色域标尺，便于区分「模型没画好」与「标尺配错了」
        "expected_family_min": effective_family_min(profile, material),
        "acceptance_criteria": plan.get("acceptance_criteria") or [],
    }

    metrics.record_quality(
        style_score=verdict.score,
        passed=verdict.passed,
        revisions=revision,
        kind=str(request.get("kind") or "unknown"),
    )

    if tracer is not None:
        tracer.emit(
            "qa_verdict",
            "quality",
            round_index=revision,
            score=round(verdict.score, 4),
            objective_score=round(verdict.objective_score, 4),
            judge_score=round(verdict.judge_score, 4) if verdict.judge_score is not None else None,
            threshold=verdict.threshold,
            passed=verdict.passed,
            decision=decision,
            reason=reason,
            anachronisms=verdict.anachronisms,
            feedback=verdict.feedback,
            dimensions=qa_payload["dimensions"],
            degraded=verdict.degraded,
        )

    logger.info(
        "质检决策",
        extra={"decision": decision, "score": round(verdict.score, 3), "round": revision},
    )

    completed = list(state.get("completed") or [])
    if "quality" not in completed:
        completed.append("quality")

    return {
        "qa": qa_payload,
        "revisions": revision + 1 if decision == "revise" else revision,
        "next_agent": "restoration" if decision == "revise" else "supervisor",
        "supervisor_reason": reason,
        "completed": completed,
        "steps": int(state.get("steps") or 0) + 1,
        "degraded_components": ["judge"] if verdict.degraded else [],
    }


async def _load_pixels(task_id: str, image_url: str) -> bytes | None:
    blob = blobs.get(task_id)
    if blob is not None:
        return blob.data
    if not image_url:
        return None
    try:
        data, content_type = await fetch_image_bytes(image_url)
        blobs.put(task_id, data, content_type)
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("质检阶段无法取得图像像素: %s", exc)
        metrics.inc("sxd_quality_pixels_missing_total")
        return None
