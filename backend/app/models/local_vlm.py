"""本地 Qwen2.5-VL + QLoRA adapter 推理后端。

它让 `vlm` 与 `judge` 两个角色可以**不经过任何外部 API** 完成推理：
- 按图计费变成固定 GPU 成本，批量评估时成本可控；
- 数据不出内网（文物影像素材的合规要求）；
- 可以用三星堆专有语料继续微调（见 `training/`）。

设计上刻意做成**惰性单例 + 线程卸载**：
模型加载要几十秒且占数 GB 显存，不能在进程启动时无条件加载
（无 GPU 的机器也必须能起服务）；推理是同步阻塞的，必须 `to_thread`
否则会卡死整个事件循环、把 SSE 流全部堵住。
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.models.llm import ChatResult, Message, _loads_lenient, _validate  # noqa: PLC2701

logger = get_logger("app.models.local_vlm")


class LocalQwenVL:
    """惰性加载的本地视觉语言模型。"""

    _instance: "LocalQwenVL | None" = None
    _lock = asyncio.Lock()

    def __init__(self, *, base_model: str, adapter_path: str) -> None:
        self.base_model = base_model
        self.adapter_path = adapter_path
        self._model = None
        self._processor = None
        self._load_error: str | None = None
        self._loading = False

    # ── 单例 ────────────────────────────────────────────────────────────────
    @classmethod
    async def instance(cls) -> "LocalQwenVL":
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(
                        base_model=settings.lora_base_model,
                        adapter_path=settings.lora_adapter_path,
                    )
        return cls._instance

    # ── 可用性 ──────────────────────────────────────────────────────────────
    def _deps_ok(self) -> tuple[bool, str]:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
            import peft  # noqa: F401
        except ImportError as exc:
            return False, f"缺少训练依赖: {exc}"
        if not self.adapter_path:
            return False, "未配置 LORA_ADAPTER_PATH"
        if not Path(self.adapter_path).exists():
            return False, f"adapter 路径不存在: {self.adapter_path}"
        return True, ""

    async def available(self) -> bool:
        if not settings.lora_enabled:
            return False
        ok, reason = self._deps_ok()
        if not ok:
            logger.info("本地 VLM 不可用: %s", reason)
        return ok

    # ── 推理 ────────────────────────────────────────────────────────────────
    async def chat_json(
        self,
        messages: list[Message],
        *,
        required_keys: tuple[str, ...] = (),
        max_new_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> tuple[dict[str, Any], ChatResult]:
        result = await self.chat(
            messages, max_new_tokens=max_new_tokens, temperature=temperature
        )
        payload = _validate(_loads_lenient(result.text), required_keys)
        return payload, result

    async def chat(
        self, messages: list[Message], *, max_new_tokens: int = 1024, temperature: float = 0.0
    ) -> ChatResult:
        if not await self.available():
            raise ProviderUnavailable("本地 QLoRA 模型不可用", model=self.base_model)
        return await asyncio.to_thread(
            self._generate_sync, messages, max_new_tokens, temperature
        )

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if self._load_error:
            raise ProviderUnavailable(self._load_error, model=self.base_model)
        if self._loading:
            raise ProviderUnavailable("模型正在加载中，请稍后重试", model=self.base_model)

        self._loading = True
        started = time.perf_counter()
        try:
            import torch
            from peft import PeftModel
            from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

            logger.info("正在加载本地 VLM: %s + adapter %s", self.base_model, self.adapter_path)
            compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16

            processor = AutoProcessor.from_pretrained(
                self.adapter_path, min_pixels=256 * 28 * 28, max_pixels=1280 * 28 * 28
            )
            base = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                self.base_model,
                torch_dtype=compute_dtype,
                device_map="auto",
                attn_implementation="sdpa",
            )
            model = PeftModel.from_pretrained(base, self.adapter_path)
            model.eval()

            self._processor = processor
            self._model = model
            logger.info("本地 VLM 加载完成，耗时 %.1fs", time.perf_counter() - started)
        except Exception as exc:  # noqa: BLE001
            self._load_error = f"本地 VLM 加载失败: {type(exc).__name__}: {exc}"
            logger.exception("本地 VLM 加载失败，后续将回退到 API 通道")
            raise ProviderUnavailable(self._load_error, model=self.base_model) from exc
        finally:
            self._loading = False

    def _generate_sync(
        self, messages: list[Message], max_new_tokens: int, temperature: float
    ) -> ChatResult:
        import torch

        self._ensure_loaded()
        assert self._model is not None and self._processor is not None

        started = time.perf_counter()
        qwen_messages, images = _to_qwen_messages(messages)

        text = self._processor.apply_chat_template(
            qwen_messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self._processor(
            text=[text], images=images or None, return_tensors="pt", padding=True
        ).to(self._model.device)

        with torch.inference_mode():
            generated = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=temperature > 0,
                temperature=temperature if temperature > 0 else None,
                top_p=0.9 if temperature > 0 else None,
            )

        trimmed = generated[:, inputs["input_ids"].shape[1] :]
        output = self._processor.batch_decode(trimmed, skip_special_tokens=True)[0]
        latency_ms = (time.perf_counter() - started) * 1000

        del inputs, generated
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        return ChatResult(
            text=output,
            model=f"{self.base_model}+lora",
            latency_ms=latency_ms,
            usage={},
            finish_reason="stop",
        )

    def release(self) -> None:
        self._model = None
        self._processor = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:  # pragma: no cover
            pass


def _to_qwen_messages(messages: list[Message]) -> tuple[list[dict[str, Any]], list[Any]]:
    """把 OpenAI 风格内容块转成 Qwen 的 messages 格式，并把图像解码成 PIL。"""
    from PIL import Image

    converted: list[dict[str, Any]] = []
    images: list[Any] = []

    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            converted.append({"role": message.get("role", "user"), "content": [{"type": "text", "text": content}]})
            continue

        blocks: list[dict[str, Any]] = []
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                blocks.append({"type": "text", "text": str(block.get("text") or "")})
            elif block.get("type") == "image_url":
                url = (block.get("image_url") or {}).get("url", "")
                image = _load_image(url, Image)
                if image is not None:
                    images.append(image)
                    blocks.append({"type": "image", "image": image})
        converted.append({"role": message.get("role", "user"), "content": blocks})

    return converted, images


def _load_image(url: str, Image):
    try:
        if url.startswith("data:"):
            _, _, payload = url.partition(",")
            return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")

        if url.startswith(("/media/", "media/")):
            name = url.split("/")[-1].split("?")[0]
            candidate = settings.output_path / name
            if candidate.is_file():
                return Image.open(candidate).convert("RGB")
            return None

        if url.startswith(("http://", "https://")):
            import httpx

            with httpx.Client(timeout=20.0) as client:
                response = client.get(url)
                response.raise_for_status()
                return Image.open(io.BytesIO(response.content)).convert("RGB")

        path = Path(url)
        if path.is_file():
            return Image.open(path).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        logger.warning("本地 VLM 读取图像失败 %s: %s", url[:80], exc)
    return None


async def local_vlm_available() -> bool:
    if not settings.lora_enabled:
        return False
    return await (await LocalQwenVL.instance()).available()
