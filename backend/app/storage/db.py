"""PostgreSQL 连接门面（任务 / 产物 / 质检记录 / 语料向量）。

三条纪律
--------
1. **连不上不算致命**：返回 `available=False`，上层继续用进程内实现。
   本项目的每个外部依赖（LLM、出图、检索、存储）都必须「不配也能启动」。
2. **连接由存储层自己按需建立**（`ensure_connected`），不依赖调用方记得先 `connect()`。
   这条是被真实事故逼出来的：`scripts/smoke_test.py` 直接跑 `runner.astream()`，
   从不经过 FastAPI 的 lifespan，于是所有落库**静默 no-op**，脚本却照样报 `SMOKE PASS`。
   把「连接的建立权」留在调用方手里，就等于留了一个「不报错、只是什么都没写」的坑。
3. **探测有冷却**：连不上时不会每个写入点都重试一次 —— 一次连接超时 5s，
   叠加到每个写入点上会把「数据库挂了」放大成「每个任务慢十倍」。
4. **状态如实上报**：失败原因原样保留在 `reason` 里，`/api/health` 直接透出。
   最坏的情况不是「数据库没连上」，而是**配了 DATABASE_URL 却没连上、日志里还写一切正常**。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics

logger = get_logger("app.storage.db")


class Database:
    # 连不上时的重试冷却（秒）。见 ensure_connected 的注释。
    RETRY_COOLDOWN_SECONDS = 30.0

    def __init__(self) -> None:
        self.engine: AsyncEngine | None = None
        self.session_factory: async_sessionmaker[AsyncSession] | None = None
        self.available = False
        self.attempted = False
        self.reason = "尚未连接"
        self.server_version = ""
        self.vector_version = ""
        self.schema_revision = ""
        self._lock = asyncio.Lock()
        self._last_attempt_at = 0.0
        # 连接池绑定在创建它的那个事件循环上，见 ensure_connected 的注释
        self._loop_id = 0

    # ── 生命周期 ────────────────────────────────────────────────────────────
    @property
    def configured(self) -> bool:
        return bool(settings.database_url.strip())

    @staticmethod
    def _current_loop_id() -> int:
        try:
            return id(asyncio.get_running_loop())
        except RuntimeError:
            return 0

    async def ensure_connected(self) -> bool:
        """确保已连接：幂等、按需、带冷却。

        **为什么不能让调用方负责连接**：`scripts/` 下的脚本
        （`smoke_test.py` / `check_revision_effect.py` / `probe_*`）会直接调用
        `RestorationRunner.astream()`，不经过 FastAPI 的 lifespan，也就从没调用过
        `connect()`。此时 `available` 一直是 False，所有落库静默 no-op ——
        而脚本的判定只看图有没有出来，照样报 `SMOKE PASS`。

        实测就是这么踩的：冒烟通过、库里 0 行。这类「不报错、只是什么都没写」
        的失败，比抛异常危险得多。所以连接的建立权收回到存储层自己手里。

        冷却的意义：真连不上时，每个写入点都试一次 5s 超时会把整条链路拖慢十倍；
        冷却期内直接返回失败，让降级保持「廉价」。
        """
        if self.available:
            if self._loop_id == self._current_loop_id():
                return True
            # 连接池绑定在**创建它的那个事件循环**上。换循环后再用（脚本里
            # `for t in tasks: asyncio.run(run_once(t))` 是很自然的写法），
            # 会拿到属于旧循环的连接：asyncpg 抛 "Event loop is closed"，
            # 而写入失败被上层当降级吞掉 —— 表现是**任务照跑、数据不落库**。
            # 实测踩过一次：`ensure_task` 没写进父行，随后 verdict 直接外键违约。
            logger.warning("检测到事件循环已切换，重建数据库连接池")
            metrics.inc("sxd_db_loop_switch_total")
            self._discard_engine_for_loop_switch()
        if not self.configured:
            return False
        now = time.monotonic()
        if self._last_attempt_at and now - self._last_attempt_at < self.RETRY_COOLDOWN_SECONDS:
            return False
        self._last_attempt_at = now
        return await self.connect()

    async def connect(self) -> bool:
        """建立连接池并做一次真实探测（不是 `SELECT 1` 就算数）。"""
        if self.available:
            return True
        async with self._lock:
            if self.available:
                return True
            self.attempted = True
            if not self.configured:
                self.reason = "未配置 DATABASE_URL"
                metrics.set_gauge("sxd_db_available", 0)
                return False
            try:
                self.engine = create_async_engine(
                    settings.database_url,
                    echo=settings.db_echo,
                    pool_size=settings.db_pool_size,
                    max_overflow=settings.db_max_overflow,
                    # 连接被中间层静默掐断是常态，取连接前先探活，避免把
                    # 「连接已失效」报成业务错误
                    pool_pre_ping=True,
                    connect_args={"timeout": settings.db_connect_timeout},
                )
                async with self.engine.connect() as conn:
                    self.server_version = str(
                        (await conn.execute(text("show server_version"))).scalar_one()
                    )
                    extension = (
                        await conn.execute(
                            text("select extversion from pg_extension where extname = 'vector'")
                        )
                    ).scalar_one_or_none()
                    self.vector_version = str(extension or "")
                    self.schema_revision = str(
                        (
                            await conn.execute(
                                text("select version_num from alembic_version")
                            )
                        ).scalar_one_or_none()
                        or ""
                    ) if await _table_exists(conn, "alembic_version") else ""
                self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
                self.available = True
                self._loop_id = self._current_loop_id()
                self.reason = ""
                metrics.set_gauge("sxd_db_available", 1)
                logger.info(
                    "PostgreSQL 已连接: %s（pgvector %s，schema %s）",
                    self.server_version,
                    self.vector_version or "未安装",
                    self.schema_revision or "未初始化",
                )
                return True
            except Exception as exc:  # noqa: BLE001 - 任何失败都降级，不阻断启动
                self.reason = f"{type(exc).__name__}: {exc}"
                logger.warning("PostgreSQL 不可用，任务与产物将不落库: %s", self.reason)
                metrics.record_degradation(
                    component="database", reason=self.reason, fallback="in_process_only"
                )
                metrics.set_gauge("sxd_db_available", 0)
                await self._dispose_engine()
                return False

    async def aclose(self) -> None:
        await self._dispose_engine()

    def _discard_engine_for_loop_switch(self) -> None:
        """换事件循环时**直接丢弃**引擎，不走 `dispose()`。

        旧循环已经关闭，池里的连接无法再被正确关闭：`dispose()` 会逐个调用
        `connection.terminate()`，每个都抛 "Event loop is closed"，
        由 SQLAlchemy 的 pool logger 打成 ERROR —— 一屏噪音，而它描述的
        是一件已经无法挽回、也不需要挽回的事（那些连接随旧循环一起死了）。
        直接丢引用让 GC 回收更诚实。

        `asyncio.Lock` 也会绑定到首次使用的事件循环，所以必须同时换一把新的，
        否则下一次 connect() 会在 `async with self._lock` 上直接抛
        "is bound to a different event loop"。
        """
        self.engine = None
        self.session_factory = None
        self.available = False
        self._loop_id = 0
        self._last_attempt_at = 0.0
        self._lock = asyncio.Lock()

    async def _dispose_engine(self) -> None:
        self.session_factory = None
        self.available = False
        # 显式关闭后允许立刻重连（冷却只用于「自动重试」，不该拦住主动重连）
        self._last_attempt_at = 0.0
        engine, self.engine = self.engine, None
        if engine is not None:
            try:
                await engine.dispose()
            except Exception as exc:  # noqa: BLE001
                logger.warning("关闭数据库连接池失败: %s", exc)

    # ── 会话 ────────────────────────────────────────────────────────────────
    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """取一个数据库会话。

        **必须先 `ensure_connected()`**：这是对一次真实事故的补漏。
        上一轮给 `ensure_connected` 加了「事件循环切换 → 重建连接池」的检测，
        但只有 `repository` 的写入方法走了它；`session()` 仍然直接拿旧连接池用。

        后果很隐蔽：脚本里两次 `asyncio.run()`（例如先 build 检索器、再跑评测）
        会让第二次的查询拿到属于已关闭循环的连接，抛 `Event loop is closed`；
        而检索层把这类异常当降级吞掉，**静默退回纯 BM25** ——
        页面上一切正常，只有「语义召回没了」这一个症状，没有任何报错指向它。
        """
        await self.ensure_connected()
        if not self.available or self.session_factory is None:
            raise RuntimeError("数据库不可用：请先 await database.connect()")
        async with self.session_factory() as session:
            yield session

    async def fetch_all(self, statement: Any, params: dict[str, Any] | None = None) -> list[Any]:
        async with self.session() as session:
            result = await session.execute(statement, params or {})
            return list(result.fetchall())

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> int:
        """执行不返回结果集的语句（DDL / DELETE），返回受影响行数。

        和 fetch_all 分开是必要的：对 DELETE 调 `result.fetchall()` 不会返回空列表，
        而是抛 `ResourceClosedError` —— 一个只在「删成功」时才出现的异常。
        """
        async with self.session() as session:
            result = await session.execute(statement, params or {})
            await session.commit()
            return int(result.rowcount or 0)

    # ── 迁移 ────────────────────────────────────────────────────────────────
    async def upgrade(self) -> bool:
        """把 schema 升到最新（Alembic 是同步 API，丢到线程里跑）。"""
        if not self.available or not settings.db_auto_migrate:
            return False
        try:
            head = await asyncio.to_thread(_head_revision)
        except Exception as exc:  # noqa: BLE001 - 读不到 head 不致命，照常尝试升级
            logger.warning("无法读取 Alembic head，直接尝试升级: %s", exc)
            head = ""
        if head and self.schema_revision == head:
            # 已经是最新就别跑迁移：否则每次启动都会刷一屏 Alembic 的 INFO 日志，
            # 把真正重要的启动信息（存储档位、出图通道）淹掉
            return True
        try:
            await asyncio.to_thread(_run_alembic_upgrade)
        except Exception as exc:  # noqa: BLE001
            logger.error("数据库迁移失败（服务继续启动，但表可能不完整）: %s", exc)
            metrics.record_degradation(
                component="database_migration", reason=str(exc), fallback="skip"
            )
            return False
        # 迁移后重新读一次版本号，让 /api/health 报的是迁移**之后**的真实版本
        async with self.session() as session:
            self.schema_revision = str(
                (await session.execute(text("select version_num from alembic_version"))).scalar_one_or_none()
                or ""
            )
        logger.info("数据库 schema 已就绪: %s", self.schema_revision or "未知")
        return True

    # ── 上报 ────────────────────────────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        return {
            "backend": "postgresql+asyncpg" if self.available else "disabled",
            "available": self.available,
            "configured": self.configured,
            "server_version": self.server_version or None,
            "pgvector_version": self.vector_version or None,
            "schema_revision": self.schema_revision or None,
            "reason": (self.reason or None) if self.attempted else "尚未连接",
        }


async def _table_exists(conn: Any, name: str) -> bool:
    result = await conn.execute(text("select to_regclass(:name)"), {"name": f"public.{name}"})
    return result.scalar_one_or_none() is not None


def alembic_config() -> Any:
    """构造 Alembic 配置。

    DATABASE_URL 不在 ini 里，而是在 `migrations/env.py` 从 settings 读取 ——
    所以命令行迁移与启动时自动迁移走的是**同一套**连接参数，
    不会出现「CLI 能升、服务启动却报缺表」这种最难查的错位。
    """
    from alembic.config import Config

    backend_root = settings.resolve(".")
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    return config


def head_revision() -> str:
    """迁移脚本目录里的最新版本号（不需要连数据库）。"""
    from alembic.script import ScriptDirectory

    return str(ScriptDirectory.from_config(alembic_config()).get_current_head() or "")


def _head_revision() -> str:
    return head_revision()


def _run_alembic_upgrade() -> None:
    """同步执行 `alembic upgrade head`。"""
    from alembic import command

    command.upgrade(alembic_config(), "head")


database = Database()
