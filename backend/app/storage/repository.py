"""任务 / 产物 / 质检记录的读写。

失败隔离
--------
所有写操作都是**尽力而为**：数据库抖一下不应该让一次已经跑完（并且已经花了出图钱）
的复原任务失败。所以每个写方法自己捕获异常、记指标、返回 False，
调用方不需要（也不应该）用 try 包起来。

幂等
----
`task_images` / `qa_verdicts` 都以 `(task_id, round_index)` 为唯一键，写用 upsert。
回炉轮次天然幂等，这带来两个好处：
- 任务重试、补写、重复触发都不会产生重复行；
- 可以「先写每轮，最后再补总表」，不怕中途崩溃后重跑。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Integer, and_, cast, desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import get_logger
from app.core.metrics import metrics
from app.storage.db import Database
from app.storage.models import CorpusChunk, QaVerdict, RestorationTask, TaskImage

logger = get_logger("app.storage.repository")

TASK_CORE_FIELDS = (
    "kind",
    "item",
    "identity",
    "scene",
    "style",
    "goal",
    "engine",
    "profile_key",
    "material_key",
    "status",
    "score",
    "objective_score",
    "judge_score",
    "passed",
    "decision",
    "revisions",
    "provider",
    "image_degraded",
    "qa_skipped",
    "evidence_count",
    "degraded_components",
    "duration_ms",
    "request",
    "error",
)


# 列宽在这里集中兜底。
# 为什么不信任调用方：PostgreSQL 对 varchar 溢出是**直接报错**（不是截断），
# 一个 130 字的 `item` 会让整条任务记录写不进去 —— 而这种失败只在
# 「有人输入了长名字」时才出现，最容易漏测。
_COLUMN_LIMITS = {
    "kind": 32,
    "item": 120,
    "identity": 120,
    "scene": 120,
    "style": 120,
    "engine": 32,
    "profile_key": 64,
    "material_key": 64,
    "status": 16,
    "decision": 16,
    "provider": 48,
}


def _clip(value: Any, limit: int | None) -> Any:
    if limit is None or not isinstance(value, str):
        return value
    return value[:limit]


def _pick(payload: dict[str, Any]) -> dict[str, Any]:
    """只挑出表里真实存在的列，避免上游多塞一个键就整条写失败。"""
    return {
        key: _clip(payload[key], _COLUMN_LIMITS.get(key))
        for key in TASK_CORE_FIELDS
        if key in payload
    }


class TaskRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ── 写 ──────────────────────────────────────────────────────────────────
    async def ensure_task(self, task_id: str, payload: dict[str, Any]) -> bool:
        """先落一条骨架行。

        必须早于 images / verdicts 写入：这两张表有外键指向 restoration_tasks，
        而每轮产物是在任务跑到一半时就要落库的（不能等任务结束）。
        """
        if not await self.db.ensure_connected():
            return False
        values = {"task_id": task_id, "status": "running", **_pick(payload)}
        values.setdefault("kind", "unknown")
        try:
            async with self.db.session() as session:
                statement = pg_insert(RestorationTask).values(**values)
                # 冲突时只补空缺字段，不覆盖已经写好的终态（重跑同一个 task_id 时）
                await session.execute(
                    statement.on_conflict_do_nothing(index_elements=["task_id"])
                )
                await session.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            self._fail("ensure_task", exc)
            return False

    async def upsert_task(self, task_id: str, payload: dict[str, Any]) -> bool:
        """写入/更新任务终态。"""
        if not await self.db.ensure_connected():
            return False
        values = {"task_id": task_id, **_pick(payload)}
        values.setdefault("kind", "unknown")
        try:
            async with self.db.session() as session:
                statement = pg_insert(RestorationTask).values(**values)
                update_columns = {
                    key: getattr(statement.excluded, key)
                    for key in values
                    if key != "task_id"
                }
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["task_id"], set_=update_columns
                    )
                )
                await session.commit()
            metrics.inc("sxd_db_task_written_total")
            return True
        except Exception as exc:  # noqa: BLE001
            self._fail("upsert_task", exc)
            return False

    async def upsert_image(self, task_id: str, payload: dict[str, Any]) -> bool:
        if not await self.db.ensure_connected():
            return False
        values = {"task_id": task_id, **_image_fields(payload)}
        try:
            async with self.db.session() as session:
                statement = pg_insert(TaskImage).values(**values)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["task_id", "round_index"],
                        set_={
                            key: getattr(statement.excluded, key)
                            for key in values
                            if key not in {"task_id", "round_index"}
                        },
                    )
                )
                await session.commit()
            metrics.inc("sxd_db_image_written_total")
            return True
        except Exception as exc:  # noqa: BLE001
            self._fail("upsert_image", exc)
            return False

    async def upsert_verdict(self, task_id: str, payload: dict[str, Any]) -> bool:
        if not await self.db.ensure_connected():
            return False
        values = {"task_id": task_id, **_verdict_fields(payload)}
        try:
            async with self.db.session() as session:
                statement = pg_insert(QaVerdict).values(**values)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["task_id", "round_index"],
                        set_={
                            key: getattr(statement.excluded, key)
                            for key in values
                            if key not in {"task_id", "round_index"}
                        },
                    )
                )
                await session.commit()
            metrics.inc("sxd_db_verdict_written_total")
            return True
        except Exception as exc:  # noqa: BLE001
            self._fail("upsert_verdict", exc)
            return False

    async def upsert_corpus_chunks(self, rows: list[dict[str, Any]]) -> int:
        """批量写入史料向量（幂等 upsert）。

        向量以**字符串** + 显式 CAST 传入：这样不依赖 asyncpg 的 vector codec 注册，
        换驱动（psycopg / asyncpg）都不会因为「codec 没注册」而静默插入 NULL。
        """
        if not await self.db.ensure_connected() or not rows:
            return 0
        from sqlalchemy import text

        statement = text(
            """
            INSERT INTO corpus_chunks
                (doc_id, title, "object", era, category, source, authority, tags,
                 embedder, content_hash, corpus_tag, embedding, updated_at)
            VALUES
                (                :doc_id, :title, :object, :era, :category, :source, :authority,
                 CAST(:tags AS jsonb), :embedder, :content_hash, :corpus_tag,
                 CAST(:embedding AS vector), now())
            ON CONFLICT (doc_id) DO UPDATE SET
                title = EXCLUDED.title,
                "object" = EXCLUDED."object",
                era = EXCLUDED.era,
                category = EXCLUDED.category,
                source = EXCLUDED.source,
                authority = EXCLUDED.authority,
                tags = EXCLUDED.tags,
                embedder = EXCLUDED.embedder,
                content_hash = EXCLUDED.content_hash,
                corpus_tag = EXCLUDED.corpus_tag,
                embedding = EXCLUDED.embedding,
                updated_at = now()
            """
        )
        payload = [
            {
                **row,
                "tags": _json(row.get("tags") or []),
                "embedding": _vector_literal(row.get("embedding")),
                "content_hash": row.get("content_hash"),
                "corpus_tag": row.get("corpus_tag"),
            }
            for row in rows
        ]
        try:
            async with self.db.session() as session:
                await session.execute(statement, payload)
                await session.commit()
            metrics.inc("sxd_db_vector_written_total", float(len(payload)))
            return len(payload)
        except Exception as exc:  # noqa: BLE001
            self._fail("upsert_corpus_chunks", exc)
            return 0

    # ── 读 ──────────────────────────────────────────────────────────────────
    async def list_tasks(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        kind: str | None = None,
        decision: str | None = None,
        passed: bool | None = None,
        item: str | None = None,
    ) -> dict[str, Any]:
        if not await self.db.ensure_connected():
            return {"items": [], "total": 0, "available": False}
        conditions = []
        if kind:
            conditions.append(RestorationTask.kind == kind)
        if decision:
            conditions.append(RestorationTask.decision == decision)
        if passed is not None:
            conditions.append(RestorationTask.passed.is_(passed))
        if item:
            conditions.append(RestorationTask.item.ilike(f"%{item}%"))

        async with self.db.session() as session:
            total = await session.scalar(
                select(func.count()).select_from(RestorationTask).where(*conditions)
            )
            rows = (
                await session.scalars(
                    select(RestorationTask)
                    .where(*conditions)
                    .order_by(desc(RestorationTask.created_at))
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        return {
            "items": [_task_view(row) for row in rows],
            "total": int(total or 0),
            "limit": limit,
            "offset": offset,
            "available": True,
        }

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        if not await self.db.ensure_connected():
            return None
        async with self.db.session() as session:
            task = await session.get(RestorationTask, task_id)
            if task is None:
                return None
            images = (
                await session.scalars(
                    select(TaskImage)
                    .where(TaskImage.task_id == task_id)
                    .order_by(TaskImage.round_index)
                )
            ).all()
            verdicts = (
                await session.scalars(
                    select(QaVerdict)
                    .where(QaVerdict.task_id == task_id)
                    .order_by(QaVerdict.round_index)
                )
            ).all()
        return {
            **_task_view(task),
            "request": task.request or {},
            "error": task.error,
            "images": [_image_view(row) for row in images],
            "verdicts": [_verdict_view(row) for row in verdicts],
        }

    async def quality_stats(self, *, days: int = 30) -> dict[str, Any]:
        """按 SQL 统计质量指标 —— 这是「落库」相对「进程内计数器」的核心收益。

        时间窗在 Python 侧算好再传：不用 `now() - interval '30 days'` 这类
        PostgreSQL 专有写法，一是便于单测注入固定时间，二是避免时区语义
        （timestamptz 与 now() 在会话时区不同的时候会差出一整天）。
        """
        if not await self.db.ensure_connected():
            return {"available": False, "reason": self.db.reason or "数据库不可用"}
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        passed_expr = func.sum(cast(RestorationTask.passed.is_(True), Integer))
        skipped_expr = func.sum(cast(RestorationTask.qa_skipped, Integer))
        # 「真的判过」= 有质检结论、且不是因占位图跳过的那批。
        #
        # 不能再用 `tasks - skipped` 当分母：被用户**中止**的、以及中途**失败**的
        # 任务，既没有质检结论、也不在 `qa_skipped` 里 —— 它们会被算进分母，
        # 让通过率凭空变差。中止现在是用户随手可做的事，这个口径错误会立刻显形。
        judged_expr = func.sum(
            cast(
                and_(
                    RestorationTask.decision.is_not(None),
                    RestorationTask.qa_skipped.is_not(True),
                ),
                Integer,
            )
        )
        try:
            async with self.db.session() as session:
                overall = (
                    await session.execute(
                        select(
                            func.count().label("tasks"),
                            func.avg(RestorationTask.score).label("avg_score"),
                            func.avg(RestorationTask.revisions).label("avg_revisions"),
                            passed_expr.label("passed_count"),
                            skipped_expr.label("skipped_count"),
                            judged_expr.label("judged_count"),
                            func.avg(RestorationTask.duration_ms).label("avg_duration_ms"),
                        )
                        .select_from(RestorationTask)
                        .where(RestorationTask.created_at >= cutoff)
                    )
                ).mappings().one()
                by_kind = (
                    await session.execute(
                        select(
                            RestorationTask.kind.label("kind"),
                            func.count().label("tasks"),
                            func.avg(RestorationTask.score).label("avg_score"),
                            func.avg(RestorationTask.revisions).label("avg_revisions"),
                            passed_expr.label("passed_count"),
                            skipped_expr.label("skipped_count"),
                            judged_expr.label("judged_count"),
                        )
                        .select_from(RestorationTask)
                        .where(RestorationTask.created_at >= cutoff)
                        .group_by(RestorationTask.kind)
                        .order_by(desc("tasks"))
                    )
                ).mappings().all()
        except Exception as exc:  # noqa: BLE001
            self._fail("quality_stats", exc)
            return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

        return {
            "available": True,
            "window_days": days,
            "overall": _ratio_view(dict(overall)),
            "by_kind": [_ratio_view(dict(row)) for row in by_kind],
        }

    async def vector_rows(self, *, embedder: str | None = None) -> list[tuple[str, str]]:
        """返回 [(doc_id, embedder)]，用于判断库里已有的向量是否还能复用。"""
        if not await self.db.ensure_connected():
            return []
        async with self.db.session() as session:
            statement = select(CorpusChunk.doc_id, CorpusChunk.embedder).where(
                CorpusChunk.embedding.is_not(None)
            )
            if embedder:
                statement = statement.where(CorpusChunk.embedder == embedder)
            return [tuple(row) for row in (await session.execute(statement)).all()]  # type: ignore[misc]

    async def count_vectors(self) -> int:
        if not await self.db.ensure_connected():
            return 0
        async with self.db.session() as session:
            return int(
                await session.scalar(
                    select(func.count()).select_from(CorpusChunk).where(
                        CorpusChunk.embedding.is_not(None)
                    )
                )
                or 0
            )

    # ── 内部 ────────────────────────────────────────────────────────────────
    @staticmethod
    def _fail(operation: str, exc: Exception) -> None:
        logger.warning("数据库写入失败 [%s]，本次仅记指标不影响主流程: %s", operation, exc)
        metrics.inc("sxd_db_error_total", operation=operation)
        metrics.record_degradation(
            component="database", reason=f"{operation}: {exc}", fallback="skip_write"
        )


def _image_fields(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "round_index",
        "image_url",
        "provider",
        "seed",
        "width",
        "height",
        "prompt",
        "negative_prompt",
        "degraded",
        "bytes_available",
    )
    values = {key: payload.get(key) for key in allowed if key in payload}
    values.setdefault("round_index", 0)
    values.setdefault("degraded", False)
    values.setdefault("bytes_available", False)
    values["provider"] = _clip(values.get("provider"), 48)
    return values


def _verdict_fields(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "round_index",
        "score",
        "objective_score",
        "judge_score",
        "threshold",
        "passed",
        "decision",
        "reason",
        "feedback",
        "anachronisms",
        "dimensions",
        "violated_rules",
        "material_key",
        "profile_key",
    )
    values = {key: payload.get(key) for key in allowed if key in payload}
    values.setdefault("round_index", 0)
    for key in ("feedback", "anachronisms", "violated_rules"):
        values[key] = list(values.get(key) or [])
    values["dimensions"] = dict(values.get("dimensions") or {})
    values["decision"] = _clip(values.get("decision"), 16)
    values["material_key"] = _clip(values.get("material_key"), 64)
    values["profile_key"] = _clip(values.get("profile_key"), 64)
    return values


def _task_view(row: RestorationTask) -> dict[str, Any]:
    return {
        "task_id": row.task_id,
        "kind": row.kind,
        "item": row.item,
        "identity": row.identity,
        "scene": row.scene,
        "style": row.style,
        "goal": row.goal,
        "engine": row.engine,
        "profile_key": row.profile_key,
        "material_key": row.material_key,
        "status": row.status,
        "score": row.score,
        "objective_score": row.objective_score,
        "judge_score": row.judge_score,
        "passed": row.passed,
        "decision": row.decision,
        "revisions": row.revisions,
        "provider": row.provider,
        "image_degraded": row.image_degraded,
        "qa_skipped": row.qa_skipped,
        "evidence_count": row.evidence_count,
        "degraded_components": row.degraded_components or [],
        "duration_ms": row.duration_ms,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _image_view(row: TaskImage) -> dict[str, Any]:
    return {
        "round_index": row.round_index,
        "image_url": row.image_url,
        "provider": row.provider,
        "seed": row.seed,
        "width": row.width,
        "height": row.height,
        "prompt": row.prompt,
        "negative_prompt": row.negative_prompt,
        "degraded": row.degraded,
        "bytes_available": row.bytes_available,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _verdict_view(row: QaVerdict) -> dict[str, Any]:
    return {
        "round_index": row.round_index,
        "score": row.score,
        "objective_score": row.objective_score,
        "judge_score": row.judge_score,
        "threshold": row.threshold,
        "passed": row.passed,
        "decision": row.decision,
        "reason": row.reason,
        "feedback": row.feedback or [],
        "anachronisms": row.anachronisms or [],
        "dimensions": row.dimensions or {},
        "violated_rules": row.violated_rules or [],
        "material_key": row.material_key,
        "profile_key": row.profile_key,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _ratio_view(row: dict[str, Any]) -> dict[str, Any]:
    tasks = int(row.get("tasks") or 0)
    passed = int(row.get("passed_count") or 0)
    skipped = int(row.get("skipped_count") or 0)
    # 分母 = **真的产出了质检结论**的任务数，由 SQL 侧 `judged_count` 给出。
    #
    # 原先是 `tasks - skipped`，它同时错在两处：
    #   1. 落占位图时质检是 skipped，不该进分母（原注释已经指出）；
    #   2. 被用户**中止**与中途**失败**的任务不在 skipped 里，却会被算进分母。
    # 第 2 点在中止功能上线后会立刻显形 —— 越爱中止，通过率越差，
    # 而指标本身并没有变坏。回退到 `tasks - skipped` 只在拿不到该字段时发生
    # （例如调用方是旧签名的桩），保留是为了不把测试里的桩写死。
    judged = (
        int(row["judged_count"])
        if row.get("judged_count") is not None
        else max(0, tasks - skipped)
    )
    return {
        "kind": row.get("kind"),
        "tasks": tasks,
        "judged": judged,
        "passed": passed,
        "pass_rate": round(passed / judged, 4) if judged else None,
        "avg_score": round(float(row["avg_score"]), 4) if row.get("avg_score") is not None else None,
        "avg_revisions": (
            round(float(row["avg_revisions"]), 3) if row.get("avg_revisions") is not None else None
        ),
        "avg_duration_ms": (
            round(float(row["avg_duration_ms"]), 1) if row.get("avg_duration_ms") is not None else None
        ),
        "qa_skipped": skipped,
    }


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _vector_literal(vector: Any) -> str | None:
    """把 list[float] 转成 pgvector 的字面量 `[0.1,0.2,...]`。"""
    if vector is None:
        return None
    if isinstance(vector, str):
        return vector
    return "[" + ",".join(f"{float(value):.8g}" for value in vector) + "]"
