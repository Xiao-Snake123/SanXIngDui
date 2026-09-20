"""探针：第三方免费通道到底看不看提示词的**尾部**？

背景
----
回炉失败排查中有一条硬约束：尾部是「本品回炉修正指令」唯一可能出现的位置。
`fit_prompt()` 因此改成保留「头部 + 尾部」，把尾部差异保住。

但这里还有一个更隐蔽的可能：提示词是塞在 URL 里的，上游模型有自己的
**有效 token 窗口**。如果 1400 字符的提示词超出窗口，那么被丢掉的正好是尾部 ——
即使我们保住了尾部差异，模型也看不到，于是两轮出图完全相同、
质检分数一模一样，看起来像「回炉无效 / 模型能力不行」。

本探针把这条链路单独拎出来验证：同一场景下构造两个只差**尾部**的提示词，
分别出图后**逐像素**比较。两种长度都要测，因为结论可能完全不同：

  * `short` —— 提示词未触发截断（几百字）。验证通道「是否听得懂尾部」；
  * `long`  —— 提示词按生产方式截断到 freeimage_max_prompt_chars。
               这才是线上真实形态：头部完全相同，差异只在被保留下来的尾部。

判定：
  * 像素不同 -> 尾部有效，回炉指令确实能影响画面；
  * 像素相同 -> 尾部被上游丢弃（超出有效窗口），
               修正指令必须前置到提示词**开头**，而不是结尾。

判别实验（区分「有效窗口」与「上游缓存」）
----------------------------------------
「尾部丢字」还有另一种解释：上游是按提示词整体做缓存，所以内容变不变都没反应。
两者可以用一个实验区分：**把同一处差异从结尾挪到开头**。

    python scripts/probe_freeimage_tail.py head2

  * 开头差异生效 -> 就是窗口问题，把回炉指令前置（已实施）即可修复；
  * 开头差异也无效 -> 上游在缓存，改 seed 或关掉免费通道才有意义。

用法：
    python scripts/probe_freeimage_tail.py            # 跑短提示词 + 生产长度
    python scripts/probe_freeimage_tail.py 0 1 2      # 指定填充倍数，逐个量边界
    python scripts/probe_freeimage_tail.py short      # 只跑短提示词
    python scripts/probe_freeimage_tail.py head2      # 差异放开头（判别实验）
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.core.config import settings  # noqa: E402

settings.freeimage_enabled = True
settings.freeimage_attempts = 3
# 本探针要量的是**上游的原始有效窗口**，所以刻意关掉 fit_prompt 的截断。
# 若这里就按生产预算截断，测出来的只是「被我们截断后的那个字符串是否生效」，
# 与「上游究竟能吃多长」无关 —— 那会把窗口量成一个假数字。
# 生产预算（settings.freeimage_max_prompt_chars）应当是本次测得的窗口**以下的保守值**。
settings.freeimage_max_prompt_chars = 1_000_000

from app.imagegen.base import ImageRequest  # noqa: E402
from app.imagegen.freeimage import FreeImageProvider  # noqa: E402

# 「头部」：真实风格的一段提示词。
HEAD = (
    "考古档案照片：三星堆黄金面具，金箔锤揲成型，凸起的鼻梁、镂空的双眼与下垂的耳部，"
    "边缘留有穿孔，原本附着于青铜或木质的人头像。出土于五号祭祀坑，"
    "宽约二十三厘米、高约二十八厘米，重约二百八十克，是目前三星堆出土同类金器中"
    "体量最大、保存最完整的一件。画面为出土现场记录，分层掩埋的土壤剖面，"
    "上层象牙与玉石器，中层青铜重器，下层烧骨与炭屑。"
    "照明为阴天漫射光，无强烈方向光，无镜面高光。构图：器物居中完整可见，"
    "留有充足中性留白。禁止：铁器、青花瓷、釉上彩瓷、汉字铭文、楹联、"
    "佛教造像要素、纸质书籍、现代家具、玻璃器皿、塑料制品、任何现代标识。"
)

# 用来把提示词撑到生产长度（超过 freeimage_max_prompt_chars）的填充段。
FILLER = (
    "补充形制依据：面具面相方正，鼻梁宽厚而挺直，双眼以镂空方式表现，"
    "眼眶呈杏仁状，耳部作卷云形下垂，下颌线条平直，整体比例严整对称。"
    "表面可见锤揲留下的细密锤痕与不均的哑光金色，局部有压凹与边缘撕裂，"
    "穿孔位置沿额部与两侧对称分布。周围土体呈黄褐色，含炭屑与烧骨颗粒，"
    "器物与土体接触面有明显的腐蚀过渡带。"
)

TAIL_A = " 整体色调：明亮的高饱和朱红，正红主导画面。"
TAIL_B = " 整体色调：近乎单色的深蓝，冷蓝主导画面。"

# 「差异放开头」变体。用来区分两种解释：
#   * 差异放开头仍然没有影响画面 -> 上游是按提示词整体做**缓存**（或压根没在听内容）；
#   * 差异放开头生效、放结尾失效   -> 就是**有效窗口**问题，把指令前置即可修复。
HEAD_A = "整体色调：明亮的高饱和朱红，正红主导画面。"
HEAD_B = "整体色调：近乎单色的深蓝，冷蓝主导画面。"


def _pixels(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as raw:
        return np.asarray(raw.convert("RGB"), dtype=np.int16)


async def _generate(provider: FreeImageProvider, label: str, prompt: str) -> bytes:
    request = ImageRequest(prompt=prompt, width=768, height=768, kind="artifact")
    fitted, truncated = provider.fit_prompt(prompt, settings.freeimage_max_prompt_chars)
    print(f"\n[{label}] 原始 {len(prompt)} 字 -> 下发 {len(fitted)} 字（截断={truncated}）")
    print(f"[{label}] 已下发提示词 sha1 = {hashlib.sha1(fitted.encode()).hexdigest()[:12]}")
    print(f"[{label}] 头部 60 字   = {fitted[:60]!r}")
    print(f"[{label}] 尾部 60 字   = ...{fitted[-60:]!r}")
    result = await provider.generate(request)
    assert result.local_path, "未落盘，无法逐像素比较"
    path = Path("var") / "outputs" / Path(result.local_path).name
    print(f"[{label}] provider={result.provider} latency={result.latency_ms:.0f}ms")
    return path.read_bytes()


async def _scenario(provider: FreeImageProvider, name: str, left_prompt: str, right_prompt: str) -> bool:
    print(f"\n{'=' * 70}\n场景 {name}：两个提示词只差一处色调描述，其余逐字相同\n{'=' * 70}")
    left = await _generate(provider, f"{name}-A(红)", left_prompt)
    right = await _generate(provider, f"{name}-B(蓝)", right_prompt)

    a, b = _pixels(left), _pixels(right)
    print(f"\n---- {name} 逐像素比较 ----")
    print(f"  A sha1 = {hashlib.sha1(left).hexdigest()[:14]}  ({len(left)} 字节)")
    print(f"  B sha1 = {hashlib.sha1(right).hexdigest()[:14]}  ({len(right)} 字节)")
    if a.shape != b.shape:
        print(f"  [PASS] 尺寸都不同: {a.shape} vs {b.shape} -> 这处差异确实生效")
        return True
    diff = abs(a - b)
    # 与 check_revision_effect.py 同一口径：逐通道容差 2，滤掉 JPEG 重编码噪声
    differing = int((diff.max(axis=2) > 2).sum())
    total = a.shape[0] * a.shape[1]
    ratio = differing / total
    print(f"  尺寸相同 {a.shape}；最大像素差 = {int(diff.max())}；不同像素 = {differing}/{total} ({ratio:.1%})")
    if differing == 0:
        print(f"  [FAIL] {name}: 这处改动完全没有影响画面 -> 该位置的文字被上游忽略了")
        return False
    print(f"  [PASS] {name}: 这处改动影响了画面")
    return True


def _parse_mode(mode: str) -> tuple[bool, int]:
    """解析一个参数 -> (差异是否放在开头, 填充倍数)。

    纯数字 = 尾部差异 + 填充 N 段（保持向后兼容，历史结论都是这个模式跑出来的）；
    `short` = 尾部差异 + 不填充；`headN` / `head` = 把差异放到开头。
    """
    text = mode.strip().lower()
    if text.startswith("head"):
        return True, int(text[4:] or 0)
    if text == "short":
        return False, 0
    return False, max(0, int(text))


async def main() -> int:
    args = sys.argv[1:]
    # 无参数 = 最省事的默认组合（短提示词 + 生产长度各一）
    modes: list[str] = args or ["short", "3"]
    provider = FreeImageProvider(
        url_template=settings.freeimage_url_template,
        model=settings.freeimage_model,
        timeout=settings.freeimage_timeout,
        attempts=settings.freeimage_attempts,
        backoff_seconds=settings.freeimage_backoff_seconds,
        token=settings.freeimage_token,
    )
    results: dict[str, bool] = {}
    lengths: dict[str, int] = {}
    try:
        for mode in modes:
            is_head, repeats = _parse_mode(mode)
            base = HEAD + FILLER * repeats
            if is_head:
                left_prompt = f"{HEAD_A} {base}"
                right_prompt = f"{HEAD_B} {base}"
                label = f"head x{repeats}({len(left_prompt)} 字)"
            else:
                left_prompt, right_prompt = base + TAIL_A, base + TAIL_B
                tag = "short" if repeats == 0 else f"fill x{repeats}"
                label = f"tail {tag}({len(left_prompt)} 字)"
            lengths[label] = len(left_prompt)
            results[label] = await _scenario(provider, label, left_prompt, right_prompt)
    finally:
        await provider.aclose()

    print(f"\n{'=' * 70}\n汇总（差异注入位置 -> 是否影响画面）\n{'=' * 70}")
    for name, ok in results.items():
        print(f"  {name:<24} {'生效' if ok else '被忽略'}")

    tail_only = {n: ok for n, ok in results.items() if n.startswith("tail")}
    head_only = {n: ok for n, ok in results.items() if n.startswith("head")}

    if len(tail_only) > 1:
        ordered = sorted(tail_only.items(), key=lambda item: lengths[item[0]])
        last_ok = [n for n, ok in ordered if ok]
        first_bad = [n for n, ok in ordered if not ok]
        if last_ok and first_bad:
            if lengths[last_ok[-1]] < lengths[first_bad[0]]:
                print(
                    f"\n  [尾部差异] 有效窗口落在 {lengths[last_ok[-1]]} 字（生效）"
                    f" 与 {lengths[first_bad[0]]} 字（失效）之间 —— "
                    "提示词预算应设为前者以下的保守值。"
                )
            else:
                print(
                    "\n  [尾部差异] 结果**非单调**（长提示词生效、短提示词失效）。"
                    "\n  说明边界不是「纯字符数」，很可能按 token 计且随内容变化："
                    "\n  字符预算只能当近似值用，必须取保守值；关键指令必须前置，不能押在尾部。"
                )

    if head_only:
        if all(head_only.values()):
            print(
                "\n  [开头差异] 生效 -> 可以排除「上游按提示词整体缓存」的解释，"
                "确认是有效窗口问题：把回炉修正指令前置（已实施）即可修复。"
            )
        else:
            print(
                "\n  [开头差异] 被忽略 -> 不是窗口问题那么简单，"
                "上游很可能在按提示词缓存：需要改 seed 策略，或关掉免费通道做生产验证。"
            )

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
