"""全链路追踪。

设计要点
--------
1. **一份数据，两处消费**：每个节点发出的 TraceEvent 既推给 SSE 队列（前端实时看
   Agent 轨迹），也落盘成 JSONL（离线复盘 + 评估回溯）。
2. **零耦合**：Agent 节点只依赖 TraceRecorder，不关心是自己被 LangGraph 调用还是被
   内置调度器调用，也不关心下游是 SSE 还是文件。
3. **串行序号**：seq 单调递增，前端可据此断线重连后去重续传。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.trace")

Sink = Callable[["TraceEvent"], None]


@dataclass(slots=True)
class TraceEvent:
    seq: int
    task_id: str
    kind: str
    node: str
    ts: str
    payload: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class TraceRecorder:
    """一次复原任务的轨迹记录器。"""

    def __init__(self, task_id: str | None = None, sink: Sink | None = None) -> None:
        self.task_id = task_id or f"sxd-{uuid.uuid4().hex[:12]}"
        self._sink = sink
        self._events: list[TraceEvent] = []
        self._seq = 0
        self._started = time.perf_counter()

    # ── 核心写入 ────────────────────────────────────────────────────────────
    def emit(
        self,
        kind: str,
        node: str,
        *,
        duration_ms: float | None = None,
        **payload: Any,
    ) -> TraceEvent:
        self._seq += 1
        event = TraceEvent(
            seq=self._seq,
            task_id=self.task_id,
            kind=kind,
            node=node,
            ts=_now_iso(),
            payload=_sanitize(payload),
            duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
        )
        self._events.append(event)
        if self._sink is not None:
            with contextlib.suppress(Exception):
                self._sink(event)
        logger.debug("trace", extra={"task_id": self.task_id, "node": node, "kind": kind})
        return event

    @contextlib.contextmanager
    def span(self, node: str, **payload: Any) -> Iterator[dict[str, Any]]:
        """同步计时上下文。用法::

        with tracer.span("retrieval", query=query) as out:
            out["hits"] = retriever.search(query)
        """
        self.emit("node_start", node, **payload)
        box: dict[str, Any] = {}
        started = time.perf_counter()
        try:
            yield box
        except Exception as exc:  # noqa: BLE001 - 需要把异常也写进轨迹再上抛
            self.emit(
                "error",
                node,
                duration_ms=(time.perf_counter() - started) * 1000,
                error=type(exc).__name__,
                message=str(exc),
            )
            raise
        else:
            self.emit(
                "node_end",
                node,
                duration_ms=(time.perf_counter() - started) * 1000,
                **box,
            )

    @contextlib.asynccontextmanager
    async def aspan(self, node: str, **payload: Any):
        self.emit("node_start", node, **payload)
        box: dict[str, Any] = {}
        started = time.perf_counter()
        try:
            yield box
        except Exception as exc:  # noqa: BLE001
            self.emit(
                "error",
                node,
                duration_ms=(time.perf_counter() - started) * 1000,
                error=type(exc).__name__,
                message=str(exc),
            )
            raise
        else:
            self.emit(
                "node_end",
                node,
                duration_ms=(time.perf_counter() - started) * 1000,
                **box,
            )

    # ── 便捷方法 ────────────────────────────────────────────────────────────
    def llm(self, node: str, *, model: str, latency_ms: float, usage: dict | None = None, **extra: Any) -> None:
        self.emit("llm_call", node, model=model, latency_ms=round(latency_ms, 1), usage=usage or {}, **extra)

    def degraded(self, node: str, reason: str, fallback: str) -> None:
        self.emit("degraded", node, reason=reason, fallback=fallback)

    # ── 汇总 ────────────────────────────────────────────────────────────────
    @property
    def events(self) -> list[TraceEvent]:
        return list(self._events)

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000

    def timeline(self) -> list[dict[str, Any]]:
        """面向前端的紧凑时间线（只保留 node_start / node_end / qa / degraded / error）。"""
        keep = {"node_start", "node_end", "qa_verdict", "degraded", "error", "image_ready", "final"}
        return [event.to_dict() for event in self._events if event.kind in keep]

    def usage_summary(self) -> dict[str, Any]:
        """累计 token 用量与模型调用次数 —— 用于成本观测。"""
        calls = 0
        prompt_tokens = 0
        completion_tokens = 0
        models: dict[str, int] = {}
        for event in self._events:
            if event.kind != "llm_call":
                continue
            calls += 1
            usage = event.payload.get("usage") or {}
            prompt_tokens += int(usage.get("prompt_tokens") or 0)
            completion_tokens += int(usage.get("completion_tokens") or 0)
            model = str(event.payload.get("model") or "unknown")
            models[model] = models.get(model, 0) + 1
        return {
            "llm_calls": calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "models": models,
        }

    def persist(self) -> str | None:
        """把完整轨迹写为 JSONL。失败不影响主流程。"""
        try:
            settings.trace_path.mkdir(parents=True, exist_ok=True)
            target = (settings.trace_path / f"{self.task_id}.jsonl").resolve()
            # 纵深防御：即便 schema 层已限字符集，落盘前仍断言解析后的路径
            # 没有逃出 var/traces —— 否则 task_id 含 ../ 会写到目录之外。
            if not target.is_relative_to(settings.trace_path.resolve()):
                logger.warning("trace persist rejected (path escape): %r", self.task_id)
                return None
            with target.open("w", encoding="utf-8") as handle:
                for event in self._events:
                    handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            return str(target)
        except Exception as exc:  # noqa: BLE001 - 观测失败不能拖垮业务
            logger.warning("trace persist failed: %s", exc)
            return None


def _sanitize(value: Any, depth: int = 0) -> Any:
    """截断超长字段，避免 SSE 帧过大 / trace 文件爆炸。"""
    if depth > 4:
        return "<max-depth>"
    if isinstance(value, str):
        return value if len(value) <= 2000 else value[:2000] + "…<truncated>"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth + 1) for item in list(value)[:40]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


class SseSink:
    """把 TraceEvent 塞进 asyncio.Queue，供 SSE 端点消费。

    使用同步 put_nowait：Agent 节点无论跑在事件循环线程还是 worker 线程都能安全写入。
    """

    _SENTINEL = object()

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None, maxsize: int = 2048) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._loop = loop

    def __call__(self, event: TraceEvent) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("sse queue full, dropping event %s", event.kind)

    def close(self) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(self._SENTINEL)

    async def stream(self):
        while True:
            item = await self.queue.get()
            if item is self._SENTINEL:
                return
            yield item
