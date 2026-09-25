"""用户画像事实的读写（长期记忆，PostgreSQL）。

失败隔离与任务仓储一致：画像写失败只记指标，不影响对话主流程。
用户说了名字却没存上，最坏结果是「下次不认识他」，
不该让**这一轮已经生成好的回复**失败。

无 user_id 时所有方法都是空转（返回空 / False）——
这样前端还没传用户标识时，系统退化成「只有短期记忆」，行为与改动前完全一致。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import get_logger
from app.core.metrics import metrics
from app.storage.db import Database, database
from app.storage.models import UserFact

logger = get_logger("app.storage.profile")

_ID_LIMIT = 64
_KEY_LIMIT = 64


def _clip(value: str | None, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:limit]


class UserFactRepository:
    """按 user_id 存长期事实（名字、稳定偏好等）。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def list_facts(self, user_id: str | None, limit: int = 50) -> list[dict[str, Any]]:
        """取某用户的画像事实，按最近更新排序。"""
        if not user_id or not await self.db.ensure_connected():
            return []
        try:
            async with self.db.session() as session:
                rows = (
                    (
                        await session.execute(
                            select(UserFact)
                            .where(UserFact.user_id == _clip(user_id, _ID_LIMIT))
                            .order_by(UserFact.updated_at.desc())
                            .limit(limit)
                        )
                    )
                    .scalars()
                    .all()
                )
            return [{"key": row.key, "value": row.value} for row in rows]
        except Exception as exc:  # noqa: BLE001
            self._fail("list_user_facts", exc)
            return []

    async def upsert(
        self,
        user_id: str | None,
        key: str,
        value: str,
        *,
        source_session_id: str | None = None,
    ) -> bool:
        """写入/覆盖一条事实。(user_id, key) 冲突时覆盖 value 与来源。

        覆盖而不是追加：用户改口说「叫我老肖」时，表里应当只剩一条 name，
        而不是同时躺着互相矛盾的两个名字。
        """
        if not user_id or not key or not value:
            return False
        if not await self.db.ensure_connected():
            return False
        values = {
            "user_id": _clip(user_id, _ID_LIMIT),
            "key": _clip(key, _KEY_LIMIT),
            "value": value,
            "source_session_id": _clip(source_session_id, _ID_LIMIT),
        }
        try:
            async with self.db.session() as session:
                statement = pg_insert(UserFact).values(**values)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["user_id", "key"],
                        set_={
                            "value": statement.excluded.value,
                            "source_session_id": statement.excluded.source_session_id,
                            "updated_at": func.now(),
                        },
                    )
                )
                await session.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            self._fail("upsert_user_fact", exc)
            return False

    async def delete_all_facts(self, user_id: str | None) -> int:
        """删除某用户的全部长期画像事实，返回删除条数。

        这是「清除记忆」的另一半：会话记录管「聊过什么」，画像管「你是谁」。
        用户要求彻底忘记时，两件都要清——但由**调用方**决定，
        因为「清空历史会话」和「连画像一起忘掉」是两种不同的意图。
        """
        if not user_id or not await self.db.ensure_connected():
            return 0
        try:
            async with self.db.session() as session:
                result = await session.execute(
                    delete(UserFact).where(UserFact.user_id == _clip(user_id, _ID_LIMIT))
                )
                await session.commit()
            return int(result.rowcount or 0)
        except Exception as exc:  # noqa: BLE001
            self._fail("delete_all_facts", exc)
            # -1 表示「失败了」，与「删了 0 条（本来就没数据）」区分开：
            # 用户主动点「清除记忆」却静默没生效，是最难发现也最不能接受的情况。
            return -1

    @staticmethod
    def _fail(operation: str, exc: Exception) -> None:
        logger.warning("用户画像读写失败 [%s]，不影响主流程: %s", operation, exc)
        metrics.inc("sxd_db_error_total", operation=operation)


user_facts = UserFactRepository(database)
