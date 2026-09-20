"""数据库迁移与向量表维护。

用法：
    python scripts/migrate.py                    # 升级到最新（等价 alembic upgrade head）
    python scripts/migrate.py --current          # 只打印当前版本与最新版本，不改动
    python scripts/migrate.py --recreate-vectors # 按 VECTOR_DIM 重建向量表并重算向量

为什么要有 `--recreate-vectors`
------------------------------
向量列的维度是**建表时固定**的（`vector(1024)`）。换 embedding 模型 = 换维度，
此时表结构必须跟着换。而「改维度」这件事不可能靠一条 ALTER 完成：
1024 维的已有数据既不能转成 512 维，也没有保留价值（它是语料派生的缓存）。
所以这里做的是「删表 → 按新维度重建 → 重算」——
**向量是可重建的派生数据**，这个性质允许我们这么粗暴地处理它，
但它只对派生数据成立，对任务/质检记录绝对不成立。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402
from app.storage import database  # noqa: E402


async def _current() -> int:
    from app.storage.db import head_revision

    if not await database.connect():
        print(f"FAIL: PostgreSQL 不可用 -- {database.reason}")
        return 1

    head = head_revision()
    current = database.schema_revision
    print(f"当前版本: {current or '(未初始化)'}")
    print(f"最新版本: {head or '(无迁移脚本)'}")
    if current == head:
        print("已是最新，无需迁移")
        return 0
    print("存在待应用的迁移，执行: python scripts/migrate.py")
    return 0


async def _upgrade() -> int:
    if not await database.connect():
        print(f"FAIL: PostgreSQL 不可用 -- {database.reason}")
        print("  本机初始化: python scripts/setup_postgres.py")
        return 1

    original = settings.db_auto_migrate
    settings.db_auto_migrate = True
    try:
        ok = await database.upgrade()
    finally:
        settings.db_auto_migrate = original

    if not ok:
        print("FAIL: 迁移未完成")
        return 1
    print(f"OK: schema = {database.schema_revision}")
    return 0


async def _recreate_vectors() -> int:
    if not await database.connect():
        print(f"FAIL: PostgreSQL 不可用 -- {database.reason}")
        return 1

    from sqlalchemy import text

    from app.storage.models import CorpusChunk

    print(f"按 VECTOR_DIM={settings.vector_dim} 重建 corpus_chunks ...")
    await database.execute(text("DROP TABLE IF EXISTS corpus_chunks CASCADE"))

    if database.engine is None:
        print("FAIL: 数据库引擎不可用")
        return 1
    async with database.engine.begin() as conn:
        await conn.run_sync(CorpusChunk.__table__.create)
    print("表已重建（含 HNSW 余弦索引）")

    # 立刻把向量算回来：留一张空表比报错更危险 ——
    # 检索会静默退化成纯 BM25，而没人知道向量通道已经空了。
    from app.rag.store import HybridRetriever, reset_retriever

    await reset_retriever()
    retriever = await HybridRetriever.build()
    stats = retriever.stats()
    print(
        f"向量已重算: {stats['vector_count']} 条（{stats['vector_backend']}，"
        f"维度 {stats['vector_dimension']}，新算 {stats['vector_embedded']}）"
    )
    ok = stats["vector_count"] > 0 and stats["vector_backend"] == "pgvector"
    await retriever.aclose()
    await reset_retriever()
    print("OK: 向量表已就绪" if ok else "FAIL: 向量未能写入 pgvector，请检查 VECTOR_DIM 与扩展")
    return 0 if ok else 1


async def main() -> int:
    parser = argparse.ArgumentParser(description="数据库迁移")
    parser.add_argument("--current", action="store_true", help="只显示版本，不做改动")
    parser.add_argument("--recreate-vectors", action="store_true", help="按 VECTOR_DIM 重建向量表")
    args = parser.parse_args()

    try:
        if args.current:
            return await _current()
        if args.recreate_vectors:
            return await _recreate_vectors()
        return await _upgrade()
    finally:
        await database.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
