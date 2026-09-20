"""会话存储：Redis ↔ 进程内。

为什么会话要进 Redis（而不是像原来那样留在进程内）
------------------------------------------------
原来是「进程内 OrderedDict + 显式淘汰」，它的问题不是性能，是**行为**：

1. **多副本部署时上下文会漂移** —— 同一用户第二次请求落到另一个副本，
   会话就「失忆」了，前端看到的是一句莫名其妙的回答。
2. **滚动重启丢上下文** —— 发版期间用户正在进行的对话被打断。
3. **无法观测** —— 会话存在哪个副本的内存里，运维侧完全看不到。

Redis 用「键 + TTL + 按最后活跃时间排序的 ZSET」解决这三件事：
TTL 负责回收，ZSET 负责「超出上限时淘汰最久未活动的那个」。

⚠️ 一个必须写下来的语义差异
--------------------------
进程内实现的 `load()` 返回的是**活对象**，`session.append()` 之后即使忘了 `save()`
也能读到最新内容；Redis 实现必须真的 `save()` 才会写回去。
这个差异会让「本地跑得好好的、上了 Redis 就丢上下文」这类问题只在上线后暴露。
所以本仓库的调用约定是：**任何修改会话的地方都必须显式 `await save(session)`**，
并且由 `scripts/check_storage.py` 对 Redis 通道做真实的读写往返验证。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Protocol

from app.conversation.store import (
    MAX_SESSIONS,
    SESSION_TTL_SECONDS,
    ChatSession,
    ChatTurn,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics

logger = get_logger("app.storage.sessions")


class SessionBackend(Protocol):
    name: str

    async def load(self, session_id: str) -> ChatSession | None: ...

    async def save(self, session: ChatSession) -> None: ...

    async def delete(self, session_id: str) -> bool: ...

    async def stats(self) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


class MemorySessionBackend:
    """进程内实现（无 Redis 时的行为，与原实现语义一致）。"""

    name = "memory"

    def __init__(
        self,
        *,
        max_sessions: int = MAX_SESSIONS,
        ttl_seconds: float = SESSION_TTL_SECONDS,
    ) -> None:
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, ChatSession] = OrderedDict()
        self.max_sessions = max_sessions
        self.ttl = ttl_seconds
        self.evicted_total = 0

    async def load(self, session_id: str) -> ChatSession | None:
        with self._lock:
            self._evict_locked()
            session = self._sessions.get(session_id)
            if session is not None:
                self._sessions.move_to_end(session_id)
            return session

    async def save(self, session: ChatSession) -> None:
        # 注意：这里存的就是传进来的那个对象本身（活引用），所以 save 对进程内实现
        # 只是「维护 LRU 顺序 + 触发淘汰」，不是真正的序列化。别把这个当成通用语义。
        with self._lock:
            self._sessions[session.session_id] = session
            self._sessions.move_to_end(session.session_id)
            self._evict_locked()

    async def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    async def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": self.name,
                "sessions": len(self._sessions),
                "evicted_total": self.evicted_total,
                "ttl_seconds": self.ttl,
                "max_sessions": self.max_sessions,
            }

    async def aclose(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _evict_locked(self) -> None:
        now = time.time()
        expired = [
            sid
            for sid, session in self._sessions.items()
            if now - session.updated_at > self.ttl
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
            self.evicted_total += 1

        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)
            self.evicted_total += 1


class RedisSessionBackend:
    """Redis 实现：字符串存会话 JSON + ZSET 按活跃时间排序。"""

    name = "redis"

    def __init__(
        self,
        url: str,
        *,
        prefix: str = "sxd",
        ttl_seconds: int = 1800,
        max_sessions: int = 200,
        timeout: float = 3.0,
    ) -> None:
        import redis.asyncio as redis_async

        self._prefix = prefix.rstrip(":")
        self.ttl = ttl_seconds
        self.max_sessions = max_sessions
        self.evicted_total = 0
        self._client = redis_async.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=timeout,
            socket_timeout=max(timeout, 3.0),
            health_check_interval=30,
        )

    # ── 键 ──────────────────────────────────────────────────────────────────
    def _key(self, session_id: str) -> str:
        return f"{self._prefix}:chat:{session_id}"

    @property
    def _index_key(self) -> str:
        return f"{self._prefix}:chat:index"

    # ── 生命周期 ────────────────────────────────────────────────────────────
    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭 Redis 连接失败: %s", exc)

    # ── 读写 ────────────────────────────────────────────────────────────────
    async def load(self, session_id: str) -> ChatSession | None:
        payload = await self._client.get(self._key(session_id))
        if not payload:
            return None
        try:
            session = ChatSession.from_dict(json.loads(payload))
        except (ValueError, TypeError) as exc:
            # 结构不兼容（老版本写入的格式）时丢掉这条，而不是抛出去让整个对话 500
            logger.warning("会话 %s 反序列化失败，按过期处理: %s", session_id, exc)
            await self.delete(session_id)
            return None
        if not session.session_id:
            session.session_id = session_id
        return session

    async def save(self, session: ChatSession) -> None:
        payload = json.dumps(session.to_dict(), ensure_ascii=False)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.set(self._key(session.session_id), payload, ex=self.ttl)
            pipe.zadd(self._index_key, {session.session_id: session.updated_at})
            # 顺带清掉索引里已经过期（超过 TTL）的条目，防止 ZSET 无限增长
            pipe.zremrangebyscore(self._index_key, "-inf", time.time() - self.ttl)
            await pipe.execute()
        await self._enforce_capacity()

    async def delete(self, session_id: str) -> bool:
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.delete(self._key(session_id))
            pipe.zrem(self._index_key, session_id)
            removed, _ = await pipe.execute()
        return bool(removed)

    async def stats(self) -> dict[str, Any]:
        total = int(await self._client.zcard(self._index_key))
        info: dict[str, Any] = {}
        try:
            info = await self._client.info("memory")
        except Exception:  # noqa: BLE001 - stats 不该因为拿不到内存信息而失败
            info = {}
        return {
            "backend": self.name,
            "sessions": total,
            "evicted_total": self.evicted_total,
            "ttl_seconds": self.ttl,
            "max_sessions": self.max_sessions,
            "used_memory_human": info.get("used_memory_human"),
        }

    async def _enforce_capacity(self) -> None:
        """超出上限时淘汰「最久未活动」的会话。

        Redis 的 TTL 只能管「多久没动过」，管不了「总数上限」——
        内存压力来自总数，所以这两件事必须分开做。
        """
        total = int(await self._client.zcard(self._index_key))
        overflow = total - self.max_sessions
        if overflow <= 0:
            return
        stale = await self._client.zrange(self._index_key, 0, overflow - 1)
        if not stale:
            return
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.delete(*[self._key(sid) for sid in stale])
            pipe.zrem(self._index_key, *stale)
            await pipe.execute()
        self.evicted_total += len(stale)
        metrics.inc("sxd_session_evicted_total", float(len(stale)))


class SessionRepository:
    """对上层唯一可见的会话存储门面。"""

    def __init__(self) -> None:
        self.backend: SessionBackend = MemorySessionBackend()
        self.attempted = False
        self.reason = "尚未连接"
        self._lock = asyncio.Lock()

    @property
    def backend_name(self) -> str:
        return self.backend.name

    async def connect(self) -> bool:
        async with self._lock:
            if isinstance(self.backend, RedisSessionBackend):
                return True
            self.attempted = True
            url = settings.redis_url.strip()
            if not url:
                self.reason = "未配置 REDIS_URL"
                metrics.set_gauge("sxd_session_store_redis", 0)
                return False
            candidate = RedisSessionBackend(
                url,
                prefix=settings.redis_key_prefix,
                ttl_seconds=settings.redis_session_ttl_seconds,
                max_sessions=settings.redis_max_sessions,
                timeout=settings.redis_connect_timeout,
            )
            try:
                await candidate.ping()
            except Exception as exc:  # noqa: BLE001 - 连不上就降级，不阻断启动
                self.reason = f"{type(exc).__name__}: {exc}"
                logger.warning("Redis 不可用，会话退回进程内存储: %s", self.reason)
                metrics.record_degradation(
                    component="session_store", reason=self.reason, fallback="memory"
                )
                metrics.set_gauge("sxd_session_store_redis", 0)
                await candidate.aclose()
                return False
            self.backend = candidate
            self.reason = ""
            metrics.set_gauge("sxd_session_store_redis", 1)
            logger.info(
                "会话存储已切换到 Redis（prefix=%s, ttl=%ds, 上限 %d）",
                settings.redis_key_prefix,
                settings.redis_session_ttl_seconds,
                settings.redis_max_sessions,
            )
            return True

    # ── 读写 ────────────────────────────────────────────────────────────────
    async def get_or_create(self, session_id: str | None) -> ChatSession:
        if session_id:
            existing = await self.backend.load(session_id)
            if existing is not None:
                return existing
        new_id = session_id or f"chat-{uuid.uuid4().hex[:10]}"
        session = ChatSession(session_id=new_id)
        await self.backend.save(session)
        return session

    async def get(self, session_id: str) -> ChatSession | None:
        return await self.backend.load(session_id)

    async def append_user(self, session: ChatSession, message: str) -> ChatTurn:
        turn = ChatTurn(role="user", content=message)
        session.append(turn)
        await self.backend.save(session)
        return turn

    async def append_assistant(
        self,
        session: ChatSession,
        content: str,
        *,
        proposals: list[dict[str, Any]] | None = None,
        intent: dict[str, Any] | None = None,
        evidence_titles: list[str] | None = None,
    ) -> ChatTurn:
        turn = ChatTurn(
            role="assistant",
            content=content,
            proposals=proposals or [],
            intent=intent or {},
            evidence_titles=evidence_titles or [],
        )
        session.append(turn)
        if intent:
            session.last_intent = dict(intent)
        await self.backend.save(session)
        return turn

    async def reset(self, session_id: str) -> bool:
        return await self.backend.delete(session_id)

    async def stats(self) -> dict[str, Any]:
        return await self.backend.stats()

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "available": self.backend_name == "redis",
            "configured": bool(settings.redis_url.strip()),
            # 连过但失败要报原因；压根没连过要说「尚未连接」——
            # 两种情况混在一起会让「配了却降级」看起来像「没配」
            "reason": (self.reason or None) if self.attempted else "尚未连接",
        }

    async def aclose(self) -> None:
        await self.backend.aclose()


sessions = SessionRepository()
