"""对话会话的**领域模型**（不含存储实现）。

这里只放两样东西：`ChatSession` / `ChatTurn` 两个数据类，以及它们的容量与生命周期常量。
真正的存储在 `app/storage/sessions.py`（Redis 实现 + 进程内实现）。

拆开的理由：模型是业务语义（「一个会话里保留几轮」「长会话怎么裁剪」），
存储是基础设施（「键怎么命名」「TTL 挂在哪」「用什么序列化」）。
原来的版本把两者揉在一个类里，结果是「想换 Redis 就得连裁剪规则一起改」。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

MAX_SESSIONS = 200
SESSION_TTL_SECONDS = 1800.0     # 30 分钟无活动即回收
MAX_TURNS_PER_SESSION = 40
HISTORY_WINDOW = 8               # 送入模型的历史轮数


@dataclass(slots=True)
class ChatTurn:
    role: str                    # user | assistant
    content: str
    ts: float = field(default_factory=time.time)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    intent: dict[str, Any] = field(default_factory=dict)
    evidence_titles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "ts": self.ts,
            "proposals": self.proposals,
            "intent": self.intent,
            "evidence_titles": self.evidence_titles,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChatTurn":
        return cls(
            role=str(payload.get("role") or "user"),
            content=str(payload.get("content") or ""),
            ts=float(payload.get("ts") or time.time()),
            proposals=list(payload.get("proposals") or []),
            intent=dict(payload.get("intent") or {}),
            evidence_titles=[str(item) for item in (payload.get("evidence_titles") or [])],
        )


@dataclass
class ChatSession:
    session_id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    turns: list[ChatTurn] = field(default_factory=list)
    last_intent: dict[str, Any] = field(default_factory=dict)
    selected_proposal: dict[str, Any] | None = None

    def append(self, turn: ChatTurn) -> None:
        self.turns.append(turn)
        if len(self.turns) > MAX_TURNS_PER_SESSION:
            # 保留最早的一条用户诉求 + 最近若干轮，避免长会话把上下文冲掉
            head = self.turns[:1]
            tail = self.turns[-(MAX_TURNS_PER_SESSION - 1) :]
            self.turns = head + tail
        self.updated_at = turn.ts

    def history(self, limit: int = HISTORY_WINDOW) -> list[dict[str, str]]:
        """转成 LLM 消息格式，只保留 role/content。"""
        recent = [turn for turn in self.turns if turn.role in {"user", "assistant"}][-limit:]
        return [{"role": turn.role, "content": turn.content} for turn in recent]

    def transcript(self, limit: int = 4) -> str:
        recent = self.turns[-limit:]
        return "\n".join(
            f"{'用户' if turn.role == 'user' else '助手'}：{turn.content[:200]}" for turn in recent
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turns": [turn.to_dict() for turn in self.turns],
            "last_intent": self.last_intent,
            "selected_proposal": self.selected_proposal,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChatSession":
        """从持久化形态还原。

        `slots=True` 的 ChatTurn 不能直接反序列化，必须显式重建 ——
        这也是为什么存储层统一走 `to_dict` / `from_dict` 这一对，
        而不是直接 pickle 对象：结构改了要显式处理，不会静默读出一堆旧字段。
        """
        turns = [ChatTurn.from_dict(item) for item in (payload.get("turns") or [])]
        session = cls(
            session_id=str(payload.get("session_id") or ""),
            created_at=float(payload.get("created_at") or time.time()),
            updated_at=float(payload.get("updated_at") or time.time()),
            turns=turns,
            last_intent=dict(payload.get("last_intent") or {}),
            selected_proposal=payload.get("selected_proposal"),
        )
        return session
