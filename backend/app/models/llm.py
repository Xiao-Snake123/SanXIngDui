"""LLM / VLM 客户端。

选型说明：直接对 DashScope 的 **OpenAI 兼容模式** 写一个薄客户端，而不引入
`openai` SDK 或 `langchain-openai`。原因有三：

1. 依赖面小 —— 少一层抽象，异常与重试策略完全可控，便于面试时把「为什么慢、
   为什么重试、为什么降级」讲清楚；
2. 兼容性广 —— 任何 OpenAI 兼容网关（vLLM / Ollama / DashScope / 自建代理）
   只需改 `DASHSCOPE_BASE_URL` 即可；
3. 可观测性 —— 每次调用的 model / token / 延迟都被显式记录进 TraceRecorder。

多模态（VLM）走同一套 `chat()`，只是 user content 从 str 变成内容块数组。
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.core.errors import ProviderError, ProviderUnavailable, StructuredOutputError
from app.core.logging import get_logger
from app.core.metrics import metrics

logger = get_logger("app.models.llm")

Role = Literal["system", "user", "assistant"]
Message = dict[str, Any]


@dataclass(slots=True)
class ChatResult:
    text: str
    model: str
    latency_ms: float
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None
    degraded: bool = False

    @property
    def total_tokens(self) -> int:
        return int(self.usage.get("total_tokens") or 0)


def image_part(url: str) -> dict[str, Any]:
    """构造 VLM 可消费的图像内容块。url 支持 http(s) 与 data:image/...;base64,。"""
    return {"type": "image_url", "image_url": {"url": url}}


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


class ChatClient:
    """OpenAI 兼容的对话客户端（chat + vision + 流式）。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 90.0,
        max_retries: int = 3,
        supports_json_mode: bool = True,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.supports_json_mode = supports_json_mode
        self._client: httpx.AsyncClient | None = None

    # ── 生命周期 ────────────────────────────────────────────────────────────
    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ── 非流式对话 ──────────────────────────────────────────────────────────
    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.3,
        top_p: float = 0.9,
        max_tokens: int = 2048,
        json_mode: bool = False,
        extra_body: dict[str, Any] | None = None,
        tag: str = "chat",
    ) -> ChatResult:
        if not self.api_key:
            raise ProviderUnavailable("未配置 DASHSCOPE_API_KEY", model=self.model, tag=tag)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode and self.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}
        if extra_body:
            payload.update(extra_body)

        started = time.perf_counter()
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self._ensure_client().post("/chat/completions", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                await self._sleep_backoff(attempt, f"transport:{type(exc).__name__}")
                continue

            if response.status_code == 400 and "response_format" in payload:
                # 少数模型不支持 json_object，摘掉重试一次
                payload.pop("response_format", None)
                self.supports_json_mode = False
                logger.info("json_mode unsupported by %s, retrying without it", self.model)
                continue

            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = ProviderError(
                    f"上游限流/故障: {response.status_code}", status=response.status_code
                )
                await self._sleep_backoff(attempt, f"http:{response.status_code}")
                continue

            if response.status_code >= 400:
                raise ProviderError(
                    _extract_error(response), status=response.status_code, model=self.model
                )

            data = response.json()
            latency_ms = (time.perf_counter() - started) * 1000
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content")
            text = _flatten_content(content)
            usage = data.get("usage") or {}

            metrics.observe("sxd_llm_latency_ms", latency_ms, model=self.model, tag=tag)
            metrics.inc("sxd_llm_calls", model=self.model)

            return ChatResult(
                text=text,
                model=data.get("model") or self.model,
                latency_ms=latency_ms,
                usage={
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "total_tokens": int(usage.get("total_tokens") or 0),
                },
                finish_reason=choice.get("finish_reason"),
            )

        metrics.inc("sxd_llm_retry_exhausted", model=self.model)
        raise ProviderError(
            f"调用 {self.model} 失败，已重试 {self.max_retries} 次: {last_error}",
            model=self.model,
        )

    # ── 结构化输出 ──────────────────────────────────────────────────────────
    async def chat_json(
        self,
        messages: Sequence[Message],
        *,
        required_keys: Sequence[str] = (),
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tag: str = "json",
        repair_attempts: int = 1,
    ) -> tuple[dict[str, Any], ChatResult]:
        """要求模型返回 JSON，并做「解析 → 校验 → 修复重试」三级兜底。"""
        result = await self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            tag=tag,
        )
        try:
            return _validate(_loads_lenient(result.text), required_keys), result
        except StructuredOutputError as exc:
            if repair_attempts <= 0:
                raise
            logger.warning("结构化输出校验失败，触发修复重试: %s", exc.message)
            repair_messages = [
                *messages,
                {"role": "assistant", "content": result.text[:4000]},
                {
                    "role": "user",
                    "content": (
                        "上面的输出不是合法 JSON 或缺少必需字段。"
                        f"错误：{exc.message}。"
                        f"必需字段：{list(required_keys)}。"
                        "请只输出一个严格合法的 JSON 对象，不要 markdown 代码块，不要任何解释。"
                    ),
                },
            ]
            return await self.chat_json(
                repair_messages,
                required_keys=required_keys,
                temperature=0.0,
                max_tokens=max_tokens,
                tag=f"{tag}:repair",
                repair_attempts=repair_attempts - 1,
            )

    # ── 流式对话 ────────────────────────────────────────────────────────────
    async def stream_chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.5,
        max_tokens: int = 2048,
        tag: str = "stream",
    ) -> AsyncIterator[str]:
        if not self.api_key:
            raise ProviderUnavailable("未配置 DASHSCOPE_API_KEY", model=self.model, tag=tag)

        payload = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        client = self._ensure_client()
        async with client.stream("POST", "/chat/completions", json=payload) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "ignore")
                raise ProviderError(f"流式调用失败: {body[:500]}", status=response.status_code)

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    parsed = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                delta = ((parsed.get("choices") or [{}])[0].get("delta") or {}).get("content")
                if delta:
                    yield _flatten_content(delta)

    # ── 内部 ────────────────────────────────────────────────────────────────
    async def _sleep_backoff(self, attempt: int, reason: str) -> None:
        delay = min(8.0, 0.6 * (2 ** (attempt - 1))) * (0.7 + random.random() * 0.6)
        logger.warning("llm retry", extra={"model": self.model, "attempt": attempt, "reason": reason})
        metrics.inc("sxd_llm_retry", model=self.model, reason=reason.split(":")[0])
        await asyncio.sleep(delay)


def _extract_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        error = body.get("error") or body
        message = error.get("message") if isinstance(error, dict) else str(error)
        return f"HTTP {response.status_code}: {message}"
    except Exception:  # noqa: BLE001
        return f"HTTP {response.status_code}: {response.text[:300]}"


def _flatten_content(content: Any) -> str:
    """兼容 string / [{type:text,text:...}] 两种返回形态。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return str(content)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _loads_lenient(raw: str) -> dict[str, Any]:
    """容忍 markdown 代码块、前后缀解释、尾随逗号的 JSON 解析。"""
    text = (raw or "").strip()
    if not text:
        raise StructuredOutputError("模型返回空内容")

    fence = _FENCE.search(text)
    if fence:
        text = fence.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise StructuredOutputError(f"JSON 解析失败: {exc}", raw=raw[:500]) from exc

    if not isinstance(parsed, dict):
        raise StructuredOutputError("顶层不是 JSON 对象", raw=raw[:200])
    return parsed


def _validate(payload: dict[str, Any], required_keys: Sequence[str]) -> dict[str, Any]:
    missing = [key for key in required_keys if key not in payload]
    if missing:
        raise StructuredOutputError(f"缺少必需字段 {missing}", got=list(payload)[:20])
    return payload
