"""HTTP API 层。

全部端点分成五组：
- **执行**：`/api/restore`（同步）、`/api/restore/stream`（SSE 流式，前端主用）
- **元信息**：`/api/health`、`/api/models`、`/api/graph`、`/api/styles`
- **问答**：`/api/ask`（领域问答，只依据本项目语料并回带引用）
- **观测**：`/api/metrics`、`/api/traces/{task_id}`
- **治理**：`/api/corpus/reload`、`/api/eval/run`

SSE 帧约定（前端 `agentApi.ts` 按此解析）：
    event: start | node | trace | done | error | ping
    data : JSON

`trace` 帧携带细粒度 Agent 轨迹（node_start / llm_call / qa_verdict / degraded…），
`node` 帧携带粗粒度阶段进度与增量结果。两者交错输出，前端可同时画
「Agent 时间线」与「阶段进度条」——进度值来自真实节点，不再是随机数。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import (
    AskRequest,
    AskResponse,
    EvalRequest,
    EvalResponse,
    HealthResponse,
    RestoreRequest,
    RestoreResponse,
)
from app.core.config import settings
from app.conversation.smalltalk import classify_smalltalk
from app.qa.answerer import answer as ask_question
from app.core.http import proxy_diagnostics
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.core.tracing import TraceEvent
from app.graph.builder import engine_report, get_orchestrator
from app.graph.runner import RestorationRunner
from app.models.registry import registry
from app.quality.style_profiles import all_profiles
from app.rag.store import get_retriever, reset_retriever
from app.storage import health as storage_health
from app.storage import tasks as task_repository

logger = get_logger("app.api")
router = APIRouter(prefix="/api")

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # 让 nginx 不要缓冲 SSE
}

# 出图并发配额。出图是付费长耗时调用（单任务 = 1 次出图 + 1 次 VLM 质检
# + 最多 2 次回炉），没有上限时几个并发请求就能打穿上游额度并触发全局降级。
# 这里做背压而不是排队丢弃：超出的请求会等前一个让出配额。
_RESTORE_SLOTS: asyncio.Semaphore | None = None


def _restore_slots() -> asyncio.Semaphore:
    global _RESTORE_SLOTS
    if _RESTORE_SLOTS is None:
        _RESTORE_SLOTS = asyncio.Semaphore(max(1, settings.max_concurrent_restore))
    return _RESTORE_SLOTS

PING_INTERVAL = 15.0
VERSION = "2.0.0"


# ════════════════════════════════════════════════════════════════════════════
#  执行
# ════════════════════════════════════════════════════════════════════════════
@router.post("/restore/stream")
async def restore_stream(payload: RestoreRequest, request: Request) -> StreamingResponse:
    """SSE 流式复原。前端用 fetch + ReadableStream 消费（EventSource 不支持 POST）。"""
    agent_request = payload.to_agent_request()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=4096)
    eof = object()

    def push(kind: str, data: Any) -> None:
        try:
            queue.put_nowait((kind, data))
        except asyncio.QueueFull:  # 客户端太慢时丢帧而不是拖垮服务端
            logger.warning("SSE 队列已满，丢弃 %s 帧", kind)

    def on_trace(event: TraceEvent) -> None:
        push("trace", event.to_dict())

    runner = RestorationRunner(agent_request, sink=on_trace)

    async def produce() -> None:
        # 整个流期间持有配额：出图是付费长耗时调用，并发无上限会打穿上游额度
        async with _restore_slots():
            try:
                async for event in runner.astream():
                    push(str(event.get("type") or "node"), event)
            except Exception as exc:  # noqa: BLE001
                logger.exception("SSE 生产任务异常")
                # 对外只回类型 + 通用文案 + task_id：异常原文常含上游响应体、
                # SQL 片段或内部路径，不该回给匿名客户端。原文只进日志与 trace。
                push("error", {
                    "type": "error",
                    "message": "任务执行失败，请稍后重试或联系管理员",
                    "error_type": type(exc).__name__,
                    "task_id": runner.task_id,
                })
            finally:
                push("__eof__", eof)

    async def event_source():
        task = asyncio.create_task(produce())
        try:
            while True:
                if await request.is_disconnected():
                    logger.info("客户端断开，终止 SSE 流", extra={"task_id": runner.task_id})
                    break
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if kind == "__eof__":
                    break
                yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    return StreamingResponse(event_source(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/restore", response_model=RestoreResponse)
async def restore(payload: RestoreRequest) -> RestoreResponse:
    """同步复原（评估脚本、调试、以及不支持 SSE 的客户端使用）。"""
    async with _restore_slots():
        result = await RestorationRunner(payload.to_agent_request()).arun()
    if not result:
        raise HTTPException(status_code=500, detail="执行器未返回结果")

    return RestoreResponse(
        ok=bool(result.get("ok")),
        task_id=str(result.get("task_id") or ""),
        engine=str(result.get("engine") or "unknown"),
        duration_ms=float(result.get("duration_ms") or 0.0),
        image_url=result.get("image_url"),
        image_provider=result.get("image_provider"),
        image_degraded=bool(result.get("image_degraded")),
        qa=result.get("qa") or {},
        revisions=int(result.get("revisions") or 0),
        goal=result.get("goal"),
        copy_payload=result.get("copy") or {},
        evidence=result.get("evidence") or [],
        plan=result.get("plan") or {},
        image=result.get("image") or {},
        revision_history=result.get("revision_history") or [],
        retrieval=result.get("retrieval") or {},
        degraded_components=result.get("degraded_components") or [],
        errors=result.get("errors") or [],
        usage=result.get("usage") or {},
        error=result.get("error"),
        state=result.get("state"),
    )


# ════════════════════════════════════════════════════════════════════════════
#  元信息
# ════════════════════════════════════════════════════════════════════════════
@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    from app.imagegen.service import get_image_service

    retriever = await get_retriever()
    providers = await get_image_service().health()
    return HealthResponse(
        status="ok",
        engine=engine_report(),
        providers=providers,
        retrieval=retriever.stats(),
        models_configured=registry.enabled,
        style_profiles=len(all_profiles()),
        version=VERSION,
        # 把「本进程会走哪个代理」显式暴露出来：
        # Windows 上系统代理不含 localhost 例外，是「服务起着却返回 502」的最常见原因
        network=proxy_diagnostics(),
        storage=storage_health(),
    )


@router.get("/models")
async def models() -> dict[str, Any]:
    """模型选型矩阵 —— 让「为什么用这个模型」变成可查询的接口，而不是散落在注释里。"""
    return registry.matrix()


@router.get("/graph")
async def graph() -> dict[str, Any]:
    return {
        "engine": get_orchestrator().name,
        "configured": settings.engine_backend,
        "fallback_reason": engine_report().get("fallback_reason"),
        "max_revisions": settings.max_revisions,
        "style_threshold": settings.style_threshold,
        "max_graph_steps": settings.max_graph_steps,
        "topology": engine_report().get("topology"),
    }


@router.get("/styles")
async def styles() -> dict[str, Any]:
    return {"profiles": all_profiles(), "default_threshold": settings.style_threshold}


@router.get("/corpus")
async def corpus_overview() -> dict[str, Any]:
    retriever = await get_retriever()
    return retriever.stats()


# /api/ask 的闲聊应答。这里服务的是「古蜀大祭司」问答面板（FloatingAssistant），
# 文案沿用其人设，与 /api/chat/stream 的固定话术区分开。
_ASK_SMALLTALK_REPLIES: dict[str, str] = {
    "identity": (
        "吾乃古蜀大祭司，守护三星堆圣地三千载。汝可问「青铜纵目面具的宽和高」，"
        "亦可问「金杖上刻的是什么图案」——凡典籍有载者，吾必据实相告，不作妄语。"
    ),
    "capability": (
        "吾可为你解答三星堆与古蜀文明之疑：文物形制、出土始末、典籍记载，皆有出处可循。"
        "若典籍无载，吾会直说不知。"
    ),
    "chat": (
        "与吾闲谈亦可。然吾所长者，乃三星堆之史事——"
        "不妨问吾「青铜神树有几层几枝」，或「蚕丛纵目出自何处」。"
    ),
    "greeting": "汝好。有疑尽管相询——三星堆之文物史事，凡典籍有载者，吾必据实相告。",
    "thanks": "不必言谢。若还有疑，尽可再问。",
}

@router.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest) -> AskResponse:
    """领域问答：**只依据本项目的语料库作答**，并回带可点开的引用。

    为什么不接外部对话服务（Dify 之类）：引用必须来自我们自己的语料，
    否则「每条事实都能溯源」这句话就不成立 —— 那正是本项目要消灭的
    「看起来有出处、实际追不到」的问题。

    行为契约（详见 app/qa/answerer.py）：
    - 检索不到可依据的记载 → `refused=true`，明确说不能回答；
    - 自撰内容与来源不明的条目**一律不作依据**；
    - 引用的展示内容取自语料条目，不经模型改写。
    """
    # 非知识类消息（问候/身份/致谢/聊天请求）不进语料检索——
    # 「你是谁」去查史料只会得到「无相关记载」。规则层直接应答，
    # 判定与 /api/chat/stream 的闲聊闸门是同一套（classify_smalltalk）。
    smalltalk = classify_smalltalk(payload.question)
    if smalltalk is not None:
        category, _ = smalltalk
        metrics.inc("sxd_ask_smalltalk_total")
        return AskResponse(
            question=payload.question,
            answer=_ASK_SMALLTALK_REPLIES[category],
            confidence="high",
            retrieval_mode="smalltalk",
        )
    result = await ask_question(payload.question, top_n=payload.top_n)
    return AskResponse(**result.to_dict())


# ════════════════════════════════════════════════════════════════════════════
#  观测
# ════════════════════════════════════════════════════════════════════════════
@router.get("/metrics")
async def get_metrics() -> dict[str, Any]:
    return metrics.snapshot()


@router.get("/traces/{task_id}")
async def get_trace(task_id: str) -> dict[str, Any]:
    if not task_id.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="非法的 task_id")

    path = settings.trace_path / f"{task_id}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="未找到该任务的轨迹文件")

    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {"task_id": task_id, "count": len(events), "events": events}


# ── 历史任务（来自 PostgreSQL，不是进程内计数）──────────────────────────────
# 这一组端点是「接入数据库」这件事唯一有价值的证明：
# 它们回答的问题（上周那批青铜面具平均回炉几次、哪一类器物的通过率最低）
# 在只有 JSONL trace 的时候**根本没法用查询回答**。
@router.get("/tasks")
async def list_tasks(
    limit: int = 20,
    offset: int = 0,
    kind: str | None = None,
    decision: str | None = None,
    passed: bool | None = None,
    item: str | None = None,
) -> dict[str, Any]:
    return await task_repository.list_tasks(
        limit=max(1, min(limit, 200)),
        offset=max(0, offset),
        kind=kind,
        decision=decision,
        passed=passed,
        item=item,
    )


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    record = await task_repository.get_task(task_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="未找到该任务（也可能只是 DATABASE_URL 未配置或数据库不可用）",
        )
    return record


@router.get("/stats/quality")
async def quality_stats(days: int = 30) -> dict[str, Any]:
    """质量统计：通过率、平均分、平均回炉次数，按任务类型分组。

    与 `/api/metrics` 的区别是**数据来源**：metrics 是进程内计数器（重启归零），
    这里是 SQL 聚合（跨重启累计）。两者对不上时，优先信这个 ——
    它对应的是真实落库的每一行任务记录。
    """
    return await task_repository.quality_stats(days=max(1, min(days, 365)))


# ════════════════════════════════════════════════════════════════════════════
#  治理
# ════════════════════════════════════════════════════════════════════════════
@router.post("/corpus/reload")
async def reload_corpus() -> dict[str, Any]:
    """热更新史料语料：改完 jsonl 不用重启服务。"""
    await reset_retriever()
    retriever = await get_retriever()
    logger.info("语料已热重载: %d 条", len(retriever.corpus))
    return {"ok": True, **retriever.stats()}


@router.post("/eval/run", response_model=EvalResponse)
async def run_eval(payload: EvalRequest) -> EvalResponse:
    """跑风格错配率基准。会真实调用出图与质检，注意成本与耗时。"""
    # 默认关闭：它会真实跑 N 条完整出图链路（limit 上限 60），
    # 无鉴权无限流时一行 curl 就能产生高额账单。需显式开启后再用。
    if not settings.eval_endpoint_enabled:
        raise HTTPException(
            status_code=403,
            detail="评估端点已关闭。它会真实出图并计费，需显式设置 EVAL_ENDPOINT_ENABLED=true 后使用。",
        )
    from app.eval.harness import run_benchmark

    started = time.perf_counter()
    report = await run_benchmark(limit=payload.limit, engine_override=payload.engine)
    logger.info("评估完成，耗时 %.1fs", time.perf_counter() - started)
    return EvalResponse(
        summary=report["summary"],
        cases=report["cases"],
        report_path=report.get("report_path"),
    )


__all__ = ["router"]
