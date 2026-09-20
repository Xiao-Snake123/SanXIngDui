"""领域异常。

统一异常层次让上层可以精确决定「降级 / 重试 / 上抛」：
- ProviderUnavailable：外部能力不可用（无 Key、服务不可达）→ 应降级
- ProviderError：外部返回了错误（4xx/5xx）→ 可重试或降级
- QualityGateFailure：质检未通过 → 触发 Self-Correction 回炉
- BudgetExceeded：超出步数/重试预算 → 强制收敛，返回当前最优结果
"""

from __future__ import annotations

from typing import Any


class SanxingduiError(Exception):
    """本项目所有异常的基类，携带结构化上下文便于写入 trace。"""

    code = "internal_error"
    http_status = 500

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "context": self.context}


class ConfigError(SanxingduiError):
    code = "config_error"


class ProviderUnavailable(SanxingduiError):
    """外部能力不可用，调用方应走降级分支而不是报错给用户。"""

    code = "provider_unavailable"
    http_status = 503


class ProviderError(SanxingduiError):
    code = "provider_error"
    http_status = 502

    def __init__(self, message: str, *, status: int | None = None, **context: Any) -> None:
        super().__init__(message, status=status, **context)
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status in {408, 409, 425, 429} or self.status >= 500


class StructuredOutputError(SanxingduiError):
    """模型没有按约定输出 JSON / schema 不匹配。"""

    code = "structured_output_error"


class QualityGateFailure(SanxingduiError):
    code = "quality_gate_failure"


class BudgetExceeded(SanxingduiError):
    code = "budget_exceeded"


class TaskNotFound(SanxingduiError):
    code = "task_not_found"
    http_status = 404
