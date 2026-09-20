"""DashScope（阿里云百炼）图像生成 provider —— 本项目的主出图通道。

为什么走 `multimodal-generation` 同步端点：
- 同步返回，链路短，不需要轮询 task_id（少一个失败面、少一份状态）；
- 请求体是 messages 结构，天然支持**多模态参考图** —— 把出土照片作为参考图塞进去，
  让模型在「形制对齐」上有据可依。这是文物复原区别于普通文生图的关键。

一个曾经存在、且很难察觉的缺陷（已修）：
`request.negative_prompt` **从来没有被下发过**。项目里辛苦维护的时代错配负向词
（铁器 / 青花瓷 / 汉字铭文 / 镜面反光 …）全部只停留在 PromptOverride 与界面里，
真正决定出图的请求体里一个都没有。现在按模型能力正确下发，
并把实际下发的负向词长度记入 `raw` 以便审计。
"""

from __future__ import annotations

import time
from typing import Any

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.http import make_async_client
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.imagegen.base import ImageRequest, ImageResult

logger = get_logger("app.imagegen.dashscope")

# 有些模型不接受 negative_prompt（例如 wan2.7-image-pro 系列），传了会直接报 400。
# 命中这些特征时自动降级为「把禁止项写进正向提示词」。
_NO_NEGATIVE_PROMPT_HINTS = ("wan2.7",)


class DashScopeImageProvider:
    name = "dashscope"

    def __init__(
        self,
        *,
        api_key: str,
        endpoint_url: str,
        model: str,
        timeout: float = 300.0,
        prompt_extend: bool = False,
        watermark: bool = False,
    ) -> None:
        self.model = model
        self.prompt_extend = prompt_extend
        self.watermark = watermark
        self._endpoint_url = endpoint_url
        self._accepts_negative_prompt = not any(
            hint in model for hint in _NO_NEGATIVE_PROMPT_HINTS
        )
        # 出图是公网长耗时请求，且百炼在公网，需要走系统/环境代理。
        self._client = make_async_client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            trust_env=True,
        )

    async def available(self) -> bool:
        return settings.has_dashscope_key

    # ── 请求组装 ────────────────────────────────────────────────────────────
    def build_payload(self, request: ImageRequest) -> dict[str, Any]:
        """组装请求体。抽成独立方法，便于单测直接断言「到底发了什么」。"""
        prompt = request.prompt
        negative = request.negative_prompt.strip()

        if negative and not self._accepts_negative_prompt:
            # 该模型不支持 negative_prompt：把禁止项并入正向提示词。
            # 这有有损（模型对正向否定句的服从度低于负向词），
            # 但比静默丢弃整份红线要好得多。
            prompt = f"{prompt} Avoid the following: {negative}."

        content: list[dict[str, str]] = [{"text": prompt}]
        # 参考图放在文本之后：部分模型按 content 顺序解析，文本先到更稳
        for reference in request.reference_images[:3]:
            content.append({"image": reference})

        parameters: dict[str, Any] = {
            "n": 1,
            "size": request.size,
            # 默认关闭：提示词由我们拼装且承诺逐字使用，不允许模型改写
            "prompt_extend": bool(request.prompt_extend or self.prompt_extend),
            "watermark": self.watermark,
        }
        if negative and self._accepts_negative_prompt:
            parameters["negative_prompt"] = negative
        if request.seed is not None:
            parameters["seed"] = request.seed

        return {
            "model": request.model or self.model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": parameters,
        }

    # ── 调用 ────────────────────────────────────────────────────────────────
    async def generate(self, request: ImageRequest) -> ImageResult:
        payload = self.build_payload(request)
        started = time.perf_counter()

        try:
            response = await self._client.post(self._endpoint_url, json=payload)
        except Exception as exc:  # noqa: BLE001 - 网络层异常统一转 ProviderError
            raise ProviderError(f"DashScope 图像请求失败: {exc}", model=self.model) from exc

        latency_ms = (time.perf_counter() - started) * 1000

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"DashScope 返回非 JSON（HTTP {response.status_code}）: {response.text[:300]}",
                status=response.status_code,
                model=self.model,
            ) from exc

        if response.status_code >= 400:
            code = str(data.get("code") or "")
            message = str(data.get("message") or code or "unknown")
            # 内容审核拦截是一类独立错误：它需要的是改写提示词，而不是重试。
            if "DataInspectionFailed" in code or "data inspection" in message.lower():
                raise ProviderError(
                    f"提示词或负向词被内容安全审核拦截：{message}",
                    status=response.status_code,
                    model=self.model,
                )
            raise ProviderError(message, status=response.status_code, model=self.model)

        image_url = self._extract_image(data)
        if not image_url:
            raise ProviderError(f"DashScope 未返回图片: {str(data)[:300]}", model=self.model)

        metrics.observe("sxd_image_latency_ms", latency_ms, provider=self.name)
        metrics.inc("sxd_image_calls", provider=self.name, model=self.model)

        usage = data.get("usage") or {}
        parameters = payload["parameters"]
        return ImageResult(
            image_url=image_url,
            provider=self.name,
            latency_ms=latency_ms,
            seed=request.seed,
            raw={
                "model": request.model or self.model,
                "request_id": data.get("request_id"),
                "prompt_extend": parameters["prompt_extend"],
                "negative_prompt_sent": bool(parameters.get("negative_prompt")),
                "negative_prompt_chars": len(str(parameters.get("negative_prompt") or "")),
                "size": parameters["size"],
                "reference_count": len(request.reference_images[:3]),
                "image_count": usage.get("image_count"),
            },
        )

    @staticmethod
    def _extract_image(data: dict[str, Any]) -> str | None:
        """从两种响应形状里取图：multimodal-generation 与 text2image。"""
        output = data.get("output") or {}
        for choice in output.get("choices") or []:
            content = (choice.get("message") or {}).get("content") or []
            for item in content:
                if isinstance(item, dict) and item.get("image"):
                    return str(item["image"])
        # 兼容 text2image / image-synthesis 风格返回
        for item in output.get("results") or []:
            if isinstance(item, dict) and item.get("url"):
                return str(item["url"])
        return None

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001, S110
            pass
