"""免鉴权出图通道（第三方公开服务）。

**它为什么存在**
----------------
本项目最初的无 Key 降级是「本地 Pillow 占位图」，结果是用户看到的画面
与三星堆毫无关系（一个同心圆示意图）。这在演示与自测场景下体验极差，
而且会让人误判「生图逻辑坏了」。

这个通道提供一条**无需 API Key、无需 GPU** 的真实出图路径，作为
DashScope 不可用时的兜底。

**它的代价（必须如实告知）**
----------------------------
- 由第三方公开服务提供，**不保证稳定性与可用性**，随时可能限流或下线；
- 产出带第三方水印；
- 内容审核策略不受本项目控制；
- 风格可控性远低于本地 LoRA 或 DashScope。

因此它的优先级**低于** DashScope，只高于本地占位图；
每次使用都会写入 trace 与指标（`sxd_image_calls{provider="free-third-party"}`），
前端也会显示「第三方通道」徽标。生产部署应通过 `FREEIMAGE_ENABLED=false` 关闭。
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.http import make_async_client
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.imagegen.base import (
    ImageRequest,
    ImageResult,
    archive_image,
    fetch_image_bytes,
    guess_extension,
)

logger = get_logger("app.imagegen.freeimage")

# 截断标记：用 ASCII，避免被某些网关的 URL 规范化弄乱
MARKER = " | ... | "


class FreeImageProvider:
    """基于公开 URL 模板的无鉴权出图通道。"""

    name = "free-third-party"

    def __init__(
        self,
        *,
        url_template: str,
        model: str,
        timeout: float = 120.0,
        attempts: int = 2,
        backoff_seconds: float = 16.0,
        token: str = "",
    ) -> None:
        self.url_template = url_template
        self.model = model
        self.timeout = timeout
        self._attempts = max(1, attempts)
        self._backoff_seconds = max(0.0, backoff_seconds)
        # 注册 token（免费）能提高配额并去水印；匿名档每 15 秒只允许 1 次请求
        self._token = token.strip()
        # 外部公网服务：需要走系统代理，因此 trust_env=True
        self._client = make_async_client(timeout=timeout, trust_env=True)

    @staticmethod
    def _is_retryable(response: httpx.Response) -> bool:
        """判断是否值得重试。

        除了 429/5xx 这类显式信号，还要处理一种坑：
        上游把限流包在 500 里返回，正文才是真正的 429，例如
          {"message":"Gen Sana request failed with 429: Per-user limit ... exceeded"}
        只看状态码会把它当成永久失败，直接降级到占位图。
        """
        if response.status_code in (408, 425, 429) or response.status_code >= 500:
            return True
        body = response.text[:500].lower()
        return "429" in body or "rate limit" in body or "too many requests" in body

    @staticmethod
    def fit_prompt(prompt: str, limit: int) -> tuple[str, bool]:
        """把过长的提示词压到 URL 预算内，返回 (结果, 是否发生过截断)。

        为什么不能简单地砍尾巴：
        实测（`scripts/probe_freeimage_tail.py`，2026-09）：上游对提示词有**有效窗口**。
        构造两个头部完全相同、只差结尾一句的提示词，732 字时两轮出图**逐像素完全相同**
        （0 个像素不同），说明尾部被上游静默丢弃。所以砍尾等于主动把尾部信息
        推出模型视野；超出预算时保留「头部 + 尾部」，至少让史料线索与 Final style anchor
        留在窗口内。

        但**保尾不能替代前置**：保尾只保证「这段文字还在 URL 里」，
        保证不了「模型看得见这段文字」。因此回炉的修正指令由
        `restoration_worker._compose_prompt` 放在整段提示词的**最前面**，
        而不是靠这里保尾 —— 详见那边的实测注释。
        """
        text = str(prompt or "")
        if limit <= 0 or len(text) <= limit:
            return text, False

        head_len = max(1, int(limit * 0.6))
        tail_len = max(1, limit - head_len - len(MARKER))
        if tail_len <= 1:
            return text[:limit], True
        return f"{text[:head_len]}{MARKER}{text[-tail_len:]}", True

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        """退避秒数：优先用上游的 Retry-After，否则用配置的基准值指数退避。"""
        if response is not None:
            raw = response.headers.get("retry-after", "")
            if raw:
                try:
                    return max(0.0, min(120.0, float(raw)))
                except ValueError:
                    pass
        return self._backoff_seconds * (2**attempt)

    async def available(self) -> bool:
        return settings.freeimage_enabled and bool(self.url_template)

    def build_url(self, request: ImageRequest) -> tuple[str, bool]:
        """返回 (请求 URL, 是否截断过提示词)。

        截断状态要向外暴露：它直接决定「回炉有没有真的改变输入」，
        是排查「回炉无效」时第一个要看的东西。
        """
        prompt, truncated = self.fit_prompt(request.prompt, settings.freeimage_max_prompt_chars)
        url = self.url_template.format(
            prompt=quote(prompt, safe=""),
            width=request.width,
            height=request.height,
            model=self.model,
            seed=request.seed if request.seed is not None else 0,
        )
        return url, truncated

    async def generate(self, request: ImageRequest) -> ImageResult:
        url, truncated = self.build_url(request)
        started = time.perf_counter()

        # 这个通道是共享配额的免费服务，实测会返回
        #   HTTP 500 + body {"message":"Gen Sana request failed with 429:
        #                    Per-user limit of 300 RPM exceeded ..."}
        # 也就是说「限流」被包在 500 里返回。不重试的话，一次瞬时限流就会让用户
        # 看到一张占位示意图 —— 那正是「图片跟三星堆无关」这个误解的来源。
        # 这类错误是暂时的，退避重试即可；只有重试耗尽才真正降级。
        response: httpx.Response | None = None
        last_error = ""
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
        for attempt in range(self._attempts):
            try:
                response = await self._client.get(url, follow_redirects=True, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"请求失败: {exc}"
                response = None
            else:
                if response.status_code < 400:
                    break
                last_error = f"返回 {response.status_code}: {response.text[:200]}"
                if not self._is_retryable(response):
                    raise ProviderError(last_error, status=response.status_code)

            if attempt < self._attempts - 1:
                # 上游给了 Retry-After 就听它的，比盲目指数退避更准
                await asyncio.sleep(self._retry_delay(response, attempt))

        if response is None:
            raise ProviderError(f"第三方出图通道请求失败: {last_error}")
        if response.status_code >= 400:
            raise ProviderError(last_error, status=response.status_code)

        data = response.content
        if data[:4] != b"\x89PNG" and data[:3] != b"\xff\xd8\xff":
            raise ProviderError(
                f"第三方通道返回的不是图像（前 32 字节: {data[:32]!r}）",
                content_type=response.headers.get("content-type"),
            )

        content_type = response.headers.get("content-type", "image/jpeg")
        if "jpeg" in content_type or "jpg" in content_type:
            suffix = ".jpg"
        elif "webp" in content_type:
            suffix = ".webp"
        else:
            suffix = ".png"

        latency_ms = (time.perf_counter() - started) * 1000
        task_id = f"free{int(started * 1000) % 10**10}"
        relative = archive_image(data, task_id=task_id, kind=request.kind, extension=suffix)

        metrics.observe("sxd_image_latency_ms", latency_ms, provider=self.name)
        metrics.inc("sxd_image_calls", provider=self.name, model=self.model)
        logger.info("第三方通道出图完成（%.0fms，%d 字节）", latency_ms, len(data))

        return ImageResult(
            image_url=relative or self._data_uri(data, content_type),
            provider=self.name,
            latency_ms=latency_ms,
            seed=request.seed,
            content_type=content_type,
            local_path=relative,
            degraded=True,  # 相对核定的生产通道而言仍是降级
            raw={
                "model": self.model,
                "url_host": url.split("/")[2] if "://" in url else "",
                "watermark": True,
                # 截断会直接影响回炉是否有效，必须可观测
                "prompt_truncated": truncated,
                "prompt_chars_sent": min(len(request.prompt), settings.freeimage_max_prompt_chars),
                "prompt_chars_total": len(request.prompt),
                "notice": "第三方免鉴权通道：不保证稳定性与内容审核，产出带水印",
            },
        )

    @staticmethod
    def _data_uri(data: bytes, content_type: str) -> str:
        import base64

        return f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = ["FreeImageProvider", "fetch_image_bytes", "guess_extension"]
