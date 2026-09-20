"""图节点封装与终态收敛。

节点本身只做三件事：
1. **发节点级轨迹**（node_start / node_end，带真实耗时与一行摘要）——
   这是前端 Agent 时间线与离线复盘的数据源；
2. 调用对应 Agent；
3. 把异常收敛进 `errors`（而不是让整张图崩掉）。

第 3 点是要紧的：图不应该因为「文案写失败」就把已经生成的图像一起丢掉。
部分成功也要交付，并把失败如实写进 trace 与结果 —— 这是生产系统和 demo 的分界线。
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from app.agents import copywriting_worker, planner, qa_worker, restoration_worker, retrieval_worker
from app.agents import supervisor as supervisor_agent
from app.core.context import get_run_context
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.graph.state import summarize

logger = get_logger("app.graph.nodes")


async def _run_guarded(
    name: str, state: dict[str, Any], coro: Awaitable[dict[str, Any]]
) -> dict[str, Any]:
    """节点级故障隔离：失败不中断整图，只记录并让路由继续。"""
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        logger.exception("节点 %s 执行失败", name)
        metrics.inc("sxd_node_failure_total", node=name)
        context = get_run_context()
        if context is not None:
            context.tracer.emit(
                "node_failed",
                name,
                error=type(exc).__name__,
                message=str(exc)[:500],
            )
        return {
            "errors": [f"{name}: {type(exc).__name__}: {str(exc)[:300]}"],
            "next_agent": "finalize" if name == "planner" else "supervisor",
            "steps": int(state.get("steps") or 0) + 1,
        }


async def _guard(
    name: str, state: dict[str, Any], coro: Awaitable[dict[str, Any]]
) -> dict[str, Any]:
    """统一的节点包装：外层发轨迹 span，内层做故障隔离。"""
    context = get_run_context()
    tracer = context.tracer if context is not None else None

    if tracer is None:
        return await _run_guarded(name, state, coro)

    async with tracer.aspan(name) as box:
        result = await _run_guarded(name, state, coro)
        summary = _node_summary(name, result)
        if summary:
            box["summary"] = summary
        if result.get("next_agent"):
            box["next"] = result["next_agent"]
        if result.get("errors"):
            box["errors"] = len(result["errors"])
        return result


def _node_summary(node: str, result: dict[str, Any]) -> str:
    """给前端一行可读的节点产出摘要。

    刻意在这里做（而不是让前端解析各种字段）：摘要口径只有一处定义，
    换引擎或改字段名时不会出现前端显示错乱。
    """
    if not isinstance(result, dict):
        return ""
    if node == "planner":
        return f"目标：{str((result.get('plan') or {}).get('goal') or '')[:48]}"
    if node == "retrieval":
        retrieval = result.get("retrieval") or {}
        return f"命中 {retrieval.get('hits', 0)} 条史料 · 摘要方式 {retrieval.get('synthesis_mode', '-')}"
    if node == "restoration":
        image = result.get("image") or {}
        degraded = "（降级）" if image.get("degraded") else ""
        return (
            f"出图 {image.get('provider', '-')}{degraded} · "
            f"{round(float(image.get('latency_ms') or 0))}ms · "
            f"第 {int(image.get('round_index') or 0) + 1} 轮"
        )
    if node == "quality":
        qa = result.get("qa") or {}
        return (
            f"得分 {float(qa.get('score') or 0):.3f} / 阈值 {float(qa.get('threshold') or 0):.2f} · "
            f"{'通过' if qa.get('passed') else '未通过'} · 决策 {qa.get('decision', '-')}"
        )
    if node == "copywriting":
        copy_payload = result.get("copy") or {}
        return f"文案 {copy_payload.get('length', 0)} 字 · 方式 {copy_payload.get('mode', '-')}"
    if node == "supervisor":
        return f"路由至 {result.get('next_agent', '-')}：{str(result.get('supervisor_reason') or '')[:44]}"
    if node == "finalize":
        return "产出已汇总"
    return ""


async def planner_node(state: dict[str, Any]) -> dict[str, Any]:
    return await _guard("planner", state, planner.run(state))


async def supervisor_node(state: dict[str, Any]) -> dict[str, Any]:
    return await _guard("supervisor", state, supervisor_agent.run(state))


async def retrieval_node(state: dict[str, Any]) -> dict[str, Any]:
    return await _guard("retrieval", state, retrieval_worker.run(state))


async def restoration_node(state: dict[str, Any]) -> dict[str, Any]:
    return await _guard("restoration", state, restoration_worker.run(state))


async def quality_node(state: dict[str, Any]) -> dict[str, Any]:
    return await _guard("quality", state, qa_worker.run(state))


async def copywriting_node(state: dict[str, Any]) -> dict[str, Any]:
    return await _guard("copywriting", state, copywriting_worker.run(state))


async def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
    import time

    context = get_run_context()
    tracer = context.tracer if context else None

    finished = time.time()
    final_state = dict(state)
    final_state["finished_at"] = finished

    result = summarize(final_state)
    result.update(
        {
            "plan": state.get("plan") or {},
            "retrieval": state.get("retrieval") or {},
            "evidence": state.get("evidence") or [],
            "image": state.get("image") or {},
            "qa": state.get("qa") or {},
            "copy": state.get("copy") or {},
            "revision_history": state.get("revision_history") or [],
            "supervisor_reason": state.get("supervisor_reason"),
            "degraded_components": sorted(set(state.get("degraded_components") or [])),
        }
    )

    metrics.record_task_finished(
        kind=str((state.get("request") or {}).get("kind") or "unknown"),
        duration_ms=result["duration_ms"],
        failed=bool(state.get("errors")),
    )

    if tracer is not None:
        tracer.emit(
            "final",
            "finalize",
            duration_ms=result["duration_ms"],
            image_url=result.get("image_url"),
            qa_passed=result["qa"].get("passed"),
            qa_score=result["qa"].get("score"),
            revisions=result.get("revisions"),
            degraded=result.get("degraded_components"),
            usage=tracer.usage_summary(),
        )
        tracer.persist()

    logger.info(
        "复原任务完成",
        extra={
            "task_id": result.get("task_id"),
            "duration_ms": result["duration_ms"],
            "qa_passed": result["qa"].get("passed"),
            "revisions": result.get("revisions"),
        },
    )

    return {"finished_at": finished, "next_agent": "finalize"}
