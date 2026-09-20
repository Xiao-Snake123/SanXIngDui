"""内置 DAG 调度器（`ENGINE_BACKEND=builtin`）的端到端一致性测试。

这条路径是「langgraph 装不上时的可用性兜底」，因此必须验证它不是摆设：
节点序列、状态合并、Self-Correction 回炉都要与 LangGraph 版本表现一致。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.config import settings
from app.graph import builder as builder_module
from app.graph.builder import BuiltinOrchestrator, merge_state
from app.graph.runner import RestorationRunner
from app.graph.state import initial_state

REQUEST = {
    "kind": "scene",
    "identity": "大祭司",
    "scene": "博物馆展厅",
    "item": "青铜纵目面具",
    "style": "博物馆纪实摄影",
    "seed": 4242,
}


@pytest.fixture
def builtin_engine(monkeypatch):
    """把全局引擎临时切到内置调度器，测试结束后恢复。"""
    original_orchestrator = builder_module._orchestrator
    original_report = builder_module._engine_report
    original_backend = settings.engine_backend

    settings.engine_backend = "builtin"
    builder_module._orchestrator = None
    builder_module._engine_report = {}

    yield

    settings.engine_backend = original_backend
    builder_module._orchestrator = original_orchestrator
    builder_module._engine_report = original_report


class TestBuiltinScheduler:
    def test_engine_selection(self, builtin_engine):
        orchestrator = builder_module.build_orchestrator()
        assert isinstance(orchestrator, BuiltinOrchestrator)
        report = builder_module.engine_report()
        assert report["engine"] == "builtin"
        assert report["fallback_reason"]

    def test_full_run_produces_complete_result(self, builtin_engine):
        async def scenario():
            runner = RestorationRunner(dict(REQUEST))
            events = [event async for event in runner.astream()]
            return events, runner._final or {}

        events, result = asyncio.run(scenario())
        kinds = [event["type"] for event in events]

        assert "start" in kinds and "done" in kinds
        assert "error" not in kinds, f"内置调度器执行失败: {[e for e in events if e['type'] == 'error']}"

        assert result["image_url"]
        assert result["engine"] == "builtin"
        # 测试环境无真实出图通道 → 占位图 → 质检如实标注 skipped（不伪造分数）
        assert result["qa"]["decision"] in {"accept", "revise", "give_up", "skipped"}
        assert result["evidence"]
        assert result["copy"]["text"]

    def test_node_sequence_matches_langgraph_topology(self, builtin_engine):
        async def scenario():
            runner = RestorationRunner(dict(REQUEST))
            nodes = []
            async for event in runner.astream():
                if event["type"] == "node":
                    nodes.append(event["node"])
            return nodes

        nodes = asyncio.run(scenario())

        assert nodes[0] == "planner"
        assert nodes[-1] == "finalize"
        assert "retrieval" in nodes
        assert "copywriting" in nodes
        # 出图与质检必须成对出现：restoration → quality
        for index, name in enumerate(nodes[:-1]):
            if name == "restoration":
                assert nodes[index + 1] == "quality"

    def test_self_correction_loop_reaches_quality_again(self, builtin_engine):
        async def scenario():
            runner = RestorationRunner(dict(REQUEST))
            async for _ in runner.astream():
                pass
            return runner.state

        state = asyncio.run(scenario())
        rounds = len(state.get("revision_history") or [])
        assert rounds >= 1
        # 每多一次回炉就多一轮质检，且质检记录与出图轮次数一致
        assert state["qa"]["round_index"] == rounds - 1

    def test_progress_is_monotonic(self, builtin_engine):
        async def scenario():
            runner = RestorationRunner(dict(REQUEST))
            values = []
            async for event in runner.astream():
                if event["type"] == "node":
                    values.append(event["progress"])
            return values

        values = asyncio.run(scenario())
        assert values == sorted(values)
        assert values[-1] == 100


class TestRunnerStateAccumulation:
    def test_runner_merges_reducer_keys_like_the_engine(self, builtin_engine):
        state = initial_state({"kind": "scene"}, max_revisions=1)
        merged = merge_state(state, {"degraded_components": ["image_provider"]})
        merged = merge_state(merged, {"degraded_components": ["judge"]})
        assert merged["degraded_components"] == ["image_provider", "judge"]

    def test_runner_returns_timeline_and_usage(self, builtin_engine):
        async def scenario():
            return await RestorationRunner(dict(REQUEST)).arun()

        result = asyncio.run(scenario())
        assert result["timeline"], "轨迹时间线不能为空，前端依赖它渲染 Agent 面板"
        assert result["usage"]["llm_calls"] >= 0
        assert json.dumps(result["usage"])  # 可序列化（会随 SSE 下发）
