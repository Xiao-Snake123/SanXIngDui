"""调度中枢（Supervisor）。

它是「谁来干下一步」的决策者，而不是执行者 —— 这一区分很重要：
Supervisor 不做任何实际工作（不检索、不出图、不写文案），只输出一个路由决策。
LangGraph 官方把这叫 Supervisor 模式；好处是每个 Worker 的 prompt 可以专注于自己的
任务质量，不必同时承担「何时该停」的判断。

双层决策
--------
- **规则层**：给出确定性的默认顺序（retrieval → restoration → copywriting → finalize），
  并强制几条护栏（步数上限、检索上限、禁止在已有合格结果上重复出图）。
  无论 LLM 是否可用，系统都不会死循环。
- **LLM 层**：在有 Key 时介入，处理规则层无法覆盖的情况 —— 例如质检反馈是
  「形制与实物不符」，说明缺的是史料依据，此时应回退去补检索，而不是直接重出图。
  这是「任务结果按状态路由传递」的具体体现。

LLM 的输出会被**校验并夹紧**：只接受白名单里的路由值，且必须通过护栏检查，
不合法就退回规则层决策。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.models.llm import Message
from app.models.registry import registry

logger = get_logger("app.agents.supervisor")

ROUTES = ("retrieval", "restoration", "copywriting", "finalize")

SYSTEM = """你是三星堆文物复原多智能体系统的**调度中枢**。
你不执行任何具体工作，只决定下一步交给哪个 Worker，并给出简短理由。

可选路由：
- "retrieval"   史料检索 Worker：需要补充或纠正史实依据时
- "restoration" 图像修复 Worker：需要生成或重新生成影像时
- "copywriting" 科普文案 Worker：需要撰写配套文案时
- "finalize"    结束流程：所需产物已齐备，或预算耗尽

决策原则：
1. 先有依据再出图：没有检索结果就直接出图是低质量的；
2. 不重复劳动：已经完成且未失效的步骤不要重做；
3. 质检反馈若指向「形制/史实依据不足」，应回到 retrieval 补充证据，而不是直接重出图；
4. 质检反馈若指向「画面质感/色彩/时代错配」，直接回 restoration；
5. 所有必要产物齐备后立即 finalize，不要为了「更完善」而多做一轮。

只输出 JSON。"""

TEMPLATE = """【复原目标】{goal}

【计划子任务】
{subtasks}

【已完成步骤】{completed}

【质检状态】{qa_status}

【已回炉次数】{revisions} / {max_revisions}

【已执行步数】{steps} / {max_steps}

【累计错误】{errors}

请输出：
{{"next": "<retrieval|restoration|copywriting|finalize>", "reason": "<30 字以内的中文理由>"}}"""


async def run(state: dict[str, Any]) -> dict[str, Any]:
    plan = state.get("plan") or {}
    completed = list(state.get("completed") or [])
    steps = int(state.get("steps") or 0)

    default_next, default_reason = _rule_route(state, completed, steps)
    route, reason, mode = default_next, default_reason, "rule"

    # 只有质检要求回炉时，规则层才存在真正的分叉（补检索 vs 重出图），
    # 这时才值得问 LLM；正常推进顺序（首次 retrieval→restoration→…→finalize）
    # 直接走规则路由，省掉每次经过 supervisor 都调 qwen3-max 的浪费。
    qa = state.get("qa") or {}
    needs_llm = str(qa.get("decision") or "") == "revise"
    if registry.enabled and needs_llm and steps < settings.max_graph_steps:
        llm_route = await _llm_route(state, completed, steps)
        if llm_route is not None:
            candidate, candidate_reason = llm_route
            clamped = _clamp(state, candidate, completed, steps)
            if clamped is not None:
                route, reason, mode = clamped, candidate_reason, "llm"
            else:
                logger.info(
                    "Supervisor LLM 建议 %s 被护栏拒绝，退回规则决策 %s", candidate, default_next
                )
                metrics.inc("sxd_supervisor_override_total", suggested=candidate)

    logger.info("调度决策", extra={"next": route, "mode": mode, "reason": reason})

    from app.core.context import get_run_context

    context = get_run_context()
    if context is not None:
        context.tracer.emit(
            "supervisor_route",
            "supervisor",
            next=route,
            mode=mode,
            reason=reason,
            completed=completed,
            steps=steps,
        )

    return {
        "next_agent": route,
        "supervisor_reason": reason,
        "steps": steps + 1,
    }


# ── 规则层 ──────────────────────────────────────────────────────────────────
def _rule_route(
    state: dict[str, Any], completed: list[str], steps: int
) -> tuple[str, str]:
    plan = state.get("plan") or {}
    qa = state.get("qa") or {}
    revisions = int(state.get("revisions") or 0)
    max_revisions = int(state.get("max_revisions") or settings.max_revisions)

    if steps >= settings.max_graph_steps:
        return "finalize", f"已达步数上限 {settings.max_graph_steps}，强制收敛"

    # 质检要求回炉时，优先补检索（若上轮反馈指向依据不足）
    if qa.get("decision") == "revise":
        feedback_text = " ".join(str(item) for item in (qa.get("feedback") or []))
        needs_evidence = any(
            token in feedback_text for token in ("史料", "依据", "形制", "实证", "考证")
        )
        if needs_evidence and _retrieval_count(completed) < 2:
            return "retrieval", "质检反馈指向史实依据不足，先补充检索"
        if revisions < max_revisions:
            return "restoration", "质检未通过，回炉重出"

    if "retrieval" not in completed and plan.get("retrieval_queries"):
        return "retrieval", "尚未建立史料依据"
    if "restoration" not in completed:
        return "restoration", "尚未生成复原影像"
    if "copywriting" not in completed:
        return "copywriting", "尚未产出科普文案"
    return "finalize", "所有必要产物已齐备"


def _retrieval_count(completed: list[str]) -> int:
    return sum(1 for item in completed if item == "retrieval")


# ── LLM 层 ──────────────────────────────────────────────────────────────────
async def _llm_route(
    state: dict[str, Any], completed: list[str], steps: int
) -> tuple[str, str] | None:
    plan = state.get("plan") or {}
    qa = state.get("qa") or {}

    qa_status = "尚未质检"
    if qa:
        qa_status = (
            f"{'通过' if qa.get('passed') else '未通过'}，总分 {float(qa.get('score') or 0):.2f}，"
            f"决策 {qa.get('decision')}，反馈："
            + "；".join(str(item) for item in (qa.get("feedback") or [])[:3] or ["无"])
            + f"；时代错配：{qa.get('anachronisms') or '无'}"
        )

    messages: list[Message] = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": TEMPLATE.format(
                goal=str(plan.get("goal") or ""),
                subtasks=json.dumps(
                    [item.get("objective") for item in (plan.get("subtasks") or [])],
                    ensure_ascii=False,
                ),
                completed=completed or "无",
                qa_status=qa_status,
                revisions=int(state.get("revisions") or 0),
                max_revisions=int(state.get("max_revisions") or settings.max_revisions),
                steps=steps,
                max_steps=settings.max_graph_steps,
                errors=(state.get("errors") or [])[-3:] or "无",
            ),
        },
    ]

    try:
        payload, result = await registry.call_json(
            "supervisor",
            messages,
            required_keys=("next",),
            temperature=0.0,
            max_tokens=300,
            tag="supervisor",
        )
    except ProviderUnavailable as exc:
        logger.info("Supervisor LLM 不可用，使用规则决策: %s", exc)
        metrics.record_degradation(component="supervisor", reason=str(exc), fallback="rule_based")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supervisor LLM 决策失败，使用规则决策: %s", exc)
        return None

    from app.core.context import get_run_context

    context = get_run_context()
    if context is not None:
        context.tracer.llm(
            "supervisor", model=result.model, latency_ms=result.latency_ms,
            usage=result.usage, tag="supervisor",
        )

    candidate = str(payload.get("next") or "").strip().lower()
    if candidate not in ROUTES:
        return None
    return candidate, str(payload.get("reason") or "LLM 决策")[:60]


# ── 护栏 ────────────────────────────────────────────────────────────────────
def _clamp(
    state: dict[str, Any], candidate: str, completed: list[str], steps: int
) -> str | None:
    """把 LLM 的路由建议夹紧到合法区间。返回 None 表示拒绝该建议。"""
    if steps >= settings.max_graph_steps:
        return "finalize"

    qa = state.get("qa") or {}
    revisions = int(state.get("revisions") or 0)
    max_revisions = int(state.get("max_revisions") or settings.max_revisions)

    if candidate == "retrieval":
        if _retrieval_count(completed) >= 2:
            return None  # 检索已做过两轮，禁止继续补检索
        return candidate

    if candidate == "restoration":
        # 已有合格结果时不允许重出图（浪费预算且无收益）
        if qa.get("passed"):
            return None
        # 质检被跳过（占位图不可判定）时同样禁止回炉：
        # 同一个任务里出图通道不会自己恢复，重出一遍只会得到同一张示意图。
        if qa.get("skipped"):
            return None
        # 决策是 give_up 时禁止回炉 —— `give_up` 的语义**就是**「停止回炉」
        # （见 `qa_worker.decide_quality`：收益递减或已达回炉上限都返回它）。
        #
        # 这里原先漏了这条检查，后果是一次真实事故：
        # 质检已判 give_up，LLM 仍建议 restoration，护栏放行，于是连出 4 轮，
        # 2 分钟内打完 6 次 DashScope → 触发 RPM 限流 → 免费通道也 429 →
        # 最终交付一张程序画的占位图。真正停住它的不是业务判断，
        # 而是「已达步数上限 18，强制收敛」。
        #
        # 拦住之后会退回 `_rule_route`，而规则路由只在 `decision == "revise"`
        # 时回炉，因此会正确走向文案与收敛。
        if str(qa.get("decision") or "") == "give_up":
            return None
        if revisions > max_revisions:
            return None
        return candidate

    if candidate == "copywriting":
        # 只有「还要继续回炉」时才必须先修图。
        #
        # `give_up` 与 `skipped` 是同一个道理：回炉已经结束，**没有可修的对象**。
        # 原先只排除了 `skipped`，于是 give_up 之后文案环节也被拦住 ——
        # 上一轮拦截 restoration 是对的，但另一半没跟上，任务只能靠规则路由兜底收敛。
        still_reworking = (
            bool(qa)
            and not qa.get("skipped")
            and not qa.get("passed")
            and str(qa.get("decision") or "") != "give_up"
            and revisions < max_revisions
        )
        return None if still_reworking else candidate

    if candidate == "finalize":
        return candidate

    return None
