"""FastAPI 应用入口。

启动顺序（lifespan）有意固定为：日志 → 引擎 → 语料。理由是语料预热会打网络
请求（向量化），如果引擎先失败，我们希望在启动日志里第一时间看到，
而不是被一堆超时警告淹没。

`/media` 静态挂载用于回放已归档的生成图 —— 前端拿到的是相对路径，
经 vite/server 代理后由这里提供，避免把 base64 塞进 SSE 帧。
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.chat_routes import router as chat_router
from app.api.routes import VERSION, router
from app.core.config import settings
from app.core.errors import SanxingduiError
from app.core.http import proxy_diagnostics
from app.core.logging import get_logger, setup_logging
from app.core.metrics import metrics
from app.graph.builder import build_orchestrator, engine_report
from app.imagegen.base import aclose_clients
from app.imagegen.service import get_image_service
from app.models.registry import registry
from app.rag.rerank import aclose_reranker
from app.rag.store import get_retriever, reset_retriever
from app.storage import bootstrap as bootstrap_storage
from app.storage import record_gauges, shutdown as shutdown_storage

logger = get_logger("app.main")


# 预热任务的强引用。asyncio 只持弱引用，不存一份的话任务可能在跑完前被回收。
_WARMUP_TASKS: set[asyncio.Task[None]] = set()


def _warm_intent_model() -> None:
    """后台预热意图解析模型。

    **为什么值得单独做这件事**（实测数据，`var/_bench.py`，2026-09）：

        模型          首跑        稳定值
        qwen3-max    2238 ms    1776 ms    ← 换掉的那个
        qwen-flash   3293 ms     833 ms    ← 现在用的

    换模型让稳态快了 53%，但**首跑反而更慢** —— 多出来的 2.5 秒全是连接建立
    与冷启动，是每个进程只付一次的固定成本。

    而这条链路在每轮对话的**关键路径**上：用户打完字就在等它，什么都不会先显示。
    把冷启动放在启动时，等于把那 2.5 秒从「用户盯着转圈」挪到「服务刚起来、没人看」。

    用后台任务、不阻塞启动：预热失败只记日志。真不可用时
    `resolve_intent` 本来就会退回规则解析，可用性不受影响。
    """

    async def _ping() -> None:
        try:
            client = registry.client("intent")
            started = time.perf_counter()
            await client.chat(
                [{"role": "user", "content": "ping"}], max_tokens=4, tag="warmup"
            )
            logger.info("意图解析模型已预热（%.0fms）", (time.perf_counter() - started) * 1000)
        except Exception as exc:  # noqa: BLE001 - 预热失败绝不能影响启动
            logger.info("意图解析模型预热跳过（不影响可用性）: %s", exc)

    if not registry.enabled:
        return
    task = asyncio.create_task(_ping())
    _WARMUP_TASKS.add(task)
    task.add_done_callback(_WARMUP_TASKS.discard)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    logger.info("启动三星堆复原多智能体服务 v%s", VERSION)

    orchestrator = build_orchestrator()
    report = engine_report()
    metrics.set_gauge("sxd_engine_langgraph", 1 if orchestrator.name == "langgraph" else 0)
    logger.info(
        "编排引擎: %s%s",
        orchestrator.name,
        f"（降级原因：{report.get('fallback_reason')}）" if report.get("fallback_reason") else "",
    )

    if not registry.enabled:
        logger.warning(
            "未配置 DASHSCOPE_API_KEY：LLM/VLM/Embedding 层将降级为规则与本地实现，"
            "功能可用但质量下降。请在 backend/.env 中填入 Key。"
        )

    # 出图通道的可用性必须如实上报：
    # 之前这里只看本地服务/DashScope，于是「免费通道已生效、图片其实是真的」也会
    # 被报成「将使用本地占位图」。日志说了假话，排查就得靠猜。
    from app.imagegen.service import get_image_service

    image_service = get_image_service()
    provider_status = await image_service.health()
    usable = [name for name, ok in provider_status.items() if ok is True]
    real_channels = [name for name in usable if name != "local-placeholder"]
    priority = " → ".join(image_service.chain())
    if real_channels:
        logger.info("出图通道可用: %s（优先级: %s）", ", ".join(real_channels), priority)
        if "free-third-party" in real_channels and not settings.has_dashscope_key:
            logger.warning(
                "当前仅有第三方免费通道生效：依赖外部站点可用性、图面带水印、受内容审核限制、"
                "匿名配额约 15 秒 1 张。生产请配置 DASHSCOPE_API_KEY，并将 FREEIMAGE_ENABLED 设为 false。"
            )
    else:
        logger.warning(
            "未配置任何真实出图通道，将使用本地占位图（程序生成的示意图，与文物无关）。"
            "填入 DASHSCOPE_API_KEY 即可输出真实图像；此时质检会自动标记为 skipped，不做无意义回炉。"
        )

    # 存储必须早于语料预热：语料向量能不能复用库里已有的结果，取决于
    # 「连上数据库」这件事有没有在检索器建立之前完成。顺序错了，
    # 每次启动都会重新调一遍 embedding —— 功能对，但白花钱。
    storage_report = await bootstrap_storage()
    record_gauges(storage_report)
    _log_storage(storage_report)

    retriever = await get_retriever()
    metrics.set_gauge("sxd_corpus_size", len(retriever.corpus))
    retrieval_stats = retriever.stats()
    logger.info(
        "史料语料: %d 条（向量通道: %s，维度 %d，复用 %d / 新算 %d）",
        len(retriever.corpus),
        retrieval_stats.get("vector_backend"),
        retrieval_stats.get("vector_dimension") or 0,
        retrieval_stats.get("vector_reused") or 0,
        retrieval_stats.get("vector_embedded") or 0,
    )

    _warn_about_proxy()
    _warn_about_auth()
    _warm_intent_model()

    # 指标周期落盘：原先只在关停时 persist 一次，进程被强杀或崩溃时这批
    # 观测数据会全部丢失 —— 而它正是排查「为什么慢 / 为什么降级」的依据。
    metrics_task = asyncio.create_task(_persist_metrics_periodically())

    try:
        yield
    finally:
        logger.info("正在关闭服务…")
        metrics_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await metrics_task
        metrics.persist()
        with contextlib.suppress(Exception):
            await get_image_service().aclose()
        with contextlib.suppress(Exception):
            await registry.aclose()
        with contextlib.suppress(Exception):
            await reset_retriever()
        with contextlib.suppress(Exception):
            await shutdown_storage()
        # 这两个是模块级/全局单例，此前没有关停钩子，关停时会泄漏连接与 fd
        with contextlib.suppress(Exception):
            await aclose_clients()
        with contextlib.suppress(Exception):
            await aclose_reranker()


app = FastAPI(
    title="三星堆文物复原 Multi-Agent 服务",
    description=(
        "基于 LangGraph 的 Supervisor/Worker 多智能体系统："
        "Planner 拆解目标 → 史料检索 / 图像修复 / 科普文案 三个 Worker → "
        "质检 Agent 校验风格一致性，不达标自动回炉（Self-Correction）。"
    ),
    version=VERSION,
    lifespan=lifespan,
)

# 不允许 "*" 与 credentials 同时出现：浏览器本就拒绝这种组合，
# 而配置为空时退化为 "*" 等于把接口开放给任意站点（含真实计费的出图端点）。
_cors_origins = settings.cors_origin_list or ["http://localhost:5173", "http://127.0.0.1:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)


@app.middleware("http")
async def api_key_gate(request: Request, call_next):
    """可选 API Key 闸门（生产必开）。

    未设 API_KEY 时完全放行（本地开发方便），但启动会告警；设了之后
    /api/* 必须带 X-API-Key。/api/health 例外（便于探活），OPTIONS 预检
    也放行（否则跨域调用会先被 401 挡在预检阶段）。

    没有这道闸门时，出图端点对匿名访问完全敞开 —— 单任务最多 3 次真实出图，
    几个并发就能把上游额度打穿。
    """
    expected = settings.api_key.strip()
    if not expected or request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if not path.startswith("/api/") or path == "/api/health":
        return await call_next(request)

    provided = request.headers.get("X-API-Key", "").strip()
    if not provided:
        provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    # 定长时间比较，避免通过响应耗时侧信道逐字节猜 Key
    if not hmac.compare_digest(provided, expected):
        return JSONResponse(status_code=401, content={"detail": "缺少或错误的 API Key"})
    return await call_next(request)

app.include_router(router)
app.include_router(chat_router)


@app.exception_handler(SanxingduiError)
async def handle_domain_error(_: Request, exc: SanxingduiError) -> JSONResponse:
    logger.warning("领域异常: %s", exc.to_dict())
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


METRICS_PERSIST_INTERVAL = 60.0


async def _persist_metrics_periodically() -> None:
    """每隔一段时间把进程内指标落盘，避免崩溃时整批观测数据丢失。"""
    while True:
        try:
            await asyncio.sleep(METRICS_PERSIST_INTERVAL)
            with contextlib.suppress(Exception):
                metrics.persist()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 周期任务不该拖垮主服务
            logger.warning("周期落盘指标失败")


def _warn_about_auth() -> None:
    """未设 API_KEY 时明确告警。

    出图端点会真实计费（单任务最多 3 次出图 + VLM 质检），匿名敞开有账单风险。
    这里只是告警而不是拒绝启动 —— 本地开发不该被强制配 Key。
    """
    if not settings.api_key.strip():
        logger.warning(
            "未设置 API_KEY —— /api/* 处于无鉴权状态。出图端点会真实计费"
            "（单任务最多 3 次出图 + VLM 质检），生产环境请务必配置 API_KEY。"
        )


def _mount_media() -> None:
    try:
        settings.output_path.mkdir(parents=True, exist_ok=True)
        app.mount("/media", StaticFiles(directory=str(settings.output_path)), name="media")
    except Exception as exc:  # noqa: BLE001 - 挂载失败不应导致服务无法启动
        logger.warning("挂载 /media 失败: %s", exc)


def _log_storage(report: dict[str, Any]) -> None:
    """把存储三层的实际档位打进启动日志。

    这条日志的存在意义是**让降级被看见**。
    「配了 DATABASE_URL 却连不上」如果只表现为「任务没落库」，
    可能要等到有人想查历史任务时才发现；而启动日志里一行
    `PostgreSQL=disabled(原因)` 会让它当场暴露。
    """
    database = report.get("database", {})
    sessions = report.get("sessions", {})
    vector = report.get("vector", {})

    logger.info(
        "存储: PostgreSQL=%s%s, 会话=%s%s, 向量=%s%s",
        database.get("server_version") or "disabled",
        f"（schema {database.get('schema_revision')}）" if database.get("schema_revision") else "",
        sessions.get("backend"),
        "" if sessions.get("available") else f"（{sessions.get('reason')}）",
        vector.get("backend"),
        "" if vector.get("backend") == "pgvector" else f"（{vector.get('reason')}）",
    )


def _warn_about_proxy() -> None:
    """检测系统代理是否会把本机请求也接管 —— 这是「服务在跑但请求 502」的头号原因。"""
    report = proxy_diagnostics()
    proxies = {**report.get("system_wininet", {}), **report.get("env", {})}
    if not proxies:
        return

    bypass_hosts = "localhost,127.0.0.1,::1"
    logger.warning(
        "检测到系统/环境代理: %s。本服务已对「本机与私有网段」请求禁用代理，"
        "但若你自行调用本服务时遇到 502 空响应，请在客户端设置 NO_PROXY=%s。",
        proxies,
        bypass_hosts,
    )


_mount_media()


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "service": "sanxingdui-multiagent-restoration",
        "version": VERSION,
        "docs": "/docs",
        "health": "/api/health",
    }
