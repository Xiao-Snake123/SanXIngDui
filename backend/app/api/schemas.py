"""API 请求 / 响应模型。

字段刻意设计成**跨 Tab 共用的大扁平结构**，而不是四套独立 schema：
前端四个 Tab（场景复原 / 人物还原 / 文物修复 / 风格迁移）共用同一条 Agent 流水线，
差别只在 Planner 的解析分支。扁平结构让「新增一个复原类型」变成
「加几个可选字段 + 加一个 Planner 分支」，而不是新增一套接口。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

RestoreKind = Literal["scene", "figure", "artifact", "style"]


class PromptOverride(BaseModel):
    """用户在对话中选择/编辑过的提示词方案。

    一旦提供，它就是出图 prompt 的**唯一事实来源**：Planner 不得再改写它，
    也不能追加自己的视觉修饰词——否则用户「选了这个方案」就失去了意义。
    Planner 仍然会做检索、验收标准与文案，只是不再动 prompt。
    """

    prompt: str = Field(min_length=10, max_length=6000)
    negative_prompt: str = Field(default="", max_length=2000)
    profile_key: str | None = Field(default=None, description="目标风格档案 key（影响质检阈值）")
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1280, ge=256, le=2048)
    # 注意：这里曾出现 steps / cfg / lora_strength 三个字段。
    # 它们是扩散采样器（ComfyUI 链路）的参数，本项目改为 API 出图后不再生效，
    # 因此移除：接口上留一个不生效的旋钮比没有旋钮更糟 —— 调用方会以为它在起作用。
    # 出图侧真正可调的只有尺寸、seed、参考图与下面的提示词增强开关。
    prompt_extend: bool = Field(
        default=False,
        description="是否允许模型自行扩写提示词。默认 false：选定方案必须逐字使用。",
    )
    proposal_id: str | None = Field(default=None, description="来源方案 id，仅用于观测溯源")
    proposal_title: str | None = Field(default=None, description="来源方案标题")


class RestoreRequest(BaseModel):
    kind: RestoreKind = Field(default="scene", description="复原类型，决定 Planner 的解析分支")

    # ── 场景复原 ────────────────────────────────────────────────────────────
    identity: str | None = Field(default=None, description="人物身份，如「大祭司」")
    scene: str | None = Field(default=None, description="场景地点")
    item: str | None = Field(default=None, description="核心文物")
    style: str | None = Field(default=None, description="画面风格（会解析到风格档案）")

    # ── 人物还原 ────────────────────────────────────────────────────────────
    gender: str | None = None
    rank: str | None = None
    era: str | None = None
    expression: str | None = None
    detail: str | None = Field(default=None, description="景别，如「全身像」")

    # ── 文物修复 ────────────────────────────────────────────────────────────
    artifact: str | None = None
    method: str | None = Field(default=None, description="修复方式")
    mode: str | None = Field(default=None, description="输出模式")
    damage: str | None = Field(default=None, description="损毁率等元信息，仅用于文案与记录")

    # ── 风格迁移 ────────────────────────────────────────────────────────────
    style_preset: str | None = None
    strength: int | None = Field(default=None, ge=10, le=100)

    # ── 通用 ────────────────────────────────────────────────────────────────
    note: str | None = Field(default=None, max_length=500, description="用户补充说明")
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    reference_images: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="参考图（http(s) URL 或 data URI）。提供时会触发 VLM 参考图理解",
    )
    max_revisions: int | None = Field(default=None, ge=0, le=4)
    fast: bool = Field(
        default=True,
        description=(
            "快速模式（默认）：跳过 planner 的 LLM 增强与质检的 VLM 裁判/回炉，"
            "首图更快。设为 false 走完整精修链路（LLM 规划 + VLM 质检 + 回炉）。"
        ),
    )
    model_image: str | None = Field(
        default=None,
        description=(
            "覆盖出图模型（如 z-image-turbo / qwen-image-plus / qwen-image-2.0-pro）。"
            "不传则用后端 MODEL_IMAGE 配置。"
        ),
    )
    # 这两个 ID 会落盘（trace 文件名 = f"{task_id}.jsonl"），因此必须限定字符集：
    # 否则 task_id="../../../x" 能把文件写到 var/traces 之外（路径穿越）。
    task_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="任务 ID。只允许字母、数字、下划线、短横线",
    )
    session_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="来源对话会话，用于把出图与对话关联起来",
    )
    prompt_override: PromptOverride | None = Field(
        default=None,
        description="用户选定的提示词方案。提供时 Planner 不再改写 prompt，只出图",
    )

    @field_validator("reference_images")
    @classmethod
    def _check_references(cls, value: list[str]) -> list[str]:
        for item in value:
            if not item.startswith(("http://", "https://", "data:image/")):
                raise ValueError("参考图必须是 http(s) URL 或 data:image/ 开头的 Data URI")
        return value

    def to_agent_request(self) -> dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        payload.setdefault("kind", "scene")
        return payload


class RestoreResponse(BaseModel):
    ok: bool
    task_id: str
    engine: str
    duration_ms: float
    image_url: str | None = None
    image_provider: str | None = None
    image_degraded: bool = False
    qa: dict[str, Any] = Field(default_factory=dict)
    revisions: int = 0
    goal: str | None = None
    # 字段名避开 BaseModel.copy；序列化时仍输出为 "copy"，前端无需感知
    copy_payload: dict[str, Any] = Field(default_factory=dict, serialization_alias="copy")
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    plan: dict[str, Any] = Field(default_factory=dict)
    # 出图细节（实际下发的 prompt / provider / 轮次）——
    # 同步接口要与 SSE 的 done 帧具备同等可调试性，否则线上排查只能靠猜
    image: dict[str, Any] = Field(default_factory=dict)
    revision_history: list[dict[str, Any]] = Field(default_factory=list)
    retrieval: dict[str, Any] = Field(default_factory=dict)
    degraded_components: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    state: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str
    engine: dict[str, Any]
    providers: dict[str, Any]
    retrieval: dict[str, Any]
    models_configured: bool
    style_profiles: int
    version: str
    network: dict[str, Any] = Field(default_factory=dict)
    # 存储三层（PostgreSQL / Redis / pgvector）的**实际**档位与失败原因。
    # 与 providers 一样，这里输出的必须是「真的在用哪一个」，
    # 而不是「配置里写了哪一个」——否则健康检查会变成安慰剂。
    storage: dict[str, Any] = Field(default_factory=dict)


class EvalRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=60)
    engine: str | None = Field(default=None, description="覆盖 ENGINE_BACKEND，用于 A/B 对比")


class EvalResponse(BaseModel):
    summary: dict[str, Any]
    cases: list[dict[str, Any]]
    report_path: str | None = None


class AskRequest(BaseModel):
    """领域问答请求。"""

    question: str = Field(min_length=2, max_length=500, description="用户问题")
    top_n: int | None = Field(default=None, ge=1, le=20, description="覆盖 QA_TOP_N")


class Citation(BaseModel):
    """一条引用。

    **展示内容全部来自语料条目，不经过模型** —— 模型只能决定「用哪一条」，
    决定不了「这一条长什么样」。出处、原文、链接、加工说明都必须是语料原值，
    否则引用就不再是引用，只是模型说的一句话。
    """

    index: int
    doc_id: str
    title: str
    source: str
    source_type: str
    authority: float
    license: str
    url: str
    locator: str
    quote: str
    note: str = Field(default="", description="加工说明，如「古籍校勘注已剥离」")
    score: float
    channels: list[str] = Field(default_factory=list)


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    confidence: str = "low"
    # 检索不到可依据的记载时为 true。这时 answer 是「不能回答」的说明，
    # 而不是一个听起来合理的答案 —— 客户端**必须**据此区分，
    # 否则「明确拒绝」会被渲染成「回答了」。
    refused: bool = False
    evidence_count: int = 0
    model: str = ""
    degraded: bool = False
    retrieval_mode: str = "llm"
