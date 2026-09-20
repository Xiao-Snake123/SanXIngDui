"""运行期上下文（ContextVar）。

Agent 节点需要访问 TraceRecorder、请求对象、以及运行时开关，但**不应**把它们塞进
图状态里 —— 状态是要被快照/重放的（未来接 checkpointer 后是持久化契约），
把 recorder 这类活的运行时对象放进去会让状态无法序列化。

因此用 ContextVar 承载「本次运行」的运行时依赖：
- 只读访问，节点拿不到就不写；
- 与 LangGraph 的调用方式解耦（无论是 astream 还是内置调度器都能用）；
- 天然支持多请求并发（每个请求一个 context 副本）。
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from app.core.tracing import TraceRecorder


@dataclass(slots=True)
class RunContext:
    task_id: str
    tracer: TraceRecorder
    request: dict[str, Any]
    options: dict[str, Any] = field(default_factory=dict)

    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)


_current: ContextVar[RunContext | None] = ContextVar("sxd_run_context", default=None)


def set_run_context(context: RunContext):
    return _current.set(context)


def reset_run_context(token) -> None:
    _current.reset(token)


def get_run_context() -> RunContext | None:
    return _current.get()


def require_run_context() -> RunContext:
    context = _current.get()
    if context is None:
        raise RuntimeError("当前不在 Agent 运行上下文内（缺少 RunContext）")
    return context
