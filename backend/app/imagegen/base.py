"""图像生成抽象层。

本项目采用**全代码（API）出图**，不再依赖 ComfyUI 之类的本地服务：
部署形态简单（一个 Key 即可），也不需要为目标机器准备 GPU 与工作流文件。

三层 provider，按「可控性 → 可用性」降级：

1. **DashScope（千问图像系列）** —— 主通道。这里要的是**物理材质还原能力**：
   青铜锈层的颗粒感、锤撰金箔的哑金质感、玉的温润透光，正是通用文生图最容易
   画错的地方，而纹理细节恰好是 qwen-image 系列的强项。
2. **免费第三方通道** —— 无 Key 时的真实出图兜底（带水印、限额低，不建议生产）。
3. **LocalPlaceholder（Pillow 合成）** —— 无 Key 时的最后一道保险。
   产出**明确标注为占位**的示意图，保证整条 Agent 链路可端到端跑通，
   而不是抛异常让功能整体不可用。

关于参数选择的一个重要事实：
`steps` / `cfg` / `sampler` / `lora_strength` 是**扩散采样器**的参数，
只有 ComfyUI 那条链路才消费它们。API 模型不接受这些参数 ——
继续把它们摆在界面上（“steps 32 · cfg 6 · LoRA 0.85”）等于展示一个不生效的旋钮。
因此这里只保留 API 真正支持的参数，风格强度改为由**提示词措辞**表达。

统一契约见 `ImageRequest` / `ImageResult`。上层只认这两个类型。
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.core.config import settings
from app.core.http import is_local_url, make_async_client
from app.core.logging import get_logger
from app.core.metrics import metrics

logger = get_logger("app.imagegen")


@dataclass(slots=True)
class ImageRequest:
    prompt: str
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1280
    seed: int | None = None
    reference_images: list[str] = field(default_factory=list)
    style_tokens: list[str] = field(default_factory=list)
    kind: str = "scene"
    # 覆盖出图模型；为空时用 provider 的默认模型。
    model: str | None = None
    # API 模型的「提示词增强」开关。
    # 本项目必须默认关闭：我们的提示词是「形制要点 + 史料线索 + 质检修正指令」
    # 拼出来的，且向用户承诺了「逐字使用选定提示词」。
    # 开启后模型会自行改写提示词 —— 等于把锁定契约和质检标尺一起架空。
    prompt_extend: bool = False

    @property
    def size(self) -> str:
        return f"{self.width}*{self.height}"

    def snapshot(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "size": self.size,
            "seed": self.seed,
            "prompt_extend": self.prompt_extend,
            "prompt_chars": len(self.prompt),
            "negative_prompt_chars": len(self.negative_prompt),
            "reference_count": len(self.reference_images),
        }


@dataclass(slots=True)
class ImageResult:
    image_url: str
    provider: str
    latency_ms: float
    seed: int | None = None
    content_type: str = "image/png"
    local_path: str | None = None
    degraded: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # 这张图是「模型生成的」还是「程序画的示意图」。
    # 下游必须知道这个区别：对占位图做风格质检在物理上毫无意义 ——
    # 它的像素只由调色板与前几行文字决定，与文物内容无关，
    # 所以永远不达标、也永远不可能通过回炉变好。
    generative: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_url": self.image_url,
            "provider": self.provider,
            "latency_ms": round(self.latency_ms, 1),
            "seed": self.seed,
            "degraded": self.degraded,
            "local_path": self.local_path,
            "error": self.error,
            "generative": self.generative,
            # raw 必须一路带到响应里：截断、水印、上游模型名这些
            # 「决定了这张图为什么是这样」的事实全在这里，
            # 丢掉它等于把排查线索丢在服务端。
            "raw": self.raw,
        }


class ImageProvider(Protocol):
    name: str

    async def available(self) -> bool: ...

    async def generate(self, request: ImageRequest) -> ImageResult: ...

    async def aclose(self) -> None: ...


# ── 通用工具 ────────────────────────────────────────────────────────────────
# 两个客户端：公网走系统代理，本机/私有网段绕开代理。
# 详见 app/core/http.py 中的说明（Windows WinINET 代理不含 ProxyOverride 例外）。
_public_client: httpx.AsyncClient | None = None
_private_client: httpx.AsyncClient | None = None


def _client(url: str) -> httpx.AsyncClient:
    global _public_client, _private_client
    if is_local_url(url):
        if _private_client is None or _private_client.is_closed:
            _private_client = make_async_client(timeout=60.0, trust_env=False)
        return _private_client
    if _public_client is None or _public_client.is_closed:
        _public_client = make_async_client(timeout=60.0, trust_env=True)
    return _public_client


async def aclose_clients() -> None:
    """关闭模块级 HTTP 客户端。

    这两个客户端是延迟创建的全局单例，此前没有任何关停钩子 —— 优雅关停或
    --reload 时连接不会释放，产生 `Unclosed client` 警告并泄漏 fd。
    由 lifespan 的 finally 统一调用。
    """
    global _public_client, _private_client
    for client in (_public_client, _private_client):
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 - 关停阶段的失败不应阻止退出
                pass
    _public_client = None
    _private_client = None


async def fetch_image_bytes(url: str) -> tuple[bytes, str]:
    """把「http(s) URL / data URI / 本地归档路径」统一转成 bytes。

    质检阶段必须拿到像素，而本服务自己的产出存的是 `/media/<file>` 这种相对路径，
    直接丢给 httpx 会报「missing protocol」。这里显式处理本地归档分支，
    既省一次网络往返，也避免服务在没有公网入口时质检直接降级。
    """
    if url.startswith("data:"):
        header, _, payload = url.partition(",")
        content_type = header[5:].split(";")[0] or "image/png"
        return base64.b64decode(payload), content_type

    local = _local_media_path(url)
    if local is not None:
        return local.read_bytes(), _guess_content_type(local.suffix)

    if not url.startswith(("http://", "https://")):
        raise ValueError(f"无法解析的图像地址: {url[:120]}")

    response = await _client(url).get(url)
    response.raise_for_status()
    return response.content, response.headers.get("content-type", "image/png")


MEDIA_PREFIX = "/media/"


def _local_media_path(url: str) -> Path | None:
    """把 `/media/xxx.png` 映射回磁盘文件；不存在或越权时返回 None。"""
    if url.startswith(MEDIA_PREFIX):
        name = url[len(MEDIA_PREFIX) :].split("?")[0]
    elif "/" not in url and "\\" not in url and Path(url).suffix:
        name = url  # 也接受纯文件名
    else:
        return None

    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None  # 阻断路径穿越

    candidate = settings.output_path / name
    return candidate if candidate.is_file() else None


def _guess_content_type(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix.lower(), "application/octet-stream")


def archive_image(data: bytes, *, task_id: str, kind: str, extension: str = ".png") -> str | None:
    """落盘归档，并返回可供前端访问的相对路径 `/media/<file>`。"""
    if not settings.archive_outputs:
        return None
    try:
        settings.output_path.mkdir(parents=True, exist_ok=True)
        digest = hashlib.blake2b(data, digest_size=6).hexdigest()
        filename = f"{task_id}-{kind}-{digest}{extension}"
        target = settings.output_path / filename
        if not target.exists():
            target.write_bytes(data)
        return f"/media/{filename}"
    except Exception as exc:  # noqa: BLE001 - 归档失败不能影响主流程
        logger.warning("归档图像失败: %s", exc)
        return None


def guess_extension(content_type: str) -> str:
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "webp" in content_type:
        return ".webp"
    return ".png"


def artifact_path(filename: str) -> Path:
    return settings.output_path / filename
