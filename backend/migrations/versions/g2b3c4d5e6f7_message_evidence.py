"""chat message evidence（回看时的完整引用）

chat_messages 增加 evidence 列（JSONB），保存**完整引用块**而不只是标题。

为什么：回看历史回答时，正文里带着 [1] 这类引用标记。
此前只存了 evidence_titles（标题列表），回看时渲染不出引用的原文、出处链接与可信度——
于是「每条事实都能溯源」在**回看场景**下失效：能看到结论，却点不开出处。

存量行统一填 []（这些消息本来也没有完整引用块可还原），不影响新写入的消息。

Revision ID: g2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-23 00:00:00.000000+08:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "g2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "evidence")
