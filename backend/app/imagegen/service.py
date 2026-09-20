"""出图服务链：按「可控性」优先，失败逐级降级。

降级不是「碰运气」而是有明确优先级和可观测记录的：
每次降级都会写入 TraceRecorder 与 metrics（`sxd_degraded_total`），
因此在评估报告里可以精确回答「这批结果有多少是主通道出的、多少是降级的」。

本项目采用全代码（API）出图，不依赖 ComfyUI 之类的本地服务。
链路：DashScope（千问图像）→ 免鉴权第三方通道 → 本地占位图。
`chain()` 对外暴露**真实**构建出来的链路，供 trace / health / 日志使用 ——
不要在下游用写死的字符串描述链路，那会让 trace 说谎。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.errors import ProviderError, ProviderUnavailable
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.core.tracing import TraceRecorder
from app.imagegen.base import ImageProvider, ImageRequest, ImageResult

logger = get_logger("app.imagegen.service")


class ImageService:
    def __init__(self) -> None:
        self._providers: list[ImageProvider] = []
        self._built = False
        self._lock = asyncio.Lock()

    async def _ensure_built(self) -> None:
        if self._built:
            return
        async with self._lock:
            if self._built:
                return
            if settings.has_dashscope_key:
                from app.imagegen.dashscope import DashScopeImageProvider

                self._providers.append(
                    DashScopeImageProvider(
                        api_key=settings.dashscope_key_plain,
                        endpoint_url=settings.image_endpoint_url,
                        model=settings.model_image,
                        timeout=settings.image_timeout,
                        prompt_extend=settings.image_prompt_extend,
                        watermark=settings.image_watermark,
                    )
                )

            # 免鉴权第三方通道：无 Key 时的真实出图兜底。
            # 排在本地占位图之前，因为它至少产出的是模型生成图。
            if settings.freeimage_enabled and settings.freeimage_url_template:
                from app.imagegen.freeimage import FreeImageProvider

                self._providers.append(
                    FreeImageProvider(
                        url_template=settings.freeimage_url_template,
                        model=settings.freeimage_model,
                        timeout=settings.freeimage_timeout,
                        attempts=settings.freeimage_attempts,
                        backoff_seconds=settings.freeimage_backoff_seconds,
                        token=settings.freeimage_token,
                    )
                )

            from app.imagegen.local import LocalPlaceholderProvider

            self._providers.append(LocalPlaceholderProvider())
            self._built = True
            logger.info("出图 provider 链: %s", self.chain())

    def chain(self) -> list[str]:
        """已构建的真实链路（按尝试顺序）。

        trace / 日志 / 前端一律用这个，而不是各自写死一串名字 ——
        写死的字符串会在配置变化后继续说谎，是排查时最误导人的一类信息。
        """
        return [provider.name for provider in self._providers]

    async def generate(
        self, request: ImageRequest, tracer: TraceRecorder | None = None, node: str = "restoration"
    ) -> ImageResult:
        await self._ensure_built()
        errors: list[str] = []

        for index, provider in enumerate(self._providers):
            try:
                if not await provider.available():
                    errors.append(f"{provider.name}: 不可用")
                    continue
                result = await provider.generate(request)
                if index > 0:
                    reason = "; ".join(errors)[:300] or "primary_unavailable"
                    metrics.record_degradation(
                        component="image_provider",
                        reason=reason,
                        fallback=provider.name,
                    )
                    if tracer is not None:
                        tracer.degraded(node, reason=reason, fallback=provider.name)
                    result.degraded = True

                    # ── 降级原因必须透传到**结果**里，不能只写进 metrics ──────
                    # 事故复盘：这里原本只记 metrics 与 trace，结果对象带着
                    # provider 自己的 `error` 文案出去。而本地占位图的文案
                    # 当时写死成「未配置 DASHSCOPE_API_KEY」—— 那次真实原因是
                    # DashScope 限流，Key 一直是好的。用户只看到那句假话，
                    # 于是去翻 .env，方向完全错了。
                    #
                    # 判断依据：`result.error` 是用户唯一能看到的那份说明。
                    # metrics 与 trace 只在服务端，救不了正在界面前困惑的人。
                    result.raw["upstream_errors"] = list(errors)
                    result.raw["fallback_chain"] = [item.name for item in self._providers[:index]]
                    result.error = (
                        f"{result.error}（降级原因：{reason}）" if result.error else reason
                    )
                return result
            except (ProviderError, ProviderUnavailable) as exc:
                errors.append(f"{provider.name}: {exc}")
                logger.warning("出图 provider %s 失败: %s", provider.name, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - 任何异常都继续降级
                errors.append(f"{provider.name}: {type(exc).__name__}: {exc}")
                logger.exception("出图 provider %s 未预期异常", provider.name)
                continue

        raise ProviderUnavailable("所有出图通道均不可用", errors=errors)

    async def health(self) -> dict[str, Any]:
        await self._ensure_built()
        report: dict[str, Any] = {}
        for provider in self._providers:
            try:
                report[provider.name] = await provider.available()
            except Exception as exc:  # noqa: BLE001
                report[provider.name] = f"error: {exc}"
        return report

    async def aclose(self) -> None:
        for provider in self._providers:
            try:
                await provider.aclose()
            except Exception:  # noqa: BLE001, S110
                pass
        self._providers.clear()
        self._built = False


_service: ImageService | None = None


def get_image_service() -> ImageService:
    global _service
    if _service is None:
        _service = ImageService()
    return _service
