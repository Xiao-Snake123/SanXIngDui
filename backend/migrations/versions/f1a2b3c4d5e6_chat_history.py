"""chat history（会话记录）

新增两张表，让对话**有记录**：

    chat_sessions  一行一个会话（元信息 + 列表排序键）
    chat_messages  一条消息一行（回看详情 / 回温重建会话）

为什么要这两张表
----------------
此前会话只存在 Redis（TTL 1800s、上限 200、超出淘汰最久未活动的），
它是**活跃缓存**而不是记录：30 分钟不聊就没了，也查不出「某个用户有哪些会话」，
前端刷新页面更是直接失联（session_id 只活在 React state 里）。

这两张表负责：
- 持久化：刷新、隔天回来都能找回会话；
- 归属：按 user_id 索引，列出「我的历史会话」；
- 回温：Redis 过期后仍能从 chat_messages 重建会话继续聊。

Redis 不删，继续作为活跃会话缓存；两者靠 session_id 关联。

Revision ID: f1a2b3c4d5e6
Revises: d7e1a2b3c4d5
Create Date: 2026-09-23 00:00:00.000000+08:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f1a2b3c4d5e6"
down_revision = "d7e1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index(
        "ix_chat_sessions_user_last", "chat_sessions", ["user_id", "last_message_at"]
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "proposals",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "intent",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evidence_titles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_messages_session_created", "chat_messages", ["session_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_session_created", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_user_last", table_name="chat_sessions")
    op.drop_table("chat_sessions")
