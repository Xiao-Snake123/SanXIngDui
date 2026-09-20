"""存储层门面：PostgreSQL（任务/产物/质检/向量）+ Redis（会话）。

装配顺序有意固定为 **数据库 → 迁移 → Redis**：
先确认「表在不在」，再确认「会话能存到哪」，最后才对外说「存储就绪」。
每一步失败都只降级、不抛异常 —— 存储层的可用性不该成为服务能否启动的前提。

`health()` 的返回值会被 `/api/health` 原样透出，包括失败原因。
这是刻意的：本项目最忌讳的不是「数据库没连上」，而是**配了却连不上、还没人知道**。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.core.metrics import metrics
from app.storage.db import Database, database
from app.storage.repository import TaskRepository
from app.storage.sessions import SessionRepository, sessions

logger = get_logger("app.storage")

tasks = TaskRepository(database)

__all__ = ["Database", "TaskRepository", "SessionRepository", "database", "sessions", "tasks"]


async def bootstrap() -> dict[str, Any]:
    """启动装配。返回一份可直接放进健康检查的存储状态。"""
    await database.connect()
    if database.available:
        await database.upgrade()
    await sessions.connect()

    report = health()
    if not report["database"]["available"]:
        logger.warning(
            "未启用 PostgreSQL：任务/产物/质检记录不会落库，"
            "指标统计只能用进程内计数器（重启归零）。原因：%s",
            report["database"]["reason"],
        )
    if not report["sessions"]["available"]:
        logger.warning(
            "未启用 Redis：会话存在进程内，多副本部署会丢上下文。原因：%s",
            report["sessions"]["reason"],
        )
    return report


async def shutdown() -> None:
    await sessions.aclose()
    await database.aclose()


def health() -> dict[str, Any]:
    database_report = database.health()
    return {
        "database": database_report,
        "sessions": sessions.health(),
        "vector": {
            # 向量库与数据库同库（pgvector），所以这里报的是「能不能用 pgvector」，
            # 而不是另一个独立服务的状态
            "backend": "pgvector" if (database.available and database.vector_version) else "memory",
            "pgvector_version": database.vector_version or None,
            "dimension": settings_dimension(),
            "reason": None
            if (database.available and database.vector_version)
            else (
                database_report["reason"]
                if not database.available
                else "数据库已连接但未安装 pgvector 扩展"
            ),
        },
    }


def settings_dimension() -> int:
    from app.core.config import settings

    return int(settings.vector_dim)


def record_gauges(report: dict[str, Any]) -> None:
    """把存储可用性打进指标，方便告警（0/1 比「看一眼日志」可靠）。"""
    metrics.set_gauge(
        "sxd_storage_database", 1 if report.get("database", {}).get("available") else 0
    )
    metrics.set_gauge(
        "sxd_storage_sessions", 1 if report.get("sessions", {}).get("available") else 0
    )
    metrics.set_gauge(
        "sxd_storage_vector",
        1 if report.get("vector", {}).get("backend") == "pgvector" else 0,
    )
