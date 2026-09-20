"""任务执行器：把「状态 + 上下文 + 引擎 + 事件流」串起来。

这一层是所有外部入口（SSE 端点、同步端点、评估脚本）的**唯一**调用面，
它保证了三种调用方式共享完全相同的执行语义与观测埋点。
如果让 SSE 端点和评估脚本各自拼装一次执行逻辑，两边迟早会漂移
（评估报告的指标和线上看到的指标对不上，是最难查的一类 bug）。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.core.config import settings
from app.core.context import RunContext, reset_run_context, set_run_context
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.core.tracing import TraceEvent, TraceRecorder
from app.graph.builder import get_orchestrator, merge_state
from app.graph.state import AGENT_LABELS, RestorationState, initial_state, summarize
from app.storage import tasks as task_repository

logger = get_logger("app.graph.runner")

# 各阶段完成后的进度百分比（前端进度条使用真实值，不再用随机数模拟）
STAGE_PROGRESS = {
    "planner": 10,
    "supervisor": 12,
    "retrieval": 30,
    "restoration": 68,
    "quality": 86,
    "copywriting": 96,
    "finalize": 100,
}


class RestorationRunner:
    """一次复原任务的执行实体。"""

    def __init__(
        self, request: dict[str, Any], *, sink: Callable[[TraceEvent], None] | None = None
    ) -> None:
        self.request = dict(request)
        self.task_id = str(self.request.get("task_id") or "") or None
        self.tracer = TraceRecorder(self.task_id, sink)
        self.task_id = self.tracer.task_id
        self.request["task_id"] = self.task_id
        self.max_revisions = int(
            self.request.get("max_revisions")
            if self.request.get("max_revisions") is not None
            else settings.max_revisions
        )
        self.state: RestorationState = initial_state(self.request, max_revisions=self.max_revisions)
        self._context = RunContext(
            task_id=self.task_id,
            tracer=self.tracer,
            request=self.request,
            options={},
        )
        self._started = time.perf_counter()
        self._final: dict[str, Any] | None = None
        # 进度只增不减：supervisor 是纯路由节点，会在两个高进度阶段之间反复出现，
        # 若直接输出 STAGE_PROGRESS 的值，前端进度条会来回跳。
        self._progress: float = 0.0

    # ── 流式执行 ────────────────────────────────────────────────────────────
    async def astream(self) -> AsyncIterator[dict[str, Any]]:
        """产出结构化事件流。

        事件类型：
        - `node`：某个图节点执行完毕（含增量状态摘要）
        - `done`：任务结束（含完整结果）
        - `error`：任务级失败
        """
        metrics.record_task_started(kind=str(self.request.get("kind") or "unknown"), task_id=self.task_id)
        await self._persist_start()

        yield {
            "type": "start",
            "task_id": self.task_id,
            "kind": self.request.get("kind"),
            "max_revisions": self.max_revisions,
            "engine": get_orchestrator().name,
        }

        token = set_run_context(self._context)
        try:
            orchestrator = get_orchestrator()
            async for step in orchestrator.astream(self.state):
                node = str(step.get("node"))
                update = step.get("update") or {}
                self.state = merge_state(self.state, update)  # type: ignore[arg-type]

                self._progress = max(self._progress, float(STAGE_PROGRESS.get(node, 50)))
                await self._persist_node(node, update)

                yield {
                    "type": "node",
                    "node": node,
                    "label": AGENT_LABELS.get(node, node),
                    "progress": round(self._progress, 1),
                    "completed": list(self.state.get("completed") or []),
                    "next": self.state.get("next_agent"),
                    "reason": self.state.get("supervisor_reason"),
                    "elapsed_ms": round((time.perf_counter() - self._started) * 1000, 1),
                    "patch": _patch_view(node, update),
                }
        except asyncio.CancelledError:
            # 客户端断开（用户点了「停止」）时执行器被取消。
            #
            # `CancelledError` 继承自 `BaseException`，下面的 `except Exception` 接不住 ——
            # 不单独处理的话，任务行会永远停在起始状态：开发者页上看起来像
            # 「一个跑不完的僵尸任务」，而它其实早就被用户主动叫停了。
            #
            # 用 `shield` 包住这次写入：否则取消信号会立刻把落库也打断，等于没写。
            # 写入失败也不能吞掉取消 —— 最后必须重新抛出，否则取消语义就丢了。
            with contextlib.suppress(Exception):
                await asyncio.shield(self._persist_stopped())
            metrics.record_task_finished(
                kind=str(self.request.get("kind") or "unknown"),
                duration_ms=(time.perf_counter() - self._started) * 1000,
                failed=False,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - 执行器级兜底
            logger.exception("任务执行失败")
            metrics.record_task_finished(
                kind=str(self.request.get("kind") or "unknown"),
                duration_ms=(time.perf_counter() - self._started) * 1000,
                failed=True,
            )
            await self._persist_failure(str(exc))
            yield {
                "type": "error",
                "task_id": self.task_id,
                "message": str(exc),
                "error_type": type(exc).__name__,
                "state": summarize(self.state),
            }
            return
        finally:
            reset_run_context(token)

        result = self._build_result()
        self._final = result
        await self._persist_finish(result)
        yield {"type": "done", "task_id": self.task_id, "result": result}

    # ── 落库 ────────────────────────────────────────────────────────────────
    async def _persist_start(self) -> None:
        await task_repository.ensure_task(
            self.task_id,
            {
                "kind": str(self.request.get("kind") or "unknown"),
                "engine": get_orchestrator().name,
                "request": _sanitize_request(self.request),
            },
        )

    async def _persist_node(self, node: str, update: dict[str, Any]) -> None:
        """逐轮落库，而不是等任务结束一次性写。

        两个理由：
        1. **回炉是逐轮的** —— 第 2 轮跑完时，第 1 轮的分数必须已经可查；
        2. **钱已经花了** —— 任务中途崩溃时，前面几轮的出图与质检记录必须留下来，
           否则「失败任务到底卡在哪一轮」只能靠猜。
        """
        if node == "restoration":
            image = update.get("image") or {}
            if image:
                await task_repository.upsert_image(self.task_id, _image_row(image, self.state))
        elif node == "quality":
            qa = update.get("qa") or {}
            if qa:
                await task_repository.upsert_verdict(self.task_id, _verdict_row(qa))

    async def _persist_finish(self, result: dict[str, Any]) -> None:
        state = self.state
        qa = state.get("qa") or {}
        image = state.get("image") or {}
        plan = state.get("plan") or {}
        request = self.request
        await task_repository.upsert_task(
            self.task_id,
            {
                "kind": str(request.get("kind") or "unknown"),
                "item": _text(request.get("item") or request.get("artifact")),
                "identity": _text(request.get("identity")),
                "scene": _text(request.get("scene")),
                "style": _text(request.get("style")),
                "goal": plan.get("goal"),
                "engine": result.get("engine"),
                "profile_key": _text(plan.get("profile_key")),
                "material_key": _text(plan.get("material_key")),
                "status": "ok" if result.get("ok") else "failed",
                "score": qa.get("score"),
                "objective_score": qa.get("objective_score"),
                "judge_score": qa.get("judge_score"),
                "passed": qa.get("passed"),
                "decision": _text(qa.get("decision")),
                "revisions": int(state.get("revisions") or 0),
                "provider": _text(image.get("provider")),
                "image_degraded": bool(image.get("degraded")),
                # 落占位图时质检是 skipped。它必须单独记下来：
                # 把「没判过」的任务算进通过率分母，会让指标凭空变差，
                # 而这类口径错误比代码 bug 更难被发现
                "qa_skipped": bool(qa.get("skipped")),
                "evidence_count": len(state.get("evidence") or []),
                "degraded_components": result.get("degraded_components") or [],
                "duration_ms": result.get("duration_ms"),
                "request": _sanitize_request(request),
            },
        )

    async def _persist_stopped(self) -> None:
        """如实记录「被用户中止」。

        `status` 单列一个 `stopped`，而不是复用 `failed`：
        失败是系统的问题，中止是用户的意愿，两者混在一起会让质量报表说谎 ——
        用户越爱中止，看起来就越像系统越不稳定。

        并且**不写 `decision`**：中止的任务没有质检结论，
        而 `judged` 口径（见 `repository._ratio_view`）正是以
        「有没有 decision」判断「判没判过」。写空值才会被正确排除在通过率分母之外。
        """
        await task_repository.upsert_task(
            self.task_id,
            {
                "kind": str(self.request.get("kind") or "unknown"),
                "engine": get_orchestrator().name,
                "status": "stopped",
                "revisions": int(self.state.get("revisions") or 0),
                "duration_ms": round((time.perf_counter() - self._started) * 1000, 1),
                "request": _sanitize_request(self.request),
            },
        )

    async def _persist_failure(self, message: str) -> None:
        await task_repository.upsert_task(
            self.task_id,
            {
                "kind": str(self.request.get("kind") or "unknown"),
                "engine": get_orchestrator().name,
                "status": "failed",
                "error": message[:4000],
                "revisions": int(self.state.get("revisions") or 0),
                "request": _sanitize_request(self.request),
            },
        )

    # ── 同步执行 ────────────────────────────────────────────────────────────
    async def arun(self) -> dict[str, Any]:
        if self._final is not None:
            return self._final
        async for event in self.astream():
            if event["type"] == "error":
                return {
                    "task_id": self.task_id,
                    "ok": False,
                    "error": event.get("message"),
                    "state": event.get("state"),
                }
        return self._final or {}

    # ── 结果装配 ────────────────────────────────────────────────────────────
    def _build_result(self) -> dict[str, Any]:
        state = self.state
        base = summarize(state)
        base.update(
            {
                "ok": not (state.get("errors") or []),
                "engine": get_orchestrator().name,
                "plan": state.get("plan") or {},
                "retrieval": state.get("retrieval") or {},
                "evidence": state.get("evidence") or [],
                "image": state.get("image") or {},
                "qa": state.get("qa") or {},
                "copy": state.get("copy") or {},
                "revision_history": state.get("revision_history") or [],
                "degraded_components": sorted(set(state.get("degraded_components") or [])),
                "timeline": self.tracer.timeline(),
                "usage": self.tracer.usage_summary(),
            }
        )
        return base


def _patch_view(node: str, update: dict[str, Any]) -> dict[str, Any]:
    """给 SSE 前端一份「可展示但不冗余」的增量摘要，避免把大字段（如全量 evidence）塞进帧里。"""
    if not isinstance(update, dict):
        return {}
    if node == "planner":
        plan = update.get("plan") or {}
        return {
            "goal": plan.get("goal"),
            "profile": plan.get("profile_label"),
            "queries": plan.get("retrieval_queries"),
            "planner_mode": plan.get("planner_mode"),
        }
    if node == "retrieval":
        retrieval = update.get("retrieval") or {}
        return {
            "hits": retrieval.get("hits"),
            "mode": retrieval.get("synthesis_mode"),
            "cues": retrieval.get("image_cues"),
            "top": [item.get("title") for item in (update.get("evidence") or [])[:4]],
        }
    if node == "restoration":
        image = update.get("image") or {}
        return {
            "provider": image.get("provider"),
            "degraded": image.get("degraded"),
            "round": image.get("round_index"),
            "image_url": image.get("image_url"),
        }
    if node == "quality":
        qa = update.get("qa") or {}
        return {
            "score": qa.get("score"),
            "objective": qa.get("objective_score"),
            "judge": qa.get("judge_score"),
            "passed": qa.get("passed"),
            "decision": qa.get("decision"),
            "feedback": (qa.get("feedback") or [])[:3],
            "anachronisms": qa.get("anachronisms") or [],
        }
    if node == "copywriting":
        copy = update.get("copy") or {}
        return {"length": copy.get("length"), "mode": copy.get("mode")}
    if node == "supervisor":
        return {"next": update.get("next_agent"), "reason": update.get("supervisor_reason")}
    if node == "finalize":
        return {"finished": True}
    return {}


def _image_row(image: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """把一轮出图的结果拍成一行。

    宽高取自 plan.image_spec 而不是 image.raw：不同出图通道的 raw 字段名不统一
    （DashScope 给 size、免费通道什么都不给），而 image_spec 是**我们下发的**参数，
    它才是「这一轮实际请求的尺寸」这件事的唯一事实来源。
    """
    spec = (state.get("plan") or {}).get("image_spec") or {}
    return {
        "round_index": int(image.get("round_index") or 0),
        "image_url": image.get("image_url"),
        "provider": image.get("provider"),
        "seed": image.get("seed"),
        "width": spec.get("width"),
        "height": spec.get("height"),
        "prompt": image.get("prompt"),
        "negative_prompt": image.get("negative_prompt"),
        "degraded": bool(image.get("degraded")),
        "bytes_available": bool(image.get("bytes_available")),
    }


def _verdict_row(qa: dict[str, Any]) -> dict[str, Any]:
    return {
        "round_index": int(qa.get("round_index") or 0),
        "score": qa.get("score"),
        "objective_score": qa.get("objective_score"),
        "judge_score": qa.get("judge_score"),
        "threshold": qa.get("threshold"),
        "passed": qa.get("passed"),
        "decision": qa.get("decision"),
        "reason": _first_text(qa, "decision_reason", "reason", "reasoning"),
        "feedback": list(qa.get("feedback") or []),
        "anachronisms": list(qa.get("anachronisms") or []),
        "dimensions": dict(qa.get("dimensions") or {}),
        "violated_rules": list(qa.get("violations") or []),
        "material_key": qa.get("material_key"),
        "profile_key": qa.get("profile_key"),
    }


def _first_text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _sanitize_request(request: dict[str, Any]) -> dict[str, Any]:
    """入库前瘦身。

    参考图可能是 `data:image/png;base64,...` 的整张图（几百 KB 到几 MB）。
    把它整段塞进 JSONB 会让任务表迅速膨胀，而且内容是重复的（原图另有归档）。
    这里只留「几张、什么类型、多大」，需要原图时去 var/outputs 或上传记录里找。
    """
    payload = dict(request)
    references = payload.pop("reference_images", None)
    if references:
        payload["reference_images"] = [
            {"kind": _reference_kind(item), "chars": len(str(item))}
            for item in list(references)[:3]
        ]
    return payload


def _reference_kind(value: Any) -> str:
    text = str(value)
    if text.startswith("data:"):
        return text[5:].split(";", 1)[0] or "data"
    if text.startswith(("http://", "https://")):
        return "url"
    return "unknown"


def _text(value: Any, limit: int = 200) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


async def run_once(request: dict[str, Any]) -> dict[str, Any]:
    """供评估脚本与同步接口使用的便捷入口：跑完整条链路并返回结果。

    这里曾经只剩一句 `return result`（`result` 从未定义）——
    一个文档上公开、实际一调用就 NameError 的死函数。
    现在委托给 `RestorationRunner`，与 HTTP 接口走完全相同的执行路径，
    避免「脚本跑出来的结果和接口跑出来的不一样」这类最难的排查。
    """
    runner = RestorationRunner(request)
    return await runner.arun()
