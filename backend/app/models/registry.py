"""模型注册表 —— 多智能体的「能力路由层」。

为什么需要它
------------
多 Agent 系统里最容易被面试官追问的一点是：**为什么这个节点用这个模型？**
把选型硬编码在节点里，会导致（a）无法解释取舍（b）无法按成本/延迟做灰度。
因此这里把「角色 → 模型 → 降级链」抽成一张显式声明的表，
节点只表达意图（我要一个会看图的裁判），不关心具体是 Qwen 还是 GLM。

降级语义
--------
每个角色声明 `primary` 与 `fallbacks`。调用方用 `call()` 时按序尝试，
只有**全部**不可用（无 Key / 服务不可达）才抛 ProviderUnavailable，
由上层决定是走规则兜底还是向用户报错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.models.llm import ChatClient, ChatResult, Message

logger = get_logger("app.models.registry")

Modality = Literal["text", "vision"]


@dataclass(frozen=True, slots=True)
class RoleSpec:
    role: str
    title: str
    models: tuple[str, ...]
    modality: Modality
    rationale: str
    monthly_cost_hint: str = ""
    latency_hint: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def primary(self) -> str:
        return self.models[0]


def _build_role_specs() -> dict[str, RoleSpec]:
    """角色定义表。

    选型原则：**推理强度随「错误代价」递增，成本随「调用频次」递减。**
    - Planner / Supervisor 决定全局走向，错一次整条链路白跑 → 上最强闭源档；
    - 检索摘要 / 文案改写这类高频、可重试、错一次代价小的 → 中档模型即可；
    - 质检裁判必须与出图模型**异构**（不同家族/不同输入模态），
      否则同一个模型的偏好会互相污染，Self-Correction 会退化成自我确认。
    """
    return {
        "supervisor": RoleSpec(
            role="supervisor",
            title="调度中枢（Supervisor）",
            models=(settings.model_supervisor, settings.model_fallback_chat),
            modality="text",
            rationale=(
                "只做「选下一个 Worker」这一个决策，但决策错误会导致整条链路空转。"
                "要求强约束遵循与稳定 JSON 输出，故取同代最强档；"
                "该节点输入输出都很短，成本占比可控。"
            ),
            monthly_cost_hint="约 ¥0.02/次（输入 ~800tok / 输出 ~80tok）",
            latency_hint="首包 400~900ms",
            tags=("结构化输出", "低 token", "强遵循"),
        ),
        "intent": RoleSpec(
            role="intent",
            title="意图解析（对话关键路径）",
            models=(settings.model_intent, settings.model_fallback_chat),
            modality="text",
            rationale=(
                "**用户在等这一次调用**，所以延迟优先于推理强度。\n\n"
                "它做的是「受限抽取」而不是开放创作：候选词表由规则层预筛后随请求下发，"
                "模型只负责（1）判谁是画面主角，（2）写一句 ≤30 词的主体动作句。"
                "材质、形制、尺寸这些会被写进提示词的事实全部来自词典，它碰不到 —— "
                "所以不需要强模型，也就不会因模型小而编造史实。\n\n"
                "不要改成最强档。它原先借的是 planner 的 qwen3-max，"
                "让最慢的模型干最轻的活；抽偏了还有 `rule_intent` 兜底，"
                "而每轮多等一两秒是实打实的。"
            ),
            monthly_cost_hint="约 ¥0.002/次（输入 ~450tok / 输出 ~120tok）",
            latency_hint="首包 200~500ms",
            tags=("关键路径", "短输出", "可降级"),
        ),
        "planner": RoleSpec(
            role="planner",
            title="规划 Agent（Planner）",
            models=(settings.model_planner, settings.model_fallback_chat),
            modality="text",
            rationale=(
                "把「大祭司站在神树祭坛前」这种自然语言目标，翻译成可执行的检索 query、"
                "图像 prompt、风格约束与验收标准。这是全链路里唯一需要**跨域常识融合**"
                "（考古/美术/摄影）的节点，值得用最强模型。"
            ),
            monthly_cost_hint="约 ¥0.05/次（输入 ~1500tok / 输出 ~700tok）",
            latency_hint="1.5~3s",
            tags=("任务拆解", "跨域知识", "长输出"),
        ),
        "retrieval": RoleSpec(
            role="retrieval",
            title="史料检索 Worker",
            models=(settings.model_retrieval, settings.model_fallback_chat),
            modality="text",
            rationale=(
                "对召回的史料片段做压缩摘要而非重新生成知识，属于**受限阅读理解**任务，"
                "中档模型即可达到与最强档持平的忠实度，成本降低约 70%。"
            ),
            monthly_cost_hint="约 ¥0.01/次（输入 ~3000tok / 输出 ~300tok）",
            latency_hint="1~2s",
            tags=("RAG", "摘要", "高频"),
        ),
        "qa": RoleSpec(
            role="qa",
            title="领域问答（带引用）",
            models=(settings.model_qa, settings.model_fallback_chat),
            modality="text",
            rationale=(
                "与 retrieval 同属**受限阅读理解**：只能使用检索到的史料片段作答，"
                "不得引入片段之外的事实，因此不需要最强档的创作/推理能力，中档即可；"
                "省下的成本花在检索链路（embedding + rerank）上更划算。\n\n"
                "**但忠实度要求比 retrieval 更高**：它输出的是直接展示给用户的答案，"
                "每一句事实都要能回带 doc_id。所以答案是**先由代码校验再返回** —— "
                "模型给出的引用若不在检索结果里，会被丢弃并记进 caveats，"
                "而不是靠 prompt 自觉。"
            ),
            monthly_cost_hint="约 ¥0.015/次（输入 ~2500tok / 输出 ~400tok）",
            latency_hint="1.5~3s",
            tags=("RAG", "引用回带", "用户可见", "忠实度"),
        ),
        "copywriter": RoleSpec(
            role="copywriter",
            title="科普文案 Worker",
            models=(settings.model_copywriter, settings.model_retrieval, settings.model_fallback_chat),
            modality="text",
            rationale=(
                "B端用户可见的最终产物，且需要「有史实依据但不迁就术语」的二次创作能力。"
                "文案一旦出现「三星堆出土司母戊鼎」这类硬伤，整条链路的可信度归零，"
                "因此同样上最强档。支持流式输出以改善体感。"
            ),
            monthly_cost_hint="约 ¥0.06/次（输入 ~2500tok / 输出 ~800tok，流式）",
            latency_hint="首字 300~600ms",
            tags=("流式", "用户可见", "史实约束"),
        ),
        "judge": RoleSpec(
            role="judge",
            title="质检 Agent（Judge）",
            models=(settings.model_judge, settings.model_fallback_vlm),
            modality="vision",
            rationale=(
                "**必须与出图链路异构**：出图侧是 Diffusion（SDXL+LoRA 或 z-image），"
                "质检侧必须是判别式 VLM，否则等于让考生自己判卷。"
                "VLM 裁判负责风格一致性与时代错配（anachronism）判定，"
                "与本地色彩/纹理客观指标做加权融合，避免 VLM 幻觉独裁。"
            ),
            monthly_cost_hint="约 ¥0.12/次（单图 ~1300 image tok + 文本）",
            latency_hint="2~4s",
            tags=("多模态", "判别式", "异构校验"),
        ),
        "vlm": RoleSpec(
            role="vlm",
            title="文物视觉理解（VLM）",
            models=(settings.model_vlm, settings.model_fallback_vlm),
            modality="vision",
            rationale=(
                "读取参考图/出土照片，抽取形制、纹饰、材质、残缺位置，"
                "作为图像 prompt 与修复策略的依据；同时承担「修复后形制走样」的核查。"
                "生产路径可替换为本地 Qwen2.5-VL-7B + QLoRA adapter（见 training/），"
                "以获得对三星堆专有纹饰更高的辨识粒度并规避按图计费。"
            ),
            monthly_cost_hint="约 ¥0.10/次；切本地 QLoRA 后为固定 GPU 成本",
            latency_hint="API 2~3s / 本地 4bit 7B 约 1.5~2.5s",
            tags=("多模态", "可微调", "QLoRA"),
        ),
    }


class ModelRegistry:
    """按角色分发客户端，并内建降级链与用量记账。"""

    def __init__(self) -> None:
        self._roles = _build_role_specs()
        self._clients: dict[str, ChatClient] = {}

    # ── 能力查询 ────────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return settings.has_dashscope_key

    def spec(self, role: str) -> RoleSpec:
        if role not in self._roles:
            raise KeyError(f"未注册的模型角色: {role}")
        return self._roles[role]

    def client(self, role: str) -> ChatClient:
        """取主模型客户端。无 Key 时抛 ProviderUnavailable，由调用方降级。"""
        spec = self.spec(role)
        return self.client_for_model(spec.primary)

    def client_for_model(self, model: str) -> ChatClient:
        if not self.enabled:
            raise ProviderUnavailable("未配置 DASHSCOPE_API_KEY", model=model)
        cache_key = model
        if cache_key not in self._clients:
            self._clients[cache_key] = ChatClient(
                api_key=settings.dashscope_key_plain,
                base_url=settings.openai_compatible_base,
                model=model,
            )
        return self._clients[cache_key]

    # ── 带降级的调用 ────────────────────────────────────────────────────────
    async def call(
        self,
        role: str,
        messages: list[Message],
        *,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        json_mode: bool = False,
        tag: str | None = None,
    ) -> ChatResult:
        return (await self._call_chain(
            role,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            required_keys=(),
            tag=tag or role,
        ))[0]

    async def call_json(
        self,
        role: str,
        messages: list[Message],
        *,
        required_keys: tuple[str, ...] = (),
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tag: str | None = None,
    ) -> tuple[dict[str, Any], ChatResult]:
        return await self._call_chain(
            role,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            required_keys=required_keys,
            tag=tag or f"{role}:json",
        )

    async def _call_chain(
        self,
        role: str,
        messages: list[Message],
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        required_keys: tuple[str, ...],
        tag: str,
    ) -> tuple[dict[str, Any], ChatResult]:
        spec = self.spec(role)
        last_error: Exception | None = None

        # 视觉类角色优先走本地 QLoRA（成本固定、数据不出内网、可用三星堆专有语料微调）。
        # 本地不可用时静默回落到 API 通道，对上游 Agent 完全透明。
        if role in {"vlm", "judge"} and settings.lora_enabled:
            local = await self._try_local_vlm(
                role, messages, json_mode=json_mode, required_keys=required_keys,
                temperature=temperature, max_tokens=max_tokens,
            )
            if local is not None:
                return local

        for index, model in enumerate(spec.models):
            try:
                client = self.client_for_model(model)
                if json_mode:
                    payload, result = await client.chat_json(
                        messages, required_keys=required_keys, temperature=temperature,
                        max_tokens=max_tokens, tag=tag,
                    )
                else:
                    result = await client.chat(
                        messages, temperature=temperature, max_tokens=max_tokens, tag=tag
                    )
                    payload = {}
                if index > 0:
                    metrics.record_degradation(
                        component=role, reason="primary_failed", fallback=model
                    )
                return payload, ChatResult(
                    text=result.text,
                    model=result.model,
                    latency_ms=result.latency_ms,
                    usage=result.usage,
                    finish_reason=result.finish_reason,
                    degraded=index > 0,
                )
            except ProviderUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - 逐个模型尝试降级
                last_error = exc
                logger.warning(
                    "role %s 主模型 %s 调用失败，尝试降级: %s", role, model, exc
                )
                continue

        raise ProviderUnavailable(
            f"角色 {role} 的所有候选模型均不可用: {last_error}",
            role=role,
            candidates=list(spec.models),
        )

    async def _try_local_vlm(
        self,
        role: str,
        messages: list[Message],
        *,
        json_mode: bool,
        required_keys: tuple[str, ...],
        temperature: float,
        max_tokens: int,
    ) -> tuple[dict[str, Any], ChatResult] | None:
        """尝试本地 QLoRA 通道；任何失败都返回 None 让调用方继续走 API 链。"""
        try:
            from app.models.local_vlm import LocalQwenVL

            runner = await LocalQwenVL.instance()
            if not await runner.available():
                return None

            if json_mode:
                payload, result = await runner.chat_json(
                    messages, required_keys=required_keys, max_new_tokens=max_tokens,
                    temperature=temperature,
                )
            else:
                result = await runner.chat(messages, max_new_tokens=max_tokens, temperature=temperature)
                payload = {}

            metrics.inc("sxd_local_vlm_calls", role=role)
            logger.info("角色 %s 使用本地 QLoRA 模型（%.0fms）", role, result.latency_ms)
            return payload, result
        except Exception as exc:  # noqa: BLE001 - 本地失败必须能自动回落
            logger.warning("本地 QLoRA 通道失败，回落到 API：%s", exc)
            metrics.record_degradation(component=role, reason=str(exc)[:200], fallback="remote_api")
            return None

    # ── 对外描述（/api/models）──────────────────────────────────────────────
    def matrix(self) -> dict[str, Any]:
        return {
            "provider": "dashscope",
            "configured": self.enabled,
            "base_url": settings.openai_compatible_base,
            "roles": [
                {
                    "role": spec.role,
                    "title": spec.title,
                    "primary": spec.primary,
                    "fallbacks": list(spec.models[1:]),
                    "modality": spec.modality,
                    "rationale": spec.rationale,
                    "cost_hint": spec.monthly_cost_hint,
                    "latency_hint": spec.latency_hint,
                    "tags": list(spec.tags),
                }
                for spec in self._roles.values()
            ],
            "image_generation": {
                "primary": settings.model_image,
                "endpoint": settings.image_endpoint_url,
                "fallback": "free-third-party（免鉴权，仅用于零配置体验）→ local-placeholder",
                "prompt_extend": settings.image_prompt_extend,
                "watermark": settings.image_watermark,
            },
            "local_finetune": {
                "base": settings.lora_base_model,
                "adapter": settings.lora_adapter_path or None,
                "enabled": settings.lora_enabled,
            },
            "embedding": settings.model_embedding,
            "rerank": settings.model_rerank,
        }

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()


registry = ModelRegistry()
