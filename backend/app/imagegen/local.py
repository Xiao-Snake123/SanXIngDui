"""本地占位图 provider（Pillow 合成）。

存在的意义不是「假造一张好看的图」，而是**保证可观测性**：
出图通道全部不可用时，整条 Agent 流水线仍能跑完，
并把「本次没有真实出图」这个事实显式暴露在 trace、指标与结果里，
而不是让用户在界面上看到一个静默失败或一张来路不明的网图。

质检如何对待它
--------------
这张图带 `generative=False`，质检会**跳过**而不是打低分 ——
对一张程序画的示意图做「三星堆风格一致性」判定，结论在物理上没有意义。
（曾经它会被判不通过并被回炉两轮，最后以「回炉收益递减」收场，
那个结论是假的：它不是画得不好，是没有可判定的对象。）
"""

from __future__ import annotations

import hashlib
import io
import textwrap
import time
from functools import lru_cache
from pathlib import Path

from app.core.logging import get_logger
from app.imagegen.base import ImageRequest, ImageResult, archive_image
from app.rag.text import truncate

logger = get_logger("app.imagegen.local")

# 三星堆主题色板：锈绿 / 蓝铜 / 土褐 / 金 / 暗底
PALETTES = [
    (("#0B0C10", "#1F2833"), "#45A29E", "#D4AF37"),
    (("#0D1117", "#232B36"), "#5C8A7B", "#C9A227"),
    (("#101314", "#2A2E2B"), "#7A8B6F", "#B8860B"),
    (("#0A0E14", "#1B2733"), "#3E7C8C", "#E0B84C"),
]

FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhl.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


@lru_cache(maxsize=4)
def _load_font(size: int):
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:  # noqa: BLE001
                continue
    logger.info("未找到中文字体，占位图将只渲染 ASCII 摘要")
    return ImageFont.load_default()


class LocalPlaceholderProvider:
    name = "local-placeholder"

    async def available(self) -> bool:
        try:
            import PIL  # noqa: F401
        except ImportError:
            return False
        return True

    async def generate(self, request: ImageRequest) -> ImageResult:
        started = time.perf_counter()
        try:
            data = self._render(request)
        except ImportError as exc:
            raise RuntimeError("未安装 Pillow，无法生成本地占位图") from exc

        task_id = hashlib.blake2b(request.prompt.encode("utf-8"), digest_size=5).hexdigest()
        relative = archive_image(data, task_id=task_id, kind=request.kind)
        latency_ms = (time.perf_counter() - started) * 1000
        # 只说事实。真实失败原因由 ImageService 在降级时记在上一行日志里
        # （`出图 provider xxx 失败: ...`）—— 这里不要替上游猜。
        logger.warning("使用本地占位图：本次没有可用的出图通道")

        return ImageResult(
            image_url=relative or self._data_uri(data),
            provider=self.name,
            latency_ms=latency_ms,
            seed=request.seed,
            local_path=relative,
            degraded=True,
            # 这不是模型生成图。下游据此跳过风格质检 —— 对示意图做质检只会
            # 产生永远不达标、且回炉也不可能改善的假结论。
            generative=False,
            # ⚠️ 这里**只能**说自己知道的事实，不能猜原因。
            #
            # 曾经这里写的是「未配置 DASHSCOPE_API_KEY」。那句话是编的 ——
            # 本地 provider 根本不知道上游为什么失败，而那次真实原因是
            # DashScope 限流（Key 一直是配好的）。用户看到那句假话，
            # 于是去翻 .env 找 Key，方向完全错了。
            #
            # 真实原因由 `ImageService.generate` 汇总各 provider 的异常后
            # 覆写进 `error`（见那边的注释），所以这里只留一句不会出错的话。
            error="出图通道均不可用，本次输出为程序绘制的示意图",
        )

    # ── 绘制 ────────────────────────────────────────────────────────────────
    def _render(self, request: ImageRequest) -> bytes:
        from PIL import Image, ImageDraw

        width = max(320, min(request.width, 1536))
        height = max(320, min(request.height, 1536))
        digest = hashlib.blake2b(request.prompt.encode("utf-8"), digest_size=4).digest()
        (top, bottom), primary, accent = PALETTES[digest[0] % len(PALETTES)]

        image = Image.new("RGB", (width, height), top)
        draw = ImageDraw.Draw(image)

        # 背景垂直渐变
        start = _hex_to_rgb(top)
        end = _hex_to_rgb(bottom)
        for y in range(height):
            ratio = y / max(height - 1, 1)
            draw.line(
                [(0, y), (width, y)],
                fill=tuple(int(start[i] + (end[i] - start[i]) * ratio) for i in range(3)),
            )

        # 象征性「器物」轮廓：同心圆 + 放射芒条，呼应太阳形器与纵目
        center_x, center_y = width // 2, int(height * 0.38)
        radius = int(min(width, height) * 0.18)
        for index in range(12):
            angle = index * 30
            draw.line(
                _polar(center_x, center_y, radius + 12, angle)
                + _polar(center_x, center_y, radius + 40, angle),
                fill=_hex_to_rgb(accent),
                width=2,
            )
        draw.ellipse(
            [center_x - radius, center_y - radius, center_x + radius, center_y + radius],
            outline=_hex_to_rgb(primary),
            width=3,
        )
        draw.ellipse(
            [
                center_x - radius // 3,
                center_y - radius // 3,
                center_x + radius // 3,
                center_y + radius // 3,
            ],
            outline=_hex_to_rgb(accent),
            width=2,
        )

        # 文案区
        title_font = _load_font(max(20, width // 28))
        body_font = _load_font(max(14, width // 44))
        small_font = _load_font(max(12, width // 56))

        margin = int(width * 0.08)
        cursor_y = int(height * 0.62)

        draw.text((margin, cursor_y), "PLACEHOLDER / 占位示意图", font=title_font, fill=_hex_to_rgb(accent))
        cursor_y += int(title_font.size * 1.7)

        meta_lines = [
            f"kind: {request.kind}   size: {request.width}x{request.height}",
            f"seed: {request.seed if request.seed is not None else 'random'}",
        ]
        for line in meta_lines:
            draw.text((margin, cursor_y), line, font=small_font, fill=_hex_to_rgb(primary))
            cursor_y += int(small_font.size * 1.6)

        cursor_y += int(small_font.size * 0.6)
        draw.text((margin, cursor_y), "PROMPT", font=small_font, fill=_hex_to_rgb(accent))
        cursor_y += int(small_font.size * 1.6)

        for line in textwrap.wrap(_ascii_safe(request.prompt), width=42)[:9]:
            draw.text((margin, cursor_y), line, font=body_font, fill=(200, 205, 210))
            cursor_y += int(body_font.size * 1.5)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def _data_uri(data: bytes) -> str:
        import base64

        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")

    async def aclose(self) -> None:  # pragma: no cover
        return None


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _polar(cx: int, cy: int, radius: float, degrees: float) -> tuple[float, float]:
    import math

    radians = math.radians(degrees)
    return (cx + radius * math.cos(radians), cy + radius * math.sin(radians))


def _ascii_safe(text: str) -> str:
    """无中文字体时，中文会渲染成方框；这里保留可读的英文摘要。"""
    cleaned = truncate(text, 600)
    ascii_part = cleaned.encode("ascii", "ignore").decode("ascii")
    return ascii_part if len(ascii_part.strip()) > 40 else cleaned
