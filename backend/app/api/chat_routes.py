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

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.conversation.agent import run_chat
from app.conversation.profile import extract_facts, format_profile
from app.conversation.store import ChatSession
from app.conversation.strategies import strategies_for
from app.core.config import settings
from app.core.context import RunContext, reset_run_context, set_run_context
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.core.tracing import TraceEvent, TraceRecorder
from app.storage.chat_history import chat_history
from app.storage.profile import user_facts
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
    user_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description=(
            "长期画像的用户标识。留空表示「不知道这是谁」：本轮不读写长期记忆，"
            "只有会话内的短期记忆（行为与不传完全一致）"
        ),
    )


class ChatResetRequest(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


@router.post("/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    session: ChatSession = await sessions.get_or_create(payload.session_id)
    await sessions.append_user(session, payload.message)

    # 长期画像：只有传了 user_id 才读。没有用户标识时无从归属，
    # 本轮退化为「仅短期记忆」，行为与加画像之前完全一致。
    profile_text = ""
    if payload.user_id:
        profile_text = format_profile(await user_facts.list_facts(payload.user_id))

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
        # 完整引用块：回看时要能点开出处的原文与链接，光有标题不够
        evidence_items: list[dict[str, Any]] = []
        try:
            async for event in run_chat(
                session, payload.message, tracer, user_profile=profile_text
            ):
                if event["type"] == "message":
                    reply = str(event.get("text") or "")
                elif event["type"] == "proposals":
                    proposals = event.get("items") or []
                elif event["type"] == "intent":
                    intent = event.get("intent") or {}
                elif event["type"] == "evidence":
                    evidence_items = list(event.get("items") or [])[:4]
                    evidence_titles = [str(item.get("title")) for item in evidence_items]
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
            # 会话记录：元信息（供列表）+ 本轮两条消息（供回看 / 回温）。
            # 没有 user_id 就无从判断「这是谁的会话」，此时不记——
            # 宁可缺一条记录，也不要记下一条无法归属、别人可能看到的会话。
            if payload.user_id:
                await chat_history.upsert_session(
                    session.session_id,
                    payload.user_id,
                    # 标题取首条用户消息：upsert 内部只在标题为空时才写入，
                    # 所以后续每轮传进来也不会把标题冲掉。
                    title=payload.message,
                    turn_count=sum(1 for turn in session.turns if turn.role == "user"),
                )
                await chat_history.append_messages(
                    session.session_id,
                    [
                        {"role": "user", "content": payload.message},
                        {
                            "role": "assistant",
                            "content": reply,
                            "proposals": proposals,
                            "intent": intent,
                            "evidence_titles": evidence_titles,
                            "evidence": evidence_items,
                        },
                    ],
                )
            # 长期记忆：本轮用户若主动陈述了个人信息（名字、稳定偏好），落库供后续会话使用。
            # 只有传了 user_id 才抽——没有用户标识时无从归属，也不该产生写库副作用。
            if payload.user_id:
                for fact in await extract_facts(payload.message):
                    await user_facts.upsert(
                        payload.user_id,
                        fact["key"],
                        fact["value"],
                        source_session_id=session.session_id,
                    )
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


@router.get("/sessions")
async def list_chat_sessions(
    user_id: str = Query(..., pattern=r"^[A-Za-z0-9_-]{1,64}$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """列出某用户的历史会话，按最近一次对话倒序。

    `user_id` 必填：不传就无法判断「这是谁的会话」，
    宁可直接拒绝，也不要把别人的会话列出来。
    """
    items = await chat_history.list_sessions(user_id, limit=limit, offset=offset)
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/sessions/{session_id}")
async def get_chat_session(session_id: str) -> dict[str, Any]:
    """取某会话的完整消息（回看）。

    即使 Redis 里的活跃会话已过期也能回看——消息来自持久记录，不是缓存。
    """
    messages = await chat_history.get_messages(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="会话记录不存在")
    return {
        "session_id": session_id,
        "meta": await chat_history.get_session_meta(session_id),
        "messages": messages,
    }


@router.delete("/sessions")
async def clear_chat_sessions(
    user_id: str = Query(..., pattern=r"^[A-Za-z0-9_-]{1,64}$"),
) -> dict[str, Any]:
    """清空某用户的全部会话记录。

    只清「聊过什么」，**不动** `user_facts`（长期画像）——
    要连画像一起忘掉，另外调 `DELETE /api/profile/{user_id}`。
    """
    removed = await chat_history.delete_all_sessions(user_id)
    # removed < 0 表示失败；0 表示「本来就没有会话」。两者语义不同，如实返回。
    return {"ok": removed >= 0, "removed": removed, "user_id": user_id}


@router.delete("/sessions/{session_id}")
async def delete_chat_session(session_id: str) -> dict[str, Any]:
    """删除会话：**缓存与记录一起删**。

    只删「聊过什么」，不动 `user_facts`——那是「这个人是谁」，
    属于长期画像，与会话生命周期无关。
    """
    await sessions.reset(session_id)
    removed = await chat_history.delete_session(session_id)
    return {"ok": True, "removed": removed, "session_id": session_id}
