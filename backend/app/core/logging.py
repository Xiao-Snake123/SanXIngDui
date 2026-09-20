"""结构化日志。

生产环境按 JSON 行输出（便于 Loki / CloudWatch 采集），
本地开发按人类可读格式输出。零外部依赖。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from app.core.config import settings

_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class HumanFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[41m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        reset = "\033[0m" if color else ""
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED and not key.startswith("_")
        }
        suffix = ""
        if extras:
            suffix = "  " + " ".join(f"{k}={v}" for k, v in extras.items())
        base = (
            f"{color}{record.levelname:<8}{reset} "
            f"{time.strftime('%H:%M:%S', time.localtime(record.created))} "
            f"{record.name:<28} {record.getMessage()}{suffix}"
        )
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    use_json = settings.log_level.upper() in {"INFO", "WARNING"} and not sys.stdout.isatty()
    handler.setFormatter(JsonFormatter() if use_json else HumanFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    # 第三方库降噪
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "alembic"):
        # alembic 每次构造 ScriptDirectory（包括「已是最新、不需要迁移」的路径）
        # 都会刷 7 行「setup plugin alembic.autogenerate.*」的 INFO，
        # 把启动日志里真正重要的信息（存储档位、出图通道）挤下去。
        # 真正跑迁移时由 app.storage.db 自己打一行结果，不依赖它的日志。
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
