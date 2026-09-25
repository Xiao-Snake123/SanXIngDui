"""user facts（长期用户画像）

新增 user_facts 表：按 user_id 存放跨会话长期有效的用户事实（名字、稳定偏好等）。

与 Redis 里的会话（短期记忆，TTL 1800s）**互补而不是替代**：
- 会话管「这一轮对话说了什么」——每轮都写、会过期、换 session_id 即丢；
- 画像管「这个人是谁」——只在用户陈述新事实时 upsert、永久保留、跨会话可读。

答话前把画像注入提示词（见 app/conversation/profile.py），
让助手在**新会话里也认识用户**，而不只是同一段对话里记得住。

Revision ID: d7e1a2b3c4d5
Revises: b1f2c3d4e5f6
Create Date: 2026-09-23 00:00:00.000000+08:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d7e1a2b3c4d5"
down_revision = "b1f2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_facts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source_session_id", sa.String(length=64), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key", name="uq_user_fact"),
    )
    op.create_index("ix_user_facts_user_id", "user_facts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_facts_user_id", table_name="user_facts")
    op.drop_table("user_facts")
