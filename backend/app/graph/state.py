"""图状态定义。

设计取舍
--------
1. **扁平 + 显式**：状态里的每个键都对应一个明确的生命周期阶段产物
   （plan → evidence → image → qa → copy），便于在 trace 与前端面板里直接展示，
   不需要额外的「状态机解读」代码。
2. **产物用 dict 而非自定义类**：LangGraph 的 checkpointer（未来接入）要求状态可
   序列化。我们把 Pydantic 模型只在 API 边界使用，图内部一律 dict。
3. **不做 reducer 聚合**：除了 `trace`/`errors` 这种纯追加字段，其余字段都是
   「后写覆盖」。显式覆盖比隐式合并更容易推理，也更容易在面试时讲清楚。
"""

from __future__ import annotations

import operator
import time
import uuid
from typing import Annotated, Any, TypedDict


class RestorationState(TypedDict, total=False):
    # ── 输入 ────────────────────────────────────────────────────────────────
    task_id: str
    request: dict[str, Any]

    # ── Planner 产物 ────────────────────────────────────────────────────────
    plan: dict[str, Any]
    profile_key: str

    # ── 检索 Worker 产物 ────────────────────────────────────────────────────
    evidence: list[dict[str, Any]]
    retrieval: dict[str, Any]

    # ── 图像修复 Worker 产物 ────────────────────────────────────────────────
    image: dict[str, Any]
    revision_history: list[dict[str, Any]]

    # ── 质检 Agent 产物 ─────────────────────────────────────────────────────
    qa: dict[str, Any]

    # ── 文案 Worker 产物 ────────────────────────────────────────────────────
    copy: dict[str, Any]

    # ── 编排控制 ────────────────────────────────────────────────────────────
    next_agent: str
    supervisor_reason: str
    completed: list[str]
    revisions: int
    max_revisions: int
    steps: int
    degraded_components: Annotated[list[str], operator.add]

    # ── 观测 ────────────────────────────────────────────────────────────────
    errors: Annotated[list[str], operator.add]
    started_at: float
    finished_at: float


AGENT_LABELS: dict[str, str] = {
    "planner": "规划 Agent",
    "supervisor": "调度中枢",
    "retrieval": "史料检索 Worker",
    "restoration": "图像修复 Worker",
    "quality": "质检 Agent",
    "copywriting": "科普文案 Worker",
    "finalize": "结果汇总",
}

# 每个 Worker 完成后写入 completed 的标记名
WORKER_MARKS = {
    "retrieval": "retrieval",
    "restoration": "restoration",
    "copywriting": "copywriting",
    "quality": "quality",
}


def new_task_id() -> str:
    return f"sxd-{uuid.uuid4().hex[:12]}"


def initial_state(request: dict[str, Any], *, max_revisions: int) -> RestorationState:
    return RestorationState(
        task_id=request.get("task_id") or new_task_id(),
        request=request,
        plan={},
        profile_key="",
        evidence=[],
        retrieval={},
        image={},
        revision_history=[],
        qa={},
        copy={},
        next_agent="planner",
        supervisor_reason="",
        completed=[],
        revisions=0,
        max_revisions=max_revisions,
        steps=0,
        degraded_components=[],
        errors=[],
        started_at=time.time(),
    )


def summarize(state: RestorationState) -> dict[str, Any]:
    """面向前端 / 评估的紧凑结果视图。"""
    image = state.get("image") or {}
    qa = state.get("qa") or {}
    plan = state.get("plan") or {}
    return {
        "task_id": state.get("task_id"),
        "kind": (state.get("request") or {}).get("kind"),
        "goal": plan.get("goal"),
        "image_url": image.get("image_url"),
        "image_provider": image.get("provider"),
        "image_degraded": image.get("degraded", False),
        "seed": image.get("seed"),
        "qa": {
            "passed": qa.get("passed"),
            "score": qa.get("score"),
            "rounds": qa.get("round_index"),
            "anachronisms": qa.get("anachronisms") or [],
        },
        "revisions": state.get("revisions", 0),
        "evidence_count": len(state.get("evidence") or []),
        "copy": (state.get("copy") or {}).get("text"),
        "completed": state.get("completed") or [],
        "errors": state.get("errors") or [],
        "duration_ms": round(
            ((state.get("finished_at") or time.time()) - (state.get("started_at") or time.time()))
            * 1000,
            1,
        ),
    }
