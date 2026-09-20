"""编排引擎装配。

对外暴露统一的 `Orchestrator.astream(state)` 接口，内部有两套实现：

- `LangGraphOrchestrator`：默认引擎。用 `StateGraph` 声明节点与条件边，
  由 LangGraph 负责调度、状态合并（reducer）与步数控制。
- `BuiltinOrchestrator`：内置 DAG 调度器。节点函数、路由规则、状态合并语义
  **与 LangGraph 版本完全一致**，只是调度循环由本文件持有。

为什么保留第二套？因为真实环境里 `pip install langgraph` 未必成功
（私有源缺包、网络受限、Python 版本不匹配）。此时把引擎切到 `builtin`，
系统依然能完整运行，而不是启动即报错。这属于「可用性兜底」，
与「降级到规则实现」的取舍思路一致：**框架是手段，不是目的**。
两套实现共享同一批节点函数，因此不存在行为漂移的维护风险。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger
from app.graph.nodes import (
    copywriting_node,
    finalize_node,
    planner_node,
    quality_node,
    restoration_node,
    retrieval_node,
    supervisor_node,
)
from app.graph.routing import QUALITY_ROUTES, SUPERVISOR_ROUTES
from app.graph.state import RestorationState

logger = get_logger("app.graph.builder")

NODE_FUNCTIONS = {
    "planner": planner_node,
    "supervisor": supervisor_node,
    "retrieval": retrieval_node,
    "restoration": restoration_node,
    "quality": quality_node,
    "copywriting": copywriting_node,
    "finalize": finalize_node,
}

# 带 reducer 的字段：LangGraph 的 Annotated[list, operator.add] 语义是拼接
REDUCER_KEYS = {"errors", "degraded_components"}

# 图结构（用于 /api/graph 展示与文档生成）
GRAPH_TOPOLOGY = {
    "entry": "planner",
    "nodes": {
        "planner": ["supervisor"],
        "supervisor": ["retrieval", "restoration", "copywriting", "finalize"],
        "retrieval": ["supervisor"],
        "restoration": ["quality"],
        "quality": ["restoration", "supervisor"],
        "copywriting": ["supervisor"],
        "finalize": [],
    },
    "self_correction_loop": "quality --(未达阈值且未超预算)--> restoration",
}


class Orchestrator(Protocol):
    name: str

    async def astream(self, state: RestorationState) -> AsyncIterator[dict[str, Any]]: ...


# ── LangGraph 实现 ──────────────────────────────────────────────────────────
class LangGraphOrchestrator:
    name = "langgraph"

    def __init__(self) -> None:
        from langgraph.graph import END, START, StateGraph

        builder = StateGraph(RestorationState)
        for name, function in NODE_FUNCTIONS.items():
            builder.add_node(name, function)

        builder.add_edge(START, "planner")
        builder.add_edge("planner", "supervisor")
        builder.add_conditional_edges("supervisor", _route_supervisor, SUPERVISOR_ROUTES)
        builder.add_edge("retrieval", "supervisor")
        builder.add_edge("copywriting", "supervisor")
        builder.add_edge("restoration", "quality")
        builder.add_conditional_edges("quality", _route_quality, QUALITY_ROUTES)
        builder.add_edge("finalize", END)

        self._graph = builder.compile()
        logger.info("LangGraph 引擎就绪")

    async def astream(self, state: RestorationState) -> AsyncIterator[dict[str, Any]]:
        config = {"recursion_limit": max(25, settings.max_graph_steps * 3)}
        async for chunk in self._graph.astream(state, stream_mode="updates", config=config):
            for node, update in chunk.items():
                yield {"node": node, "update": update or {}}


def _route_supervisor(state: RestorationState) -> str:
    from app.graph.routing import route_from_supervisor

    return route_from_supervisor(state)


def _route_quality(state: RestorationState) -> str:
    from app.graph.routing import route_from_quality

    return route_from_quality(state)


# ── 内置 DAG 调度器 ─────────────────────────────────────────────────────────
class BuiltinOrchestrator:
    name = "builtin"

    def __init__(self) -> None:
        logger.info("内置 DAG 调度器就绪（未使用 LangGraph）")

    async def astream(self, state: RestorationState) -> AsyncIterator[dict[str, Any]]:
        current: str | None = "planner"
        merged: dict[str, Any] = dict(state)
        guard = 0

        while current is not None:
            guard += 1
            if guard > settings.max_graph_steps * 3 + 5:
                logger.error("内置调度器触发硬性迭代上限，强制终止")
                break

            function = NODE_FUNCTIONS.get(current)
            if function is None:
                logger.error("未知节点 %s，终止调度", current)
                break

            update = await function(merged)  # type: ignore[arg-type]
            merged = merge_state(merged, update)
            yield {"node": current, "update": update}

            current = _next_node(current, merged)

    def describe(self) -> dict[str, Any]:
        return {"engine": self.name, "topology": GRAPH_TOPOLOGY}


def _next_node(current: str, state: dict[str, Any]) -> str | None:
    if current == "planner":
        return "supervisor"
    if current == "supervisor":
        return SUPERVISOR_ROUTES.get(str(state.get("next_agent") or "finalize"), "finalize")
    if current in {"retrieval", "copywriting"}:
        return "supervisor"
    if current == "restoration":
        return "quality"
    if current == "quality":
        return QUALITY_ROUTES.get(str(state.get("next_agent") or "supervisor"), "supervisor")
    return None


def merge_state(state: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """与 LangGraph reducer 语义一致的状态合并（供内置调度器与 runner 复用）。"""
    merged = dict(state)
    for key, value in update.items():
        if key in REDUCER_KEYS:
            merged[key] = list(merged.get(key) or []) + list(value or [])
        else:
            merged[key] = value
    return merged


# ── 装配 ────────────────────────────────────────────────────────────────────
_orchestrator: Orchestrator | None = None
_engine_report: dict[str, Any] = {}


def build_orchestrator() -> Orchestrator:
    global _orchestrator, _engine_report

    if _orchestrator is not None:
        return _orchestrator

    if settings.engine_backend == "langgraph":
        try:
            _orchestrator = LangGraphOrchestrator()
            _engine_report = {"engine": "langgraph", "fallback_reason": None, "topology": GRAPH_TOPOLOGY}
            return _orchestrator
        except ImportError as exc:
            logger.warning("langgraph 不可用（%s），切换到内置调度器", exc)
            _engine_report = {
                "engine": "builtin",
                "fallback_reason": f"langgraph 导入失败: {exc}",
                "topology": GRAPH_TOPOLOGY,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("LangGraph 图编译失败，切换到内置调度器")
            _engine_report = {
                "engine": "builtin",
                "fallback_reason": f"图编译失败: {type(exc).__name__}: {exc}",
                "topology": GRAPH_TOPOLOGY,
            }
    else:
        _engine_report = {
            "engine": "builtin",
            "fallback_reason": "配置 ENGINE_BACKEND=builtin",
            "topology": GRAPH_TOPOLOGY,
        }

    _orchestrator = BuiltinOrchestrator()
    return _orchestrator


def get_orchestrator() -> Orchestrator:
    return _orchestrator or build_orchestrator()


def engine_report() -> dict[str, Any]:
    if not _engine_report:
        build_orchestrator()
    return {**_engine_report, "configured": settings.engine_backend}
