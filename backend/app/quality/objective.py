"""客观风格指标（无模型、可复现、毫秒级）。

为什么需要它
------------
只靠 VLM 裁判有三个问题：贵、慢、不稳（同一张图两次打分可能差 10 分）。
因此这里用一组**确定性图像统计量**先做筛查：

- 它是**可复现的**，同样的图永远得到同样的分，评估报告才站得住；
- 它抓得住 Diffusion 最典型的几类塌方：过饱和荧光色、镜面高光（新铜感）、
  糊成一团的塑料质感、纯色/空图；
- 它把「风格区间」变成可版本化的数值（见 `style_profiles.py`），
  调风格时改的是配置而不是散落各处的 prompt 文案。

VLM 负责它做不到的部分：时代错配识别、形制忠实度、纹饰合理性。
两者加权融合，见 `style_guard.py`。
"""

from __future__ import annotations

import colorsys
import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image, ImageFilter, UnidentifiedImageError

from app.core.logging import get_logger
from app.quality.style_profiles import StyleProfile

# MaterialSpec 在 app.agents.lexicon 中定义，但这里只做类型标注用（无循环导入风险：
# lexicon 不反向依赖 quality）。用 TYPE_CHECKING 保持 import 图单向。
if TYPE_CHECKING:  # pragma: no cover
    from app.agents.lexicon import MaterialSpec

logger = get_logger("app.quality.objective")

ANALYSIS_SIZE = (256, 256)

FAMILY_RULES: tuple[tuple[str, float, float], ...] = (
    ("red", 345.0, 15.0),
    ("brown", 15.0, 45.0),
    ("gold", 45.0, 70.0),
    ("green", 70.0, 165.0),
    ("blue", 165.0, 260.0),
    ("purple", 260.0, 345.0),
)


@dataclass(slots=True)
class ObjectiveMetrics:
    width: int
    height: int
    saturation_mean: float
    saturation_p90: float
    specular_ratio: float
    edge_density: float
    contrast: float
    brightness_mean: float
    colorfulness: float
    flat_ratio: float
    family_shares: dict[str, float] = field(default_factory=dict)
    is_blank: bool = False
    is_reading: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "saturation_mean": round(self.saturation_mean, 4),
            "saturation_p90": round(self.saturation_p90, 4),
            "specular_ratio": round(self.specular_ratio, 4),
            "edge_density": round(self.edge_density, 4),
            "contrast": round(self.contrast, 4),
            "brightness_mean": round(self.brightness_mean, 4),
            "colorfulness": round(self.colorfulness, 3),
            "flat_ratio": round(self.flat_ratio, 4),
            "family_shares": {k: round(v, 4) for k, v in self.family_shares.items()},
            "is_blank": self.is_blank,
        }


@dataclass(slots=True)
class Violation:
    metric: str
    value: float
    expected: str
    severity: float  # 0~1，越大越严重
    directive: str   # 可直接注入下一轮 prompt 的修正指令

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": round(self.value, 4),
            "expected": self.expected,
            "severity": round(self.severity, 3),
            "directive": self.directive,
        }


@dataclass(slots=True)
class ObjectiveScore:
    score: float
    violations: list[Violation]
    metrics: ObjectiveMetrics
    hard_fail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "hard_fail": self.hard_fail,
            "violations": [item.to_dict() for item in self.violations],
            "metrics": self.metrics.to_dict(),
        }


def analyze(image_bytes: bytes) -> ObjectiveMetrics | None:
    try:
        with Image.open(io.BytesIO(image_bytes)) as raw:
            raw = raw.convert("RGB")
            original_size = raw.size
            small = raw.resize(ANALYSIS_SIZE, Image.LANCZOS)
    except (UnidentifiedImageError, OSError) as exc:
        logger.warning("图像解码失败: %s", exc)
        return None

    array = np.asarray(small, dtype=np.float32) / 255.0
    red, green, blue = array[..., 0], array[..., 1], array[..., 2]

    max_c = array.max(axis=2)
    min_c = array.min(axis=2)
    delta = max_c - min_c
    saturation = np.where(max_c > 1e-6, delta / np.maximum(max_c, 1e-6), 0.0)
    value = max_c

    # 色相（度）
    hue = np.zeros_like(max_c)
    mask = delta > 1e-6
    with np.errstate(invalid="ignore", divide="ignore"):
        red_max = mask & (max_c == red)
        green_max = mask & (max_c == green) & ~red_max
        blue_max = mask & (max_c == blue) & ~red_max & ~green_max
        hue[red_max] = (60 * ((green - blue) / np.where(delta == 0, 1, delta)) % 360)[red_max]
        hue[green_max] = (60 * ((blue - red) / np.where(delta == 0, 1, delta)) + 120)[green_max]
        hue[blue_max] = (60 * ((red - green) / np.where(delta == 0, 1, delta)) + 240)[blue_max]

    # 近白高光：高亮度 + 低饱和 → 金属镜面反射（氧化青铜上不应大量出现）
    specular = (value > 0.94) & (saturation < 0.16)
    specular_ratio = float(specular.mean())

    # 边缘密度：衡量细节丰富度；过低 = 糊，过高 = 噪点/过锐
    grayscale = small.convert("L")
    edges = np.asarray(grayscale.filter(ImageFilter.FIND_EDGES), dtype=np.float32) / 255.0
    edge_density = float(np.clip(edges.mean() * 6.0, 0.0, 1.0))

    luminance = np.asarray(grayscale, dtype=np.float32) / 255.0
    contrast = float(luminance.std())

    # 平坦区占比：局部方差极小的像素比例（塑料感 / 大面积纯色的代理指标）
    local_mean = _box_blur(luminance, 3)
    local_sq_mean = _box_blur(luminance * luminance, 3)
    local_var = np.clip(local_sq_mean - local_mean * local_mean, 0.0, None)
    flat_ratio = float((local_var < 1e-5).mean())

    # Hasler-Süsstrunk 色彩丰富度
    rg = red - green
    yb = 0.5 * (red + green) - blue
    colorfulness = float(
        np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    )

    family_shares = _family_shares(hue, saturation)

    return ObjectiveMetrics(
        width=original_size[0],
        height=original_size[1],
        saturation_mean=float(saturation.mean()),
        saturation_p90=float(np.percentile(saturation, 90)),
        specular_ratio=specular_ratio,
        edge_density=edge_density,
        contrast=contrast,
        brightness_mean=float(value.mean()),
        colorfulness=colorfulness,
        flat_ratio=flat_ratio,
        family_shares=family_shares,
        is_blank=bool(contrast < 0.02 and edge_density < 0.02),
    )


def effective_family_min(
    profile: StyleProfile, material: MaterialSpec | None
) -> dict[str, float]:
    """合并「风格要求的色域」与「材质应有的色域」，冲突时**材质优先**。

    职责划分：
      * 风格档案管**环境色**（展陈厅的深色背景、赛博人的霓虹环境光）
      * 材质档案管**材质色**（金就是金，玉就是玉）

    为什么材质能否决风格：风格提要求是「审美偏好」，材质提要求是「物理事实」。
    原先所有非创作型档案都硬要求 green >= 0.10，黄金面具因此被判不合格，
    回炉时还把「补足青铜锈绿」注入提示词 —— 等于用质检把黄金主动改成青铜。
    除了去告诉，材质还会在自己的色族上取较大值（两者都要求同一个色族时取严）。
    """
    mins = dict(profile.family_min)
    if material is None:
        return mins
    for family in material.forbidden_families:
        mins.pop(family, None)
    for family, minimum in material.family_min.items():
        mins[family] = max(mins.get(family, 0.0), minimum)
    return mins


# 色族 → 中文提示，用于把「色域不足」翻译成可直接执行的修改指令
_FAMILY_LABEL = {
    "red": "偏红色调",
    "brown": "土褐色调",
    "gold": "哑金 / 暖金色调",
    "green": "锈绿 / 青绿色调",
    "blue": "冷蓝色调",
    "purple": "紫色调",
    "neutral": "中性灰调",
}


def _material_hint(material: MaterialSpec | None) -> str:
    return f"{material.label}应有的" if material is not None else "器物应有的"


def evaluate(
    image_bytes: bytes,
    profile: StyleProfile,
    material: MaterialSpec | None = None,
) -> ObjectiveScore:
    metrics = analyze(image_bytes)
    if metrics is None:
        return ObjectiveScore(
            score=0.0,
            violations=[],
            metrics=_empty_metrics(),
            hard_fail="图像无法解码",
        )

    if metrics.is_blank:
        return ObjectiveScore(
            score=0.0,
            violations=[],
            metrics=metrics,
            hard_fail="画面近似纯色，判定为生成失败",
        )

    violations: list[Violation] = []
    components: list[tuple[float, float]] = []  # (满足度, 权重)

    # 1) 饱和度区间（创作型风格放宽）
    sat_lo, sat_hi = profile.saturation_range
    sat_score = _range_score(metrics.saturation_mean, sat_lo, sat_hi, soft=0.10)
    components.append((sat_score, 0.16))
    if sat_score < 0.85:
        if metrics.saturation_mean > sat_hi:
            violations.append(
                Violation(
                    metric="saturation_mean",
                    value=metrics.saturation_mean,
                    expected=f"<={sat_hi:.2f}",
                    severity=1.0 - sat_score,
                    directive=(
                        f"饱和度偏高（{metrics.saturation_mean:.2f} > {sat_hi:.2f}）："
                        f"降低整体饱和度，改用{_material_hint(material)}"
                        "低饱和土色系，避免荧光感"
                    ),
                )
            )
        else:
            violations.append(
                Violation(
                    metric="saturation_mean",
                    value=metrics.saturation_mean,
                    expected=f">={sat_lo:.2f}",
                    severity=1.0 - sat_score,
                    directive=f"画面过于灰白失色，恢复{_material_hint(material)}材质色差与层次",
                )
            )

    # 2) 镜面高光占比 —— 出图应该保持材质本身的哑光属性，而不是打成镜面金属
    if not profile.creative:
        spec_score = _max_score(metrics.specular_ratio, profile.specular_max, soft=0.03)
        components.append((spec_score, 0.22))
        if spec_score < 0.85:
            material_label = material.label if material is not None else "该材质"
            violations.append(
                Violation(
                    metric="specular_ratio",
                    value=metrics.specular_ratio,
                    expected=f"<={profile.specular_max:.3f}",
                    severity=1.0 - spec_score,
                    directive=(
                        f"金属镜面反光过强，呈现「全新打磨」观感："
                        f"改为{material_label}的哑光表面、弱化高光斑，让表面呈漫反射"
                    ),
                )
            )

    # 3) 色彩家族占比（材质可修正风格提出的要求）
    family_min = effective_family_min(profile, material)
    if family_min:
        deficits = []
        family_satisfaction = []
        for family, minimum in family_min.items():
            actual = metrics.family_shares.get(family, 0.0)
            family_satisfaction.append(min(1.0, actual / minimum if minimum > 0 else 1.0))
            if actual < minimum:
                deficits.append(f"{family} {actual:.2f}<{minimum:.2f}")
        family_score = float(np.mean(family_satisfaction)) if family_satisfaction else 1.0
        components.append((family_score, 0.26))
        if deficits:
            wanted = " / ".join(
                _FAMILY_LABEL.get(family, family)
                for family in family_min
                if metrics.family_shares.get(family, 0.0) < family_min[family]
            )
            violations.append(
                Violation(
                    metric="color_family",
                    value=family_score,
                    expected=" / ".join(f"{k}>={v:.2f}" for k, v in family_min.items()),
                    severity=1.0 - family_score,
                    directive=(
                        "目标色域占比不足（" + ", ".join(deficits) + "）："
                        f"补足{wanted}，使画面与{_material_hint(material)}真实色彩一致"
                    ),
                )
            )

    # 4) 细节密度
    edge_score = _range_score(metrics.edge_density, *profile.edge_density_range, soft=0.10)
    components.append((edge_score, 0.16))
    if edge_score < 0.85:
        directive = (
            "画面细节不足、过于平滑（塑料感）：增加器表锈蚀颗粒与铸痕起伏"
            if metrics.edge_density < profile.edge_density_range[0]
            else "画面噪点/锐化过度：降低锐度，保留自然的柔和过渡"
        )
        violations.append(
            Violation(
                metric="edge_density",
                value=metrics.edge_density,
                expected=f"{profile.edge_density_range[0]:.2f}~{profile.edge_density_range[1]:.2f}",
                severity=1.0 - edge_score,
                directive=directive,
            )
        )

    # 5) 对比度
    contrast_score = _range_score(metrics.contrast, *profile.contrast_range, soft=0.12)
    components.append((contrast_score, 0.10))

    # 6) 平坦区占比（塑料感惩罚，仅非创作型生效）
    if not profile.creative and metrics.flat_ratio > 0.55:
        penalties = 0.12 * min(1.0, (metrics.flat_ratio - 0.55) / 0.35)
        components.append((1.0 - penalties / 0.12, 0.10))
        violations.append(
            Violation(
                metric="flat_ratio",
                value=metrics.flat_ratio,
                expected="<=0.55",
                severity=penalties / 0.12,
                directive=(
                    "大面积为无纹理平坦区域，表面缺乏肌理与工艺痕迹，需补充表面细节"
                ),
            )
        )
    else:
        components.append((1.0, 0.10))

    total_weight = sum(weight for _, weight in components)
    score = sum(value * weight for value, weight in components) / total_weight if total_weight else 0.0

    return ObjectiveScore(
        score=float(np.clip(score, 0.0, 1.0)),
        violations=sorted(violations, key=lambda item: item.severity, reverse=True),
        metrics=metrics,
    )


# ── 工具 ────────────────────────────────────────────────────────────────────
def _family_shares(hue: np.ndarray, saturation: np.ndarray) -> dict[str, float]:
    total = hue.size
    if total == 0:
        return {}
    neutral_mask = saturation < 0.12
    shares: dict[str, float] = {"neutral": float(neutral_mask.mean())}

    colored = ~neutral_mask
    for name, low, high in FAMILY_RULES:
        if low < high:
            mask = colored & (hue >= low) & (hue < high)
        else:  # 跨 0 度的红色区间
            mask = colored & ((hue >= low) | (hue < high))
        shares[name] = float(mask.sum() / total)

    return shares


def _box_blur(array: np.ndarray, radius: int) -> np.ndarray:
    """用积分图实现 O(1) 均值滤波，避免引入 scipy。"""
    padded = np.pad(array, radius, mode="edge")
    integral = padded.cumsum(0).cumsum(1)
    integral = np.pad(integral, ((1, 0), (1, 0)), mode="constant")
    size = 2 * radius + 1
    height, width = array.shape
    y0, y1 = np.arange(height), np.arange(height) + size
    x0, x1 = np.arange(width), np.arange(width) + size
    total = (
        integral[np.ix_(y1, x1)]
        - integral[np.ix_(y0, x1)]
        - integral[np.ix_(y1, x0)]
        + integral[np.ix_(y0, x0)]
    )
    return total / (size * size)


def _range_score(value: float, low: float, high: float, *, soft: float) -> float:
    if low <= value <= high:
        return 1.0
    distance = (low - value) if value < low else (value - high)
    return float(np.clip(1.0 - distance / max(soft, 1e-6), 0.0, 1.0))


def _max_score(value: float, maximum: float, *, soft: float) -> float:
    if value <= maximum:
        return 1.0
    return float(np.clip(1.0 - (value - maximum) / max(soft, 1e-6), 0.0, 1.0))


def _empty_metrics() -> ObjectiveMetrics:
    return ObjectiveMetrics(
        width=0,
        height=0,
        saturation_mean=0.0,
        saturation_p90=0.0,
        specular_ratio=1.0,
        edge_density=0.0,
        contrast=0.0,
        brightness_mean=0.0,
        colorfulness=0.0,
        flat_ratio=1.0,
        family_shares={},
        is_blank=True,
    )
