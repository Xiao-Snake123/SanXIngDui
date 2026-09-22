"""corpus chunk tag

为 corpus_chunks 增加 corpus_tag 列，使向量写/读/清理都按语料身份作用域隔离。

背景：原先向量表在「切换语料」或「多实例共享同一库」时会被污染——检索会返回
不属于当前语料的旧向量，而清理又是全局 NOT IN(doc_id) 删除，会静默清空另一份
语料的全部向量（AUDIT M13）。加标签后，PgVectorIndex 只在当前 corpus_tag 范围内
工作（见 app/storage/vectors.py）。

存量行（迁移前写入的）corpus_tag 为 NULL，会被新代码的清理逻辑认作「不属于当前
语料」而删除，并在本轮重新向量化当前语料，因此首次启动会多花一次 embedding 的钱。

Revision ID: b1f2c3d4e5f6
Revises: a64944e4659d
Create Date: 2026-09-22 00:00:00.000000+08:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1f2c3d4e5f6"
down_revision = "a64944e4659d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "corpus_chunks",
        sa.Column("corpus_tag", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_corpus_tag", "corpus_chunks", ["corpus_tag"])


def downgrade() -> None:
    op.drop_index("ix_corpus_tag", table_name="corpus_chunks")
    op.drop_column("corpus_chunks", "corpus_tag")
