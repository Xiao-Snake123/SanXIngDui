"""路由纯函数与内置调度器语义测试。

路由函数不产生副作用、不做 IO，因此可以在不启动任何外部依赖的情况下
把所有分支覆盖到位 —— 这也是把它们写成纯函数的主要动机。
"""

from __future__ import annotations

import pytest

from app.graph.builder import merge_state
from app.graph.routing import route_from_quality, route_from_supervisor


class TestSupervisorRouting:
    def test_known_routes(self):
        for target in ("retrieval", "restoration", "copywriting", "finalize"):
            assert route_from_supervisor({"next_agent": target}) == target

    def test_unknown_route_converges_to_finalize(self):
        # 非法路由必须收敛到终态，而不能让图跑飞
        assert route_from_supervisor({"next_agent": "self_destruct"}) == "finalize"

    def test_missing_route_converges_to_finalize(self):
        assert route_from_supervisor({}) == "finalize"


class TestQualityRouting:
    def test_revise_goes_back_to_restoration(self):
        assert route_from_quality({"next_agent": "restoration"}) == "restoration"

    def test_accept_returns_to_supervisor(self):
        assert route_from_quality({"next_agent": "supervisor"}) == "supervisor"

    def test_unknown_falls_back_to_supervisor(self):
        # 质检路由出问题时，宁可继续走正常调度，也不能无限回炉
        assert route_from_quality({"next_agent": "loop_forever"}) == "supervisor"


class TestStateMerge:
    def test_reducer_keys_concatenate(self):
        state = {"errors": ["a"], "degraded_components": ["judge"]}
        update = {"errors": ["b"], "degraded_components": ["judge", "vlm"]}
        merged = merge_state(state, update)
        assert merged["errors"] == ["a", "b"]
        assert merged["degraded_components"] == ["judge", "judge", "vlm"]

    def test_normal_keys_overwrite(self):
        merged = merge_state({"steps": 3, "next_agent": "a"}, {"steps": 4, "next_agent": "b"})
        assert merged["steps"] == 4
        assert merged["next_agent"] == "b"

    def test_original_state_not_mutated(self):
        state = {"errors": ["a"]}
        merge_state(state, {"errors": ["b"]})
        assert state["errors"] == ["a"]

    def test_merge_matches_langgraph_reducer_semantics(self):
        """内置调度器与 LangGraph 必须得到同一个状态，否则两套引擎会行为漂移。"""
        pytest.importorskip("langgraph")
        from langgraph.graph import StateGraph

        from app.graph.state import RestorationState

        def node_a(_: RestorationState) -> dict:
            return {"errors": ["a"], "steps": 1}

        def node_b(_: RestorationState) -> dict:
            return {"errors": ["b"], "steps": 2}

        from langgraph.graph import END, START

        builder = StateGraph(RestorationState)
        builder.add_node("a", node_a)
        builder.add_node("b", node_b)
        builder.add_edge(START, "a")
        builder.add_edge("a", "b")
        builder.add_edge("b", END)
        graph = builder.compile()

        # 用同步 invoke 走一遍（节点是同步函数，LangGraph 允许混用）
        final = graph.invoke({"errors": [], "steps": 0})

        manual = merge_state(
            merge_state({"errors": [], "steps": 0}, {"errors": ["a"], "steps": 1}),
            {"errors": ["b"], "steps": 2},
        )
        assert final["errors"] == manual["errors"]
        assert final["steps"] == manual["steps"]
