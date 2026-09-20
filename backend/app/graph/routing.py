"""条件路由。

路由函数是**纯函数**：只读状态，返回下一个节点名。不产生副作用，不做 IO。
这条纪律让整张图可被静态推理（给定状态必得唯一路由），也使单元测试可以不启动
任何外部依赖就覆盖所有分支。
"""

from __future__ import annotations

from typing import Any

from app.graph.state import RestorationState

SUPERVISOR_ROUTES = {
    "retrieval": "retrieval",
    "restoration": "restoration",
    "copywriting": "copywriting",
    "finalize": "finalize",
}

QUALITY_ROUTES = {
    "restoration": "restoration",
    "supervisor": "supervisor",
}


def route_from_supervisor(state: RestorationState) -> str:
    requested = str(state.get("next_agent") or "finalize")
    route = SUPERVISOR_ROUTES.get(requested)
    if route is None:
        # 非法路由不抛异常，收敛到终态：宁可少做一步，也不能让图跑飞
        return "finalize"
    return route


def route_from_quality(state: RestorationState) -> str:
    requested = str(state.get("next_agent") or "supervisor")
    return QUALITY_ROUTES.get(requested, "supervisor")
