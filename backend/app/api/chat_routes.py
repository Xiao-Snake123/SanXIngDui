"""对话接口（SSE）。

与 `/api/restore/stream` 的分工
-------------------------------
- `/api/chat/stream`：**不生成图片**，只做「理解需求 → 查史料 → 推荐提示词方案」。
  它是廉价的、可以反复来回的（纯文本，几百毫秒到几秒）。
- `/api/restore/stream`：**真正出图**，且此时通常带着用户选定的 `prompt_override`。

这样拆开的好处：用户可以在不消耗出图预算的前提下反复调整方案，
而每次出图都明确对应一个被用户确认过的提示词 —— 出图是「确定性执行」，
不是「边聊边猜」。

帧协议与复原接口一致（`event: <type>` + `data: <json>`），前端复用同一个解析器。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.conversation.agent import run_chat
from app.conversation.store import ChatSession
from app.conversation.strategies import strategies_for
from app.core.config import settings
from app.core.context import RunContext, reset_run_context, set_run_context
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.core.tracing import TraceEvent, TraceRecorder
from app.storage.sessions import sessions

logger = get_logger("app.api.chat")

router = APIRouter(prefix="/api/chat")

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

PING_INTERVAL = 15.0


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000, description="用户这句话")
    session_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="留空则新建会话",
    )


class ChatResetRequest(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


@router.post("/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    session: ChatSession = await sessions.get_or_create(payload.session_id)
    await sessions.append_user(session, payload.message)

    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=2048)
    eof = object()

    def push(kind: str, data: Any) -> None:
        try:
            queue.put_nowait((kind, data))
        except asyncio.QueueFull:
            logger.warning("对话 SSE 队列已满，丢弃 %s 帧", kind)

    def on_trace(event: TraceEvent) -> None:
        push("trace", event.to_dict())

    tracer = TraceRecorder(f"{session.session_id}-{int(session.updated_at)}", on_trace)

    async def produce() -> None:
        token = set_run_context(
            RunContext(
                task_id=tracer.task_id,
                tracer=tracer,
                request={"kind": "chat", "message": payload.message},
            )
        )
        reply = ""
        proposals: list[dict[str, Any]] = []
        intent: dict[str, Any] = {}
        evidence_titles: list[str] = []
        try:
            async for event in run_chat(session, payload.message, tracer):
                if event["type"] == "message":
                    reply = str(event.get("text") or "")
                elif event["type"] == "proposals":
                    proposals = event.get("items") or []
                elif event["type"] == "intent":
                    intent = event.get("intent") or {}
                elif event["type"] == "evidence":
                    evidence_titles = [str(item.get("title")) for item in (event.get("items") or [])[:4]]
                push(str(event.get("type")), event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("对话轮次执行失败")
            metrics.inc("sxd_chat_failed_total")
            # 对外只回类型 + 通用文案：异常原文常含上游响应体或内部路径，
            # 不该回给匿名客户端。原文只进日志与 trace。
            push("error", {
                "type": "error",
                "message": "对话执行失败，请稍后重试或联系管理员",
                "error_type": type(exc).__name__,
            })
        else:
            await sessions.append_assistant(
                session,
                reply,
                proposals=proposals,
                intent=intent,
                evidence_titles=evidence_titles,
            )
            tracer.emit(
                "chat_turn_done",
                "conversation",
                proposals=len(proposals),
                reply_chars=len(reply),
                usage=tracer.usage_summary(),
            )
            tracer.persist()
        finally:
            reset_run_context(token)
            push("__eof__", eof)

    async def event_source():
        task = asyncio.create_task(produce())
        try:
            # 先把会话信息发出去，前端才能显示 session 并支持「新会话」
            yield _frame("session", {"session_id": session.session_id, "turns": len(session.turns)})
            while True:
                if await request.is_disconnected():
                    logger.info("客户端断开对话流", extra={"session_id": session.session_id})
                    break
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if kind == "__eof__":
                    break
                yield _frame(kind, data)
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    return StreamingResponse(event_source(), media_type="text/event-stream", headers=SSE_HEADERS)


def _frame(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.get("/session/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    session = await sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return session.to_dict()


@router.post("/reset")
async def reset_session(payload: ChatResetRequest) -> dict[str, Any]:
    removed = await sessions.reset(payload.session_id)
    return {"ok": True, "removed": removed, "stats": await sessions.stats()}


@router.get("/stats")
async def chat_stats() -> dict[str, Any]:
    return {
        "sessions": await sessions.stats(),
        "models_configured": settings.has_dashscope_key,
        "strategy_count": {
            kind: len(strategies_for(kind)) for kind in ("scene", "figure", "artifact", "style")
        },
    }
