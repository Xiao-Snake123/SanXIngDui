"""真实存储栈端到端自检（PostgreSQL + pgvector + Redis）。

为什么必须有这个脚本
--------------------
单测里的存储层被 `tests/conftest.py` 强制钉在进程内实现上（否则会写脏真实数据库，
而且 CI 上没有 PG/Redis 就会「只在我电脑上绿」）。
所以**真实存储链路没有任何单测覆盖** —— 如果没有这个脚本，
「接入了数据库」这句话就没有任何可验证的证据。

它验证的是几件单测根本测不出来的事：
  1. 表、向量列维度、HNSW 索引在**真实 PostgreSQL** 里确实建出来了；
  2. 任务 / 产物 / 质检记录确实落库了，且外键 CASCADE 真的生效；
  3. pgvector 的 `<=>` 距离换算与进程内余弦**数值一致**（这是最容易写错的一处）；
  4. 向量复用真的省掉了 embedding 调用；
  5. Redis 会话能跨「后端实例」读回（等价于跨进程/跨副本）。

用法：
    python scripts/check_storage.py            # 全量自检
    python scripts/check_storage.py --keep     # 保留测试数据（默认跑完清理）

    设计取舍、部署步骤与「为什么 50 条语料下不该走 HNSW 索引」见 docs/STORAGE.md。

退出码：0 = 全部通过；1 = 有失败项；2 = 未验证（存储未配置）
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.storage import database, sessions, tasks  # noqa: E402
from app.storage.sessions import RedisSessionBackend  # noqa: E402
from app.storage.vectors import MemoryVectorIndex, PgVectorIndex  # noqa: E402

# 每次运行都用不同的 id，避免互相干扰；清理时按前缀删
RUN_ID = f"storagecheck-{int(time.time())}"
PROBE_TASK = f"sxd-{RUN_ID}"


class Report:
    def __init__(self) -> None:
        self.checks: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))
        # ASCII only: Windows 控制台是 GBK，非 ASCII 符号会 UnicodeEncodeError
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

    def section(self, title: str) -> None:
        print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [item for item in self.checks if not item[1]]


async def check_infrastructure(report: Report) -> None:
    report.section("1. 基础设施")
    report.add(
        "PostgreSQL 已连接",
        database.available,
        database.server_version or database.reason,
    )
    report.add("pgvector 扩展已安装", bool(database.vector_version), database.vector_version or "-")
    report.add("Alembic schema 已就绪", bool(database.schema_revision), database.schema_revision or "-")

    rows = await database.fetch_all(
        text(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' ORDER BY table_name
            """
        )
    )
    tables = {str(row[0]) for row in rows}
    expected = {"restoration_tasks", "task_images", "qa_verdicts", "corpus_chunks"}
    report.add("四张业务表齐全", expected <= tables, ", ".join(sorted(tables)))

    dim_row = await database.fetch_all(
        text(
            """
            SELECT format_type(atttypid, atttypmod) FROM pg_attribute
            WHERE attrelid = 'corpus_chunks'::regclass AND attname = 'embedding'
            """
        )
    )
    column_type = str(dim_row[0][0]) if dim_row else "-"
    report.add(
        "向量列维度与配置一致",
        column_type == f"vector({settings.vector_dim})",
        f"列={column_type} 配置=vector({settings.vector_dim})",
    )

    index_rows = await database.fetch_all(
        text("SELECT indexdef FROM pg_indexes WHERE tablename = 'corpus_chunks'")
    )
    hnsw = [str(row[0]) for row in index_rows if "hnsw" in str(row[0]).lower()]
    report.add("HNSW 余弦索引存在", bool(hnsw), hnsw[0] if hnsw else "-")


async def check_task_persistence(report: Report) -> None:
    report.section("2. 任务 / 产物 / 质检记录落库")
    ok = await tasks.ensure_task(
        PROBE_TASK,
        {"kind": "scene", "item": "青铜大立人", "engine": "builtin", "request": {"probe": True}},
    )
    report.add("ensure_task 写入骨架行", ok)

    await tasks.upsert_image(
        PROBE_TASK,
        {
            "round_index": 0,
            "image_url": "/media/probe.png",
            "provider": "local-placeholder",
            "prompt": "probe prompt",
            "width": 768,
            "height": 768,
            "degraded": True,
        },
    )
    await tasks.upsert_verdict(
        PROBE_TASK,
        {
            "round_index": 0,
            "score": 0.5,
            "threshold": 0.72,
            "passed": False,
            "decision": "revise",
            "feedback": ["降低镜面高光"],
            "dimensions": {"color": 0.4},
            "violations": [{"rule": "oversaturated"}],
        },
    )
    await tasks.upsert_task(
        PROBE_TASK,
        {
            "kind": "scene",
            "item": "青铜大立人",
            "status": "ok",
            "score": 0.5,
            "revisions": 1,
            "qa_skipped": False,
            "provider": "local-placeholder",
        },
    )

    record = await tasks.get_task(PROBE_TASK)
    report.add("读回任务详情", record is not None)
    if record:
        report.add("产物行已写入", len(record["images"]) == 1, f"{len(record['images'])} 行")
        report.add("质检行已写入", len(record["verdicts"]) == 1, f"{len(record['verdicts'])} 行")
        verdict = record["verdicts"][0] if record["verdicts"] else {}
        report.add(
            "JSONB 字段往返无损",
            verdict.get("feedback") == ["降低镜面高光"] and verdict.get("dimensions") == {"color": 0.4},
            f"feedback={verdict.get('feedback')}",
        )
        listed = await tasks.list_tasks(kind="scene", limit=200)
        report.add(
            "任务列表可按 kind 查到",
            any(item["task_id"] == PROBE_TASK for item in listed["items"]),
        )

    # 幂等：同一个 (task_id, round_index) 再写一次不应变成两行
    await tasks.upsert_image(PROBE_TASK, {"round_index": 0, "provider": "free-third-party"})
    record = await tasks.get_task(PROBE_TASK)
    images = record["images"] if record else []
    report.add(
        "回炉轮次写入幂等",
        len(images) == 1 and images[0]["provider"] == "free-third-party",
        f"{len(images)} 行, provider={images[0]['provider'] if images else '-'}",
    )


async def check_lazy_connect(report: Report) -> None:
    """脚本路径：**不显式 connect** 也必须能落库。

    这条是对一次真实事故的回归：`smoke_test.py` 走 `runner.astream()`，
    本该落库，但脚本从不经过 FastAPI 的 lifespan、也就从没调用过 `database.connect()`，
    于是所有写入静默 no-op —— 而脚本只看图有没有出来，照样报 `SMOKE PASS`。
    实测现象就是「冒烟通过、库里 0 行」。

    现在连接的建立权在存储层自己手里（`Database.ensure_connected`），
    所以这里先显式断开，再直接写一条，验证它能自己接上。
    """
    report.section("2.5 脚本路径（不经 lifespan）的落库")
    await database.aclose()
    report.add("已断开连接（模拟脚本刚启动）", not database.available)

    probe = f"{PROBE_TASK}-lazy"
    written = await tasks.upsert_task(
        probe, {"kind": "scene", "item": "指纹探针", "status": "ok"}
    )
    record = await tasks.get_task(probe)
    report.add(
        "未显式 connect 时写入仍生效",
        written and record is not None,
        f"written={written} read_back={record is not None}",
    )
    report.add("连接是存储层自己建立的", database.available, database.server_version or "-")


async def check_loop_switch(report: Report) -> None:
    """换事件循环后仍能落库。

    这是第二次真实事故的回归。脚本里 `for t in tasks: asyncio.run(run_once(t))`
    是很自然的写法，但连接池绑定在**创建它的那个事件循环**上：
    第二次 `asyncio.run` 用的是新循环，却拿到了属于旧循环的连接。

    实测症状：`ensure_task` 写父行失败（asyncpg 报 "Event loop is closed"），
    随后写质检记录直接外键违约 —— 而任务本身跑得好好的、还返回 ok。
    也就是「看得见图、看不见数据」。

    重现方式与真实事故一致：在**另一个线程的新事件循环**里做一次写入。
    """
    report.section("2.6 事件循环切换后的落库")
    probe = f"{PROBE_TASK}-loop"
    payload = {"kind": "scene", "item": "事件循环探针", "status": "ok"}

    def _write_in_fresh_loop() -> bool:
        return asyncio.run(tasks.upsert_task(probe, payload))

    ok = await asyncio.to_thread(_write_in_fresh_loop)
    record = await tasks.get_task(probe)
    report.add(
        "换事件循环后写入仍生效",
        bool(ok) and record is not None,
        f"written={ok} read_back={record is not None}",
    )
    report.add("连接池已在新循环上重建", database.available, database.server_version or "-")


async def check_pipeline_hooks(report: Report) -> None:
    """跑一次真实任务（出图链落到本地占位图，不联网不花钱），验证落库钩子。

    这一条覆盖的是「单测测不到、又最容易忘」的部分：
    图跑完了但没人写库 —— 功能全对，数据全丢。
    """
    report.section("3. 执行链路落库（真实跑一次任务）")
    from app.graph.runner import run_once

    original_freeimage = settings.freeimage_enabled
    settings.freeimage_enabled = False  # 强制走本地占位图：离线、确定性
    try:
        result = await run_once(
            {"kind": "artifact", "item": "黄金面具", "style": "博物馆纪实摄影", "task_id": f"sxd-{RUN_ID}-e2e"}
        )
    finally:
        settings.freeimage_enabled = original_freeimage

    task_id = str(result.get("task_id") or "")
    record = await tasks.get_task(task_id)
    report.add("任务跑完后库里有记录", record is not None, task_id)
    if record is None:
        return
    report.add("status 已收敛", record["status"] in {"ok", "failed"}, record["status"])
    report.add("落盘了至少一轮出图", len(record["images"]) >= 1, f"{len(record['images'])} 轮")
    report.add("落盘了至少一轮质检", len(record["verdicts"]) >= 1, f"{len(record['verdicts'])} 轮")
    report.add(
        "占位图被如实标为 qa_skipped",
        bool(record["qa_skipped"]),
        f"provider={record['provider']} qa_skipped={record['qa_skipped']}",
    )
    report.add("请求体已入库（含脱敏字段）", bool(record.get("request")), str(record.get("request"))[:60])


async def check_vectors(report: Report) -> None:
    report.section("4. pgvector 向量链路")
    if not (database.available and database.vector_version):
        report.add("pgvector 可用", False, "数据库或扩展不可用，跳过向量验证")
        return

    from app.rag.corpus import load_corpus
    from app.rag.embedder import build_embedder

    corpus = load_corpus(settings.corpus_path)
    entries = corpus.all()
    embedder = build_embedder()
    report.add("语料已装载", len(entries) > 0, f"{len(entries)} 条")

    index = PgVectorIndex(database, embedder.dimension)
    embedded_calls = {"n": 0}

    async def embed_fn(texts: list[str]):
        embedded_calls["n"] += len(texts)
        return await embedder.embed(texts), embedder.name

    report_obj = await index.ensure(entries, embed_fn, embedder.name)
    report.add("向量写入 pgvector", report_obj.ok, f"新算 {report_obj.embedded} 条，{report_obj.reason}")
    report.add("库里向量条数达标", report_obj.count >= len(entries), f"{report_obj.count} / {len(entries)}")

    # 复用：第二次不应该再调用 embedding
    before = embedded_calls["n"]
    second = await index.ensure(entries, embed_fn, embedder.name)
    report.add(
        "第二次启动复用已有向量（省掉 embedding 调用）",
        second.reused >= len(entries) and embedded_calls["n"] == before,
        f"reused={second.reused}, embedded={second.embedded}, 新增调用={embedded_calls['n'] - before}",
    )

    # 复用判定的正确性：改一条史料的**正文**（doc_id 不变），它必须被重新向量化。
    # 只看 doc_id 的复用逻辑在这里会静默复用旧向量 —— 检索变差但没有任何报错。
    probe = entries[0]
    original_text = probe.text
    try:
        probe.text = original_text + "（自检追加：用于改变内容指纹）"
        mutated = await index.ensure(entries, embed_fn, embedder.name)
        report.add(
            "正文改动过的史料会被重新向量化",
            mutated.embedded == 1 and mutated.reused == len(entries) - 1,
            f"embedded={mutated.embedded} reused={mutated.reused}",
        )
    finally:
        probe.text = original_text
    restored = await index.ensure(entries, embed_fn, embedder.name)
    report.add(
        "改回正文后指纹恢复一致（指纹对内容敏感）",
        restored.embedded == 1 and restored.reused == len(entries) - 1,
        f"embedded={restored.embedded} reused={restored.reused}",
    )

    # 与进程内实现数值对比 —— 验证 1 - (embedding <=> q) 的换算是对的
    memory = MemoryVectorIndex(embedder.dimension)
    await memory.ensure(entries, embed_fn, embedder.name)
    query_vector = (await embedder.embed(["青铜大立人的形制与纹饰"], kind="query"))[0]

    pg_scores = await index.search(query_vector, limit=10)
    mem_scores = await memory.search(query_vector, limit=10)
    report.add("pgvector 返回了候选", bool(pg_scores), f"{len(pg_scores)} 条")

    shared = sorted(set(pg_scores) & set(mem_scores))
    worst = max((abs(pg_scores[d] - mem_scores[d]) for d in shared), default=1.0)
    report.add(
        "与进程内余弦分数量级一致（换算无错）",
        bool(shared) and worst < 1e-4,
        f"共同 {len(shared)} 条，最大偏差 {worst:.2e}",
    )
    recall = len(shared) / max(1, len(mem_scores))
    report.add(
        "HNSW 召回与暴力检索一致",
        recall >= 0.9,
        f"top10 重合 {len(shared)}/{len(mem_scores)} ({recall:.0%})",
    )
    print(f"     pgvector top3 = {[d for d in list(pg_scores)[:3]]}")
    print(f"     memory   top3 = {[d for d in list(mem_scores)[:3]]}")

    # HNSW 索引到底能不能用？用 EXPLAIN 说话，别靠猜。
    #
    # 这里要看两个计划，因为「自然计划」在这个数据量下**一定会**是顺序扫描：
    # 50 条 1024 维向量（约 200KB）全表扫比走 HNSW 更快，规划器选 seq scan 是正确的。
    # 所以「自然计划没有 hnsw」不是 bug，拿它当断言反而是错的。
    # 真正要证明的是：**索引是可用的**（禁用 seqscan 后规划器会用它），
    # 以及索引结构真的建出来了 —— 语料涨到几万条时它才会被自然选中。
    # 注意用真实查询向量：余弦距离对零向量未定义，拿全零向量 EXPLAIN 验证的是错的东西。
    query_literal = "[" + ",".join(f"{value:.8g}" for value in query_vector) + "]"

    async def _plan(force_index: bool) -> str:
        async with database.session() as session:
            if force_index:
                await session.execute(text("SET LOCAL enable_seqscan = off"))
            rows = (
                await session.execute(
                    text(
                        """
                        EXPLAIN SELECT doc_id FROM corpus_chunks
                        WHERE embedding IS NOT NULL
                        ORDER BY embedding <=> CAST(:q AS vector) LIMIT 10
                        """
                    ),
                    {"q": query_literal},
                )
            ).all()
            return "\n".join(str(row[0]) for row in rows)

    natural_plan = await _plan(force_index=False)
    forced_plan = await _plan(force_index=True)
    report.add(
        "HNSW 索引可被规划器使用（禁用顺序扫描后命中）",
        "hnsw" in forced_plan.lower(),
        forced_plan.splitlines()[1].strip()[:70] if forced_plan else "-",
    )
    print(
        f"     [info] {len(entries)} 条语料下的自然计划首行: "
        f"{natural_plan.splitlines()[0].strip()[:60]} "
        f"(未走 HNSW 属正常：小表上顺序扫描更快)"
    )


async def check_retriever_switch(report: Report) -> None:
    """换后端不能改变检索排序 —— 这是「换向量库」最容易被忽略的回归点。"""
    report.section("5. 检索器整体对比（pgvector vs 进程内）")
    from app.rag.store import HybridRetriever, reset_retriever
    from app.storage.vectors import MemoryVectorIndex as _Memory

    await reset_retriever()
    pg_retriever = await HybridRetriever.build()
    stats = pg_retriever.stats()
    report.add(
        "检索器实际使用 pgvector",
        stats["vector_backend"] == "pgvector",
        f"{stats['vector_backend']}（复用 {stats['vector_reused']} / 新算 {stats['vector_embedded']}）",
    )

    queries = ["青铜大立人 形制", "黄金面具 锤揲工艺", "纵目面具 眼睛"]
    pg_hits = []
    for query in queries:
        chunks, _ = await pg_retriever.search(query)
        pg_hits.append([chunk.doc_id for chunk in chunks[:3]])

    # 换回进程内实现跑同样的 query。
    # 注意不能靠 _warm_vectors()：它会重新按「数据库可用」挑到 pgvector，
    # 那样比的就是自己跟自己。这里显式注入进程内索引。
    memory_retriever = HybridRetriever(pg_retriever.corpus, pg_retriever.embedder)

    async def memory_embed(texts: list[str]):
        return await pg_retriever.embedder.embed(texts), pg_retriever.embedder.name

    memory_index = _Memory(pg_retriever.embedder.dimension)
    await memory_index.ensure(pg_retriever.corpus.all(), memory_embed, pg_retriever.embedder.name)
    memory_retriever._index = memory_index
    mem_hits = []
    for query in queries:
        chunks, _ = await memory_retriever.search(query)
        mem_hits.append([chunk.doc_id for chunk in chunks[:3]])

    matches = sum(1 for a, b in zip(pg_hits, mem_hits, strict=False) if a == b)
    report.add(
        "两条向量通道的 top3 完全一致",
        matches == len(queries),
        f"{matches}/{len(queries)} 条 query 一致",
    )
    for query, a, b in zip(queries, pg_hits, mem_hits, strict=False):
        flag = "same" if a == b else "DIFF"
        print(f"     [{flag}] {query}: pg={a} | mem={b}")

    await pg_retriever.aclose()
    await memory_retriever.aclose()
    await reset_retriever()


async def check_redis(report: Report) -> None:
    report.section("6. Redis 会话存储")
    if not isinstance(sessions.backend, RedisSessionBackend):
        report.add("Redis 已连接", False, sessions.reason)
        return
    report.add("Redis 已连接", True, sessions.backend_name)

    session = await sessions.get_or_create(f"chat-{RUN_ID}")
    await sessions.append_user(session, "想看青铜大立人的复原")
    await sessions.append_assistant(
        session,
        "好的，这里有三份方案",
        proposals=[{"id": "p1", "title": "展陈纪实"}],
        intent={"kind": "scene"},
        evidence_titles=["发掘简报"],
    )

    # 关键一步：换一个**新的后端实例**去读 —— 等价于换一个进程/副本。
    # 如果实现里少写了 save()，进程内实现会「碰巧」通过，而这里会立刻失败。
    probe = RedisSessionBackend(
        settings.redis_url,
        prefix=settings.redis_key_prefix,
        ttl_seconds=settings.redis_session_ttl_seconds,
        max_sessions=settings.redis_max_sessions,
        timeout=settings.redis_connect_timeout,
    )
    try:
        loaded = await probe.load(f"chat-{RUN_ID}")
    finally:
        await probe.aclose()

    report.add("会话能被另一个进程实例读回", loaded is not None)
    if loaded:
        report.add("轮次完整", len(loaded.turns) == 2, f"{len(loaded.turns)} 轮")
        report.add(
            "提案与意图无损",
            loaded.turns[-1].proposals == [{"id": "p1", "title": "展陈纪实"}]
            and loaded.last_intent == {"kind": "scene"},
            f"proposals={loaded.turns[-1].proposals}",
        )
        report.add("TTL 已挂上", await _ttl(probe, f"chat-{RUN_ID}") > 0)

    stats = await sessions.stats()
    report.add("stats 可用", stats.get("sessions", 0) >= 1, str(stats))

    if not KEEP:
        await sessions.reset(f"chat-{RUN_ID}")
        report.add("测试会话已清理", await sessions.get(f"chat-{RUN_ID}") is None)


async def _ttl(backend: RedisSessionBackend, session_id: str) -> int:
    return int(await backend._client.ttl(backend._key(session_id)))  # noqa: SLF001


async def cleanup(report: Report) -> None:
    report.section("7. 清理")
    if KEEP:
        report.add("按 --keep 保留测试数据", True, PROBE_TASK)
        return

    # 顺便验证外键 CASCADE：删任务应该把产物与质检行一起带走
    before = await database.fetch_all(
        text("SELECT count(*) FROM task_images WHERE task_id LIKE :p"), {"p": f"sxd-{RUN_ID}%"}
    )
    await database.execute(
        text("DELETE FROM restoration_tasks WHERE task_id LIKE :p"), {"p": f"sxd-{RUN_ID}%"}
    )
    after = await database.fetch_all(
        text("SELECT count(*) FROM task_images WHERE task_id LIKE :p"), {"p": f"sxd-{RUN_ID}%"}
    )
    removed = int(before[0][0]) if before else 0
    left = int(after[0][0]) if after else 0
    report.add("任务行已删除", True)
    report.add("外键 CASCADE 生效（产物随任务一起删除）", removed > 0 and left == 0, f"删除前 {removed} 行 -> 之后 {left} 行")


async def main() -> int:
    parser = argparse.ArgumentParser(description="真实存储栈自检")
    parser.add_argument("--keep", action="store_true", help="保留测试数据，便于人工查库核对")
    args = parser.parse_args()

    global KEEP
    KEEP = args.keep

    report = Report()
    print(f"存储自检 run_id = {RUN_ID}")
    print(f"DATABASE_URL = {_mask(settings.database_url)}")
    print(f"REDIS_URL    = {settings.redis_url or '(未配置)'}")

    await database.connect()
    if not database.available:
        print(f"\nPostgreSQL 不可用：{database.reason}")
        print("STORAGE CHECK SKIPPED -- 未验证（不是通过）")
        print("  排查：python scripts/migrate.py --current")
        return 2
    await database.upgrade()
    await sessions.connect()

    await check_infrastructure(report)
    await check_lazy_connect(report)
    await check_loop_switch(report)
    await check_task_persistence(report)
    await check_pipeline_hooks(report)
    await check_vectors(report)
    await check_retriever_switch(report)
    await check_redis(report)
    await cleanup(report)

    print(f"\n{'=' * 74}")
    if report.failed:
        print(f"STORAGE CHECK FAIL -- {len(report.failed)}/{len(report.checks)} 项未通过")
        for name, _, detail in report.failed:
            print(f"  - {name}: {detail}")
        code = 1
    else:
        print(f"STORAGE CHECK PASS -- {len(report.checks)} 项全部通过")
        code = 0

    await sessions.aclose()
    await database.aclose()
    return code


def _mask(url: str) -> str:
    """日志与终端输出里不给密码留痕。"""
    if not url:
        return "(未配置)"
    import re

    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", url)


KEEP = False

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
