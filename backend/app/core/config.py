"""全局配置。

所有配置项均可通过环境变量覆盖，且**不存在必填项**：
未配置 DashScope Key 时，LLM/VLM/Embedding 层会降级为规则或本地实现，
整条 Agent 流水线依然可以跑完并产出可解释的结果。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # 本项目所有模型配置项都以 model_ 开头（model_planner 等），
        # 这里显式放开 pydantic 的 protected namespace 检查。
        protected_namespaces=("settings_",),
    )

    # ── 服务 ────────────────────────────────────────────────────────────────
    backend_host: str = "127.0.0.1"
    backend_port: int = 8123
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8023"

    # ── 安全 / 成本护栏 ────────────────────────────────────────────────────
    # 留空 = 不鉴权（本地开发方便）；生产**必须**设置：此时 /api/* 需带
    # X-API-Key 头。没有这道闸门时，出图端点（真实计费）对匿名访问完全敞开。
    api_key: str = ""
    # 同时进行的复原任务上限。出图是付费长耗时调用（单任务 = 1 次出图
    # + 1 次 VLM 质检 + 最多 2 次回炉），无上限时几个并发就能打爆上游额度。
    max_concurrent_restore: int = 3
    # /api/eval/run 会真实跑 N 条完整出图链路（limit 上限 60）。
    # 默认关闭：无鉴权无限流时，一行 curl 就能产生高额账单。
    eval_endpoint_enabled: bool = False

    # ── 编排引擎 ────────────────────────────────────────────────────────────
    engine_backend: Literal["langgraph", "builtin"] = "langgraph"
    max_revisions: int = 2
    style_threshold: float = 0.72
    # 图跳转步数安全阀。预算算法：
    #   planner(1) + supervisor(1+2×revisions) + retrieval(1) + restoration(1+revisions)
    #   + quality(1+revisions) + copywriting(1) + finalize(1)
    # max_revisions=2 时正常路径约 14 步，因此上限取 18 留出余量；
    # 它只在 Supervisor 出错时才会触发，不应成为正常运行时的第一道约束。
    max_graph_steps: int = 18

    # ── DashScope ───────────────────────────────────────────────────────────
    # 用 SecretStr 而不是 str：配置对象会进 repr、会进 traceback，明文 str 会跟着泄露。
    dashscope_api_key: SecretStr = SecretStr("")
    dashscope_base_url: str = "https://dashscope.aliyuncs.com"

    # ── 模型选型 ────────────────────────────────────────────────────────────
    model_supervisor: str = "qwen3-max"
    model_planner: str = "qwen3-max"
    model_retrieval: str = "qwen-plus"
    # 意图解析：把用户一句话压成「谁是主角 + 身份 + 场景 + 器物 + 动作」。
    #
    # **这是每轮对话的关键路径 —— 用户在等它**，而它做的事很轻：
    # 候选词表已由规则层预筛好随请求给出，模型只做「选择 + 一句短改写」，
    # 不引入任何新事实（材质、形制、尺寸都来自词典，它碰不到）。
    #
    # 原先它借用 planner 的 qwen3-max —— 让最慢的模型干最轻的活，
    # 每轮对话白等一两秒。换快模型；即使抽偏，`rule_intent` 也会兜底。
    # 备选：qwen-turbo（更便宜）／ qwen-plus（更稳，但慢一档）。
    model_intent: str = "qwen-flash"
    # 领域问答：把检索到的史料片段组织成带引用的回答。
    # 与 retrieval 同属「受限阅读理解」—— 答案内容必须来自给定片段，
    # 不需要（也不应该）发挥创作能力，所以同样用中档模型即可。
    model_qa: str = "qwen-plus"
    model_copywriter: str = "qwen3-max"
    model_judge: str = "qwen3-vl-plus"
    model_vlm: str = "qwen3-vl-plus"
    model_embedding: str = "text-embedding-v3"
    model_rerank: str = "gte-rerank-v2"
    # 主出图模型。选它的理由：文物复原最难的不是"画得漂亮"，而是**材质对不对**——
    # 青铜锈层的颗粒感、锤撰金箔的哑金质感、玉的温润透光。
    # qwen-image 系列在纹理细节与真实质感上是目前百炼里最合适的一档。
    #
    # 可替换项（均为同步接口，改这里即可切换）：
    #   qwen-image-3.0-pro  最新旗舰，提示词遵循与材质表现最强，价格最高
    #   qwen-image-2.0-pro  推荐：真实质感与语义遵循好，支持自由宽高
    #   qwen-image-max      真实感/自然度强，AI 合成痕迹低，但只支持固定预设尺寸
    #   qwen-image-plus     更便宜，擅长多样化艺术风格
    #   z-image-turbo       最快最便宜，适合先把链路跑通
    model_image: str = "qwen-image-2.0-pro"
    # 出图端点路径。qwen-image 全系列用 multimodal-generation（同步，无需轮询）；
    # 若以后换成 wan 系列（如需要 4K 或 color_palette 色板控制），
    # 这里改成 /api/v1/services/aigc/text2image/image-synthesis 即可，不必改代码。
    image_endpoint_path: str = "/api/v1/services/aigc/multimodal-generation/generation"
    # 提示词增强：交给模型自行扩写提示词。
    # 默认关闭 —— 我们的提示词是精心拼装且向用户承诺「逐字使用」的，
    # 被模型改写等于架空锁定契约与质检标尺。仅在提示词很短、需要模型补细节时开启。
    image_prompt_extend: bool = False
    # 是否在生成图上打「AI 生成」水印（百炼默认 false；显式写出便于审计）
    image_watermark: bool = False
    image_timeout: float = 300.0
    model_fallback_chat: str = "qwen-plus"
    model_fallback_vlm: str = "qwen-vl-max"

    # ── 免鉴权第三方出图通道（无 Key 时的真实出图兜底）──────────────────────
    # 优先级低于 DashScope，只高于本地占位图。
    # 代价：不保证稳定性、带第三方水印、内容审核不受控。生产环境建议关闭。
    # 默认关闭：它会把用户提示词发往不可控的第三方公开服务（带水印、
    # 内容审核不受控、随时可能限流）。需要「零配置真实出图」体验时再显式开启。
    freeimage_enabled: bool = False
    freeimage_url_template: str = (
        "https://image.pollinations.ai/prompt/{prompt}"
        "?width={width}&height={height}&model={model}&nologo=true&seed={seed}"
    )
    # 模型名会随上游调整而失效。实测 2026-09：`GET https://image.pollinations.ai/models`
    # 返回 ["sana"]，旧的 `flux` 已被下线，传它会落回随机模型并频繁命中限流。
    # **上线前先查一次 /models**，别把模型名当常量用。
    freeimage_model: str = "sana"
    freeimage_timeout: float = 120.0
    # 注册 token（auth.pollinations.ai，免费）可把配额从「匿名：每 15 秒 1 次」提升到
    # 「Seed：每 5 秒 1 次」，并去除水印。留空则走匿名档。
    freeimage_token: str = ""
    # 匿名档是 15 秒一个窗口，退避必须跨越它，否则重试一定撞在同一窗口上。
    # 2s/4s 这种常规退避在 15s 窗口面前等于没重试。
    freeimage_attempts: int = 2
    freeimage_backoff_seconds: float = 16.0
    # 提示词预算。**这个上限是实测出来的，不是猜的。**
    #
    # 复现：`python scripts/probe_freeimage_tail.py 0 1 2`
    # 做法：构造两个头部完全相同、只差结尾一句的提示词，分别出图后逐像素比较。
    #   288 字 -> 尾部生效（81.7% 像素不同）
    #   436 字 -> 尾部被**静默丢弃**（最大像素差 0，589824 个像素全同）
    #   584 字 -> 同样被丢弃
    # 即：超过有效窗口的部分不会报错，只会**静默消失**。
    #
    # 取 256 = 低于实测生效上限（288）约 11%，留出余量。
    # 之所以不敢贴着 288 取：填充步长约 148 字，真实边界只能定位在 (288, 436] 区间内，
    # 且窗口很可能按 token 计，中文与英文的换算并不固定。
    #
    # 配套约束：回炉修正指令已前置到提示词开头（restoration_worker._compose_prompt），
    # 所以预算内一定包含它。**不要**再把预算调大 —— 调大不会让模型看到更多，
    # 只会让超窗的部分悄无声息地丢掉，重新制造「回炉无效」。
    freeimage_max_prompt_chars: int = 256

    # ── 本地 QLoRA ──────────────────────────────────────────────────────────
    lora_base_model: str = "Qwen/Qwen2.5-VL-7B-Instruct"
    lora_adapter_path: str = ""
    lora_enabled: bool = False

    # ── 检索 ────────────────────────────────────────────────────────────────
    corpus_dir: str = "data/corpus"
    retrieval_top_k: int = 8
    retrieval_rerank_top_n: int = 4

    # 问答链路取几条证据。比复原任务（top_n=4）多一些：
    # 问答要给「依据是否充分」留出判断空间，且引用通常不止一条；
    # 但也不能太多 —— 片段越多，模型越容易把不相干的片段也用上。
    qa_top_n: int = 6

    # BM25 与向量分数的融合权重（alpha 给 BM25，1-alpha 给向量）。
    #
    # **0.20 是实测出来的，不是沿用下来的。** 此前这里写 0.45，从未被验证过。
    # 有了 golden set 之后做了一次扫描（v2 语料 150 条 / 评测集 57 条 / 真实 embedding）：
    #
    #   alpha   recall@1  recall@5  recall@10   MRR    nDCG   未召回
    #   0.00      0.807     0.947     0.965    0.871   0.892    2
    #   0.20      0.930     0.982     0.982    0.956   0.958    1   ← 最优
    #   0.30      0.895     0.982     0.982    0.939   0.945    1
    #   0.45      0.860     0.982     0.982    0.918   0.932    1   （原默认值）
    #   0.60      0.825     0.982     1.000    0.896   0.923    0
    #   1.00      0.719     0.982     1.000    0.822   0.863    0
    #
    # 0.20 在每一项上都不劣于 0.45（R@5/R@10/未召回持平，R@1 +0.070、MRR +0.038）。
    #
    # **存在的权衡要写清楚**：alpha 越大（越偏 BM25），recall@10 越好、漏召越少
    # （0.60 起漏召为 0），但首条命中率下降。所以最优值取决于下游怎么用：
    #   - 直接展示 top-1：取 0.20（本节目的问答链路目前如此）
    #   - 先取候选再交给 rerank 精排：应偏向 0.60 附近，因为那时真正重要的是
    #     「候选里有没有正确答案」，而不是它排第几。
    # 启用 gte-rerank-v2 后请重新扫一遍。
    hybrid_alpha: float = 0.20

    # ── 观测 ────────────────────────────────────────────────────────────────
    trace_dir: str = "var/traces"
    metrics_dir: str = "var/metrics"
    archive_outputs: bool = True
    output_dir: str = "var/outputs"

    # ── 持久化：PostgreSQL ──────────────────────────────────────────────────
    # 任务 / 生成图产物 / 质检判定落库，支撑「按时间、按器类、按是否通过」查询
    # 与基于 SQL 的质量统计（而不是进程内计数器，重启即归零）。
    #
    # 留空 = 不启用数据库。本项目的所有外部依赖都遵循「不配也能启动」，
    # 数据库也不例外；但**一旦配上，/api/health 会如实报告它到底连上没有**，
    # 不允许「配了却没连上」被静默吞掉。
    database_url: str = ""
    # SSE 长任务会逐轮落库（每轮出图 + 质检各一次写），并发一高容易打满。
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_echo: bool = False
    # 启动时自动把 schema 升到最新（Alembic）。默认开：本地开发少一步手工操作；
    # 生产多实例并发启动时建议关掉，改成发布流程里单独执行一次。
    db_auto_migrate: bool = True
    db_connect_timeout: float = 5.0

    # ── 向量库：pgvector（与上面的 PostgreSQL 同库，不额外部署服务）──────────
    # 语料向量持久化在 corpus_chunks.embedding，用 HNSW + 余弦距离做召回。
    # 数据库不可用 / 扩展缺失时自动退回进程内 numpy 暴力检索 —— 召回质量不变
    # （50~万级语料下暴力检索本身就够快），变的是「重启要不要重新花钱 embedding」。
    vector_dim: int = 1024
    vector_hnsw_m: int = 16
    vector_hnsw_ef_construction: int = 64
    # 命中库里已持久化且 embedder 名一致的向量时，跳过 embedding 调用。
    # 这是接向量库最直接的收益：重启不再重复为同一批语料付钱。
    vector_reuse: bool = True

    # ── 会话存储：Redis ─────────────────────────────────────────────────────
    # 会话是短生命周期状态，但要跨进程共享（多副本部署、滚动重启不丢上下文）。
    # 留空 / 连不上 = 退回进程内实现，行为一致，只是不能跨进程。
    redis_url: str = ""
    redis_key_prefix: str = "sxd"
    redis_session_ttl_seconds: int = 1800
    redis_max_sessions: int = 200
    redis_connect_timeout: float = 3.0

    # ── 派生属性 ────────────────────────────────────────────────────────────
    @field_validator("style_threshold")
    @classmethod
    def _check_threshold(cls, value: float) -> float:
        if not 0.0 < value <= 1.0:
            raise ValueError("STYLE_THRESHOLD 必须落在 (0, 1] 区间")
        return value

    @field_validator("hybrid_alpha")
    @classmethod
    def _check_alpha(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("HYBRID_ALPHA 必须落在 [0, 1] 区间")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def dashscope_key_plain(self) -> str:
        """明文 Key 的唯一出口。

        用 SecretStr 存是为了防 repr/日志泄露；但调用第三方 SDK 必须给明文，
        所以统一从这里取，避免各处各自 get_secret_value()。
        兼容 str 是为了不破坏测试/脚本里 `settings.dashscope_api_key = ""` 的写法。
        """
        value = self.dashscope_api_key
        return value.get_secret_value() if isinstance(value, SecretStr) else str(value or "")

    @property
    def has_dashscope_key(self) -> bool:
        return bool(self.dashscope_key_plain.strip())

    @property
    def openai_compatible_base(self) -> str:
        return f"{self.dashscope_base_url.rstrip('/')}/compatible-mode/v1"

    def resolve(self, relative: str) -> Path:
        """把配置里的相对路径解析到 backend/ 根目录下。"""
        path = Path(relative)
        return path if path.is_absolute() else (BACKEND_ROOT / path)

    @property
    def corpus_path(self) -> Path:
        return self.resolve(self.corpus_dir)

    @property
    def trace_path(self) -> Path:
        return self.resolve(self.trace_dir)

    @property
    def metrics_path(self) -> Path:
        return self.resolve(self.metrics_dir)

    @property
    def output_path(self) -> Path:
        return self.resolve(self.output_dir)

    @property
    def image_endpoint_url(self) -> str:
        """出图端点完整 URL（兼容百炼的两种域名形式）。"""
        path = self.image_endpoint_path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.dashscope_base_url.rstrip('/')}{path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
