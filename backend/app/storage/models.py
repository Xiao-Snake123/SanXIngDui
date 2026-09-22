"""关系模型：任务 / 生成图产物 / 质检判定 / 语料向量。

为什么是这四张表
----------------
在看这个项目之前，一次复原任务跑完之后，**唯一留下的痕迹**是：
- `var/traces/*.jsonl`（给人看的轨迹）
- `var/metrics/metrics_snapshot.json`（进程内计数器的快照）
- `var/outputs/*.png`（图片文件）

于是「上周那批青铜面具的复原，平均回炉几次、通过率多少」这类问题**没法用 SQL 回答**，
只能写脚本扒 JSONL；而这些问题的答案恰恰是评估一个多智能体系统的核心。
这四张表就是把它变成可查询的事实：

    restoration_tasks  一次复原任务一行（结果摘要 + 降级情况）
    task_images        每一轮出图一行（含提示词原文，可复现）
    qa_verdicts        每一轮质检一行（分数、判定、修改指令）
    corpus_chunks      史料分块 + 向量（pgvector 列 + HNSW 索引）

设计取舍
--------
- **时间戳一律 `timestamptz`**：会话跨时区、跨机器对比时不会出现「谁比谁早 8 小时」。
- **可变结构一律 JSONB**：`feedback` / `dimensions` / `degraded_components` 的字段
  会随质检算法演化，建成列就得每次加迁移；但它们又确实需要被 SQL 查询，
  所以用 JSONB 而不是 TEXT（JSONB 可建 GIN 索引、可按路径过滤）。
- **`(task_id, round_index)` 唯一**：回炉轮次是天然幂等键，重复写用 upsert，
  这样任务重试或补写不会产生重复行。
- **图片二进制不进库**：这里只存元信息（URL / 提示词 / 尺寸 / provider），
  像素归档仍在 `var/outputs/`。二进制塞进 PG 会拖慢备份与查询，
  真要统一管理应该上对象存储，那是另一件事。
"""

from __future__ import annotations

import datetime as dt

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings


class Base(DeclarativeBase):
    pass


class RestorationTask(Base):
    __tablename__ = "restoration_tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 任务输入摘要（用户说了什么）。存下来才能回答「哪类器物的复原最难」
    item: Mapped[str | None] = mapped_column(String(120))
    identity: Mapped[str | None] = mapped_column(String(120))
    scene: Mapped[str | None] = mapped_column(String(120))
    style: Mapped[str | None] = mapped_column(String(120))
    goal: Mapped[str | None] = mapped_column(Text)

    engine: Mapped[str | None] = mapped_column(String(32))
    profile_key: Mapped[str | None] = mapped_column(String(64), index=True)
    material_key: Mapped[str | None] = mapped_column(String(64))
    # ok | failed —— 图执行本身是否跑完（与质检是否通过是两件事）
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")

    # 质检结论（最后一轮）。nullable：任务可能在质检前就失败 / 落占位图跳过质检
    score: Mapped[float | None] = mapped_column(Float)
    objective_score: Mapped[float | None] = mapped_column(Float)
    judge_score: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    decision: Mapped[str | None] = mapped_column(String(16), index=True)
    revisions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    provider: Mapped[str | None] = mapped_column(String(48))
    image_degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    qa_skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    degraded_components: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    duration_ms: Mapped[float | None] = mapped_column(Float)

    # 原始请求与错误原文：排查线上问题时最有价值的两块，不能只留摘要
    request: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        # btree 索引可以反向扫描，所以「最近任务」这类 DESC 查询不需要单独的降序索引
        Index("ix_tasks_created_at", "created_at"),
        Index("ix_tasks_kind_decision", "kind", "decision"),
    )


class TaskImage(Base):
    """一轮出图的产物记录。

    `prompt` 存的是**实际下发的那份文字**（含回炉修正指令），不是规划阶段的草稿 ——
    否则「这张图到底是用什么提示词生成的」这个问题就答不上来。
    """

    __tablename__ = "task_images"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("restoration_tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    round_index: Mapped[int] = mapped_column(Integer, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(48))
    seed: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    prompt: Mapped[str | None] = mapped_column(Text)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bytes_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("task_id", "round_index", name="uq_task_images_round"),
    )


class QaVerdict(Base):
    """一轮质检的判定记录。

    `feedback` 是可直接转成 prompt 的修改指令 —— 它既是 Self-Correction 的输入，
    也是事后复盘「质检到底在要求什么」的唯一证据。
    """

    __tablename__ = "qa_verdicts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("restoration_tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    round_index: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    objective_score: Mapped[float | None] = mapped_column(Float)
    judge_score: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    decision: Mapped[str | None] = mapped_column(String(16), index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    feedback: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    anachronisms: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    dimensions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    violated_rules: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    material_key: Mapped[str | None] = mapped_column(String(64))
    profile_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("task_id", "round_index", name="uq_qa_verdicts_round"),
        Index("ix_qa_score", "score"),
    )


class CorpusChunk(Base):
    """史料分块 + 向量。

    `embedder` 记下这条向量是哪条通道生成的：换了 embedding 模型之后，
    旧向量与新 query 不在同一语义空间，**必须能被识别出来并重建**，
    否则检索会静默变差（这是接向量库最容易埋的坑）。
    """

    __tablename__ = "corpus_chunks"

    doc_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    title: Mapped[str | None] = mapped_column(Text)
    object: Mapped[str | None] = mapped_column(String(120))
    era: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str | None] = mapped_column(Text)
    authority: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    embedder: Mapped[str] = mapped_column(String(64), nullable=False)
    # 语料正文的指纹（blake2b-128 的十六进制，32 字符）。
    #
    # 只靠 doc_id 判断「要不要重算向量」是不够的：史料 jsonl 改了正文但没改 id 时，
    # 旧向量会被继续复用，于是检索静默变差 —— 没有报错、没有告警，
    # 只是「召回的相关性好像不如以前」。加指纹之后，改过的那几条会被重新向量化。
    content_hash: Mapped[str | None] = mapped_column(String(32))
    # 语料身份标签：标识这条向量属于哪一版/哪个语料。
    #
    # 没有它，pgvector 表会在「切换语料」或「多实例共享同一库」时被污染：检索会返回
    # 不属于当前语料的旧向量（挤占 top-k、改变排序、甚至引用已删除的出处）；而清理又是
    # 「DELETE ... NOT IN(当前 doc_id)」全局删，会静默清空另一份语料的全部向量（AUDIT M13）。
    # 加标签后，写/读/清理都按标签作用域隔离：当前实例只认自己标签的向量，
    # 切换或并发都不会互相踩。取值通常就是 Corpus.fingerprint（同语料多副本天然同标签→可共享向量）。
    # 允许 NULL：未带标签的存量行 / 诊断脚本（check_storage）走旧的「全表」语义。
    corpus_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.vector_dim))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_corpus_object", "object"),
        Index("ix_corpus_embedder", "embedder"),
        Index("ix_corpus_tag", "corpus_tag"),
        # HNSW + 余弦距离：与进程内实现（归一化向量点积）语义一致。
        # m / ef_construction 走配置，因为「召回率 vs 建索引耗时」是要按语料规模调的。
        Index(
            "ix_corpus_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={
                "m": settings.vector_hnsw_m,
                "ef_construction": settings.vector_hnsw_ef_construction,
            },
        ),
    )
