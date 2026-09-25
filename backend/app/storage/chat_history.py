"""会话记录的读写（列表 / 详情 / 落库 / 删除）。

与 Redis 会话的分工
------------------
- Redis（app/storage/sessions.py）：**活跃缓存**。读写快，供 `run_chat` 使用，
  有 TTL（1800s）与数量上限、会被淘汰——它是缓存不是记录。
- 本模块（PostgreSQL）：**持久记录**。列表、回看、回温都从这里读。

写失败只记指标：记录没落上最坏是「这条会话找不回来」，
不该让**已经生成好的回复**失败。

所有方法在关键标识缺失时返回空 / False：
没有 session_id 或 user_id 就无从判断「这是谁、哪个会话」，宁可不写。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import get_logger
from app.core.metrics import metrics
from app.storage.db import Database, database
from app.storage.models import ChatMessageRecord, ChatSessionRecord

logger = get_logger("app.storage.chat_history")

_ID_LIMIT = 64
_TITLE_LIMIT = 120


def _clip(value: str | None, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:limit]


class ChatHistoryRepository:
    """会话记录的持久化读写。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def upsert_session(
        self,
        session_id: str,
        user_id: str | None,
        *,
        title: str | None = None,
        turn_count: int | None = None,
    ) -> bool:
        """创建 / 更新会话元信息。

        - `title` 只在**尚未设置**时写入（首条消息截取的标题不该被后续消息覆盖）；
        - `turn_count` 传 None 表示本次不更新轮数。
        """
        if not session_id or not user_id:
            return False
        if not await self.db.ensure_connected():
            return False

        values: dict[str, Any] = {
            "session_id": _clip(session_id, _ID_LIMIT),
            "user_id": _clip(user_id, _ID_LIMIT),
            "last_message_at": func.now(),
        }
        if title:
            values["title"] = _clip(title, _TITLE_LIMIT)
        if turn_count is not None:
            values["turn_count"] = turn_count

        set_columns: dict[str, Any] = {
            "last_message_at": func.now(),
            "updated_at": func.now(),
        }
        if turn_count is not None:
            set_columns["turn_count"] = turn_count

        try:
            async with self.db.session() as session:
                statement = pg_insert(ChatSessionRecord).values(**values)
                if title:
                    # COALESCE(原值, 新值)：已有标题就保留，只在首次或为空时落标题，
                    # 免得后续每轮消息把标题覆盖成最新一句。
                    set_columns["title"] = func.coalesce(
                        ChatSessionRecord.title, statement.excluded.title
                    )
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["session_id"], set_=set_columns
                    )
                )
                await session.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            self._fail("upsert_session", exc)
            return False

    async def list_sessions(
        self, user_id: str | None, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """列出某用户的会话，按最近对话时间倒序。"""
        if not user_id or not await self.db.ensure_connected():
            return []
        try:
            async with self.db.session() as session:
                rows = (
                    (
                        await session.execute(
                            select(ChatSessionRecord)
                            .where(ChatSessionRecord.user_id == _clip(user_id, _ID_LIMIT))
                            .order_by(ChatSessionRecord.last_message_at.desc())
                            .limit(max(1, min(limit, 100)))
                            .offset(max(0, offset))
                        )
                    )
                    .scalars()
                    .all()
                )
            return [
                {
                    "session_id": row.session_id,
                    "title": row.title,
                    "turn_count": row.turn_count,
                    "last_message_at": row.last_message_at.isoformat()
                    if row.last_message_at
                    else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
        except Exception as exc:  # noqa: BLE001
            self._fail("list_sessions", exc)
            return []

    async def get_session_meta(self, session_id: str | None) -> dict[str, Any] | None:
        """取单个会话的元信息（标题 / 轮数等），供详情接口一并返回。"""
        if not session_id or not await self.db.ensure_connected():
            return None
        try:
            async with self.db.session() as session:
                row = (
                    (
                        await session.execute(
                            select(ChatSessionRecord).where(
                                ChatSessionRecord.session_id == _clip(session_id, _ID_LIMIT)
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
            if row is None:
                return None
            return {
                "session_id": row.session_id,
                "user_id": row.user_id,
                "title": row.title,
                "turn_count": row.turn_count,
                "last_message_at": row.last_message_at.isoformat()
                if row.last_message_at
                else None,
            }
        except Exception as exc:  # noqa: BLE001
            self._fail("get_session_meta", exc)
            return None

    async def get_messages(
        self, session_id: str | None, limit: int = 500
    ) -> list[dict[str, Any]]:
        """取某会话的完整消息（正序），用于回看与回温。"""
        if not session_id or not await self.db.ensure_connected():
            return []
        try:
            async with self.db.session() as session:
                rows = (
                    (
                        await session.execute(
                            select(ChatMessageRecord)
                            .where(ChatMessageRecord.session_id == _clip(session_id, _ID_LIMIT))
                            .order_by(
                                ChatMessageRecord.created_at.asc(),
                                ChatMessageRecord.id.asc(),
                            )
                            .limit(max(1, min(limit, 2000)))
                        )
                    )
                    .scalars()
                    .all()
                )
            return [
                {
                    "role": row.role,
                    "content": row.content,
                    "proposals": row.proposals or [],
                    "intent": row.intent or {},
                    "evidence_titles": row.evidence_titles or [],
                    "evidence": row.evidence or [],
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
        except Exception as exc:  # noqa: BLE001
            self._fail("get_messages", exc)
            return []

    async def append_messages(
        self, session_id: str, messages: list[dict[str, Any]]
    ) -> bool:
        """批量追加消息（一轮对话的 user + assistant）。"""
        if not session_id or not messages or not await self.db.ensure_connected():
            return False
        rows: list[dict[str, Any]] = []
        for item in messages:
            role = str(item.get("role") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            rows.append(
                {
                    "session_id": _clip(session_id, _ID_LIMIT),
                    "role": role,
                    "content": str(item.get("content") or ""),
                    "proposals": list(item.get("proposals") or []),
                    "intent": dict(item.get("intent") or {}),
                    "evidence_titles": list(item.get("evidence_titles") or []),
                    "evidence": list(item.get("evidence") or []),
                }
            )
        if not rows:
            return False
        try:
            async with self.db.session() as session:
                await session.execute(pg_insert(ChatMessageRecord).values(rows))
                await session.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            self._fail("append_messages", exc)
            return False

    async def delete_all_sessions(self, user_id: str | None) -> int:
        """清空某用户的全部会话记录（消息 + 元信息），返回删除的会话数。

        与 delete_session 的区别：这是「清空历史」，按 user_id 批量删；
        单条删除用 delete_session。两者都只删「聊过什么」，
        **不动 user_facts**——那是长期画像，由调用方决定是否一起清。
        """
        if not user_id or not await self.db.ensure_connected():
            return 0
        try:
            async with self.db.session() as session:
                # 先查出该用户的会话 id，再按 id 删消息（消息表按 session_id 索引）
                rows = (
                    (
                        await session.execute(
                            select(ChatSessionRecord.session_id).where(
                                ChatSessionRecord.user_id == _clip(user_id, _ID_LIMIT)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                ids = [str(row) for row in rows]
                if not ids:
                    return 0
                await session.execute(
                    delete(ChatMessageRecord).where(ChatMessageRecord.session_id.in_(ids))
                )
                await session.execute(
                    delete(ChatSessionRecord).where(
                        ChatSessionRecord.user_id == _clip(user_id, _ID_LIMIT)
                    )
                )
                await session.commit()
            return len(ids)
        except Exception as exc:  # noqa: BLE001
            self._fail("delete_all_sessions", exc)
            # -1 = 失败；0 = 本来就没有会话。两者必须区分，
            # 否则「清空没生效」会被当成「本来就是空的」而无人察觉。
            return -1

    async def delete_session(self, session_id: str) -> bool:
        """删除会话记录（消息 + 元信息）。

        只删「聊过什么」，**不碰** user_facts（那是「这个人是谁」，
        属于长期画像，与会话生命周期无关）。
        """
        if not session_id or not await self.db.ensure_connected():
            return False
        try:
            async with self.db.session() as session:
                await session.execute(
                    delete(ChatMessageRecord).where(
                        ChatMessageRecord.session_id == _clip(session_id, _ID_LIMIT)
                    )
                )
                await session.execute(
                    delete(ChatSessionRecord).where(
                        ChatSessionRecord.session_id == _clip(session_id, _ID_LIMIT)
                    )
                )
                await session.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            self._fail("delete_session", exc)
            return False

    @staticmethod
    def _fail(operation: str, exc: Exception) -> None:
        logger.warning("会话记录读写失败 [%s]，不影响主流程: %s", operation, exc)
        metrics.inc("sxd_db_error_total", operation=operation)


chat_history = ChatHistoryRepository(database)
