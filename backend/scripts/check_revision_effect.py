"""验证真实出图路径下，回炉是否**真的**改变了模型输入与输出。

与 diagnose_revision.py 的区别：本脚本保留真实出图通道（不禁用免费通道），
因为要验证的正是「回炉指令有没有送到模型」这条真实链路。

判定依据（三条缺一不可）：
  1. 两轮下发的提示词是否不同（不同 → 指令确实进了 URL）；
  2. 两轮的图像**像素**是否不同（不同 → 模型确实读到了这段文字）；
  3. 回炉轮的下发提示词是否以修正指令开头，且引导段没有被截断窗口切掉
     （窗口很窄，见 settings.freeimage_max_prompt_chars 的实测注释）。

⚠️ 为什么必须比像素、不能比字节（本脚本踩过的坑）：
  这里原本用「文件字节哈希」判断两轮图像是否相同，并且输出过 PASS —— **那是假通过**。
  上游对同一张图重新编码后，JPEG 容器字节不同、解码后的像素完全相同，
  于是哈希不同 → 脚本误判为「两轮图像不同 → 回炉有效」，把真 bug 掩盖了。
  实测 `probe_freeimage_tail.py`：732 字的提示词，两轮图字节数不同，最大像素差 = 0。
  教训：验证图像是否变化必须比像素。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.core.config import settings  # noqa: E402

settings.dashscope_api_key = ""
settings.max_revisions = 2
# 把阈值拉高，强制触发回炉，才能检验「回炉指令有没有真的送到模型」。
# 用环境变量控制，默认 0.95：正常出图几乎不可能一次达到，必然回炉一次。
settings.style_threshold = float(os.getenv("FORCE_THRESHOLD", "0.95"))
# 免费通道是共享配额，实测经常撞 429。验证脚本必须比业务链路更有耐心：
# 一旦被限流就会降级到占位图，质检随之 skipped，整个验证会被跳过 ——
# 而「跳过验证」如果还返回成功，那就是另一种假通过。
settings.freeimage_attempts = 4
settings.freeimage_backoff_seconds = 18.0

from app.graph.runner import run_once  # noqa: E402
from app.imagegen.freeimage import FreeImageProvider  # noqa: E402

REQUEST = {
    "kind": "scene",
    "item": "青铜大立人",
    "identity": "大祭司",
    "scene": "博物馆展厅",
    "style": "博物馆纪实摄影",
    "max_revisions": 2,
}

# 逐通道容差。JPEG 是有损重编码，同一张图重编码一次就可能出现 ±1~2 的像素抖动；
# 不给容差的话，这种抖动会被当成「画面变了」，又会退回到假通过。
PIXEL_TOLERANCE = 2

# 回炉轮的提示词必须以修正指令开头。这是「指令一定在模型视野内」的可观测凭证。
REVISION_PREFIX = "REVISION"
# 与 restoration_worker._compose_prompt 的措辞对齐：引导语 + 分隔符
APPLY_MARK = "- apply: "
# 至少要能看到指令开头的这么多字。整条指令可能更长，但窗口是上游给的硬约束，
# 能承诺的是「开头一定在窗口内」。
DIRECTIVE_PROBE_CHARS = 40


def _resolve(relative: str) -> Path:
    return BACKEND / str(relative).lstrip("/").replace("media/", "var/outputs/")


def _load_pixels(relative: str) -> np.ndarray | None:
    """把图读到内存里解码成像素数组；读不到返回 None。

    注意这里就已经完成了「解码」这一步：后面所有比较都在像素上做，不碰字节。
    """
    path = _resolve(relative)
    if not path.exists():
        return None
    try:
        with Image.open(path) as raw:
            return np.asarray(raw.convert("RGB"), dtype=np.int16)
    except Exception:  # noqa: BLE001 - 解码失败按「拿不到像素」处理，如实上报
        return None


def _pixel_hash(pixels: np.ndarray | None) -> str:
    if pixels is None:
        return "(missing)"
    return hashlib.blake2b(pixels.astype(np.uint8).tobytes(), digest_size=8).hexdigest()


def _compare(left: np.ndarray | None, right: np.ndarray | None) -> tuple[float | None, int]:
    """返回 (差异像素占比, 最大像素差)。尺寸不可比时占比为 None。"""
    if left is None or right is None or left.shape != right.shape:
        return None, -1
    diff = np.abs(left - right)
    worst = int(diff.max())
    differing = int((diff.max(axis=2) > PIXEL_TOLERANCE).sum())
    return differing / float(left.shape[0] * left.shape[1]), worst


async def main() -> int:
    result = await run_once(REQUEST)
    history = result.get("revision_history") or []
    qa = result.get("qa") or {}

    print("=" * 78)
    print(f"provider 链最终产出: {(result.get('image') or {}).get('provider')}")
    print(f"质检: skipped={qa.get('skipped')} passed={qa.get('passed')} "
          f"score={qa.get('score')} decision={qa.get('decision')}")
    print(f"回炉次数: {result.get('revisions')}   历史轮次: {len(history)}")
    print(f"免费通道提示词预算: {settings.freeimage_max_prompt_chars} 字（实测有效窗口以内）")
    print("=" * 78)

    if qa.get("skipped"):
        # 关键：这里**不能**返回 0。
        # 落到占位图意味着「没有可判定的对象」，也就意味着**什么都没验证**。
        # 用 0 退出会被自动化当成 PASS，正是本项目反复强调要避免的假通过。
        provider = (result.get("image") or {}).get("provider")
        print(f"\n来源 provider = {provider}")
        print("REVISION EFFECT CHECK SKIPPED —— 未验证（未取得真实生成图，无法比像素）")
        print("  排查：免费通道是否限流(429)/被墙；或直接看 probe_image_channels.py 的连通性结论。")
        return 2

    pixels = [_load_pixels(str(item.get("image_url") or "")) for item in history]
    failures: list[str] = []

    limit = int(settings.freeimage_max_prompt_chars)
    for index, item in enumerate(history):
        prompt = str(item.get("prompt") or "")
        print(f"\n[第 {index} 轮] provider={item.get('provider')} prompt={len(prompt)} 字符")
        print(f"  像素 hash = {_pixel_hash(pixels[index])}")
        if index == 0:
            continue

        previous = str(history[index - 1].get("prompt") or "")
        if prompt == previous:
            # 提示词没变，下面那些「指令有没有进窗口」的检查就没意义了，直接记失败跳过。
            failures.append(f"第 {index} 轮提示词与上一轮完全相同 —— 回炉指令没送到")
            continue

        # 修正指令已前置，所以该看的是**开头**那段，而不是尾部追加了什么
        print(f"  本轮开头 160 字 = {prompt[:160]!r}")

        if prompt.startswith(REVISION_PREFIX):
            fitted, truncated = FreeImageProvider.fit_prompt(prompt, limit)
            # 头部窗口 = fit_prompt 里「保头」的那一段。它才是模型一定看得见的部分。
            head_len = max(1, int(limit * 0.6))
            window = fitted[:head_len]
            print(f"  截断后 {len(fitted)} 字（truncated={truncated}），头部窗口 {head_len} 字")
            print(f"  窗口内容 = {window!r}")

            if not window.startswith(REVISION_PREFIX):
                failures.append(
                    f"第 {index} 轮下发内容不再以修正指令开头 —— 指令被挤出了模型视野"
                )
            # 能承诺的不是「整条指令都在窗口内」—— 单条指令本身就可能有一百多字，
            # 而窗口是上游给的硬约束。能承诺的是「指令的开头一定在窗口内」：
            # 模型至少要知道自己在改什么，而不是只看到一句 apply:。
            apply_at = prompt.find(APPLY_MARK)
            probe = prompt[apply_at + len(APPLY_MARK):][:DIRECTIVE_PROBE_CHARS] if apply_at >= 0 else ""
            if probe and probe not in window:
                failures.append(
                    f"第 {index} 轮的修改指令开头被挤出了头部窗口 —— 模型读不到要改什么"
                )
        else:
            failures.append(f"第 {index} 轮提示词与上一轮不同，却没有前置修正指令")

    if len(pixels) >= 2:
        print("\n---- 逐像素比较（不是比字节）----")
        for i in range(1, len(pixels)):
            ratio, worst = _compare(pixels[i - 1], pixels[i])
            if ratio is None:
                print(f"  第 {i - 1} -> {i} 轮：尺寸不同或存在读不到的像素，视为不同")
                continue
            total = pixels[i].shape[0] * pixels[i].shape[1]
            print(
                f"  第 {i - 1} -> {i} 轮：最大像素差 = {worst}；"
                f"不同像素 = {int(ratio * total)}/{total} ({ratio:.1%})"
            )
            if ratio == 0:
                failures.append(
                    f"第 {i - 1} -> {i} 轮图像逐像素相同 —— 回炉没有改变画面"
                    "（修正指令没进模型视野，或上游按提示词返回了缓存）"
                )

    raw = (result.get("image") or {}).get("raw") or {}
    if raw:
        # 注意：Windows 控制台是 GBK，打印非 ASCII 符号（如 U+26A0）会 UnicodeEncodeError
        print(
            "\nraw: prompt_truncated={} chars={}/{} model={}".format(
                raw.get("prompt_truncated"),
                raw.get("prompt_chars_sent"),
                raw.get("prompt_chars_total"),
                raw.get("model"),
            )
        )

    if failures:
        print("\nREVISION EFFECT CHECK FAIL")
        for item in failures:
            print("  -", item)
        return 1
    print("\nREVISION EFFECT CHECK PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
