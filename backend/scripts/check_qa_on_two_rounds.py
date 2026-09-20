"""离线复算两轮回炉图的质检指标，回答一个具体问题：

「两轮质检得分完全相同」到底是
  (a) 两张图真的几乎一样（指标巧合），还是
  (b) 第二轮质检拿到的还是第一轮的字节（取图环节串了）。

做法：把两轮落盘的图片直接喂给同一个 objective 评估器，比较逐项指标。
评估器是确定性的 —— 同一张图永远得到同样的分。
因此：若两张图的指标不同，则「线上两轮完全相同」一定是数据流问题；
若两张图的指标也完全相同，说明它们在统计意义上就是同一张，
需要人工看图确认（也可能是上游对近似提示词给出了近乎一致的构图）。

用法：
    python scripts/check_qa_on_two_rounds.py <图A> <图B> [profile_key] [material_key]
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.agents.lexicon import MATERIALS  # noqa: E402
from app.quality.objective import evaluate  # noqa: E402
from app.quality.style_profiles import resolve_profile  # noqa: E402

SCALARS = (
    "saturation_mean",
    "saturation_p90",
    "specular_ratio",
    "edge_density",
    "contrast",
    "brightness_mean",
    "colorfulness",
    "flat_ratio",
)


def _report(path: Path, profile_key: str, material_key: str | None) -> tuple[float, dict[str, float]]:
    data = path.read_bytes()
    profile = resolve_profile(profile_key)
    material = MATERIALS.get(material_key) if material_key else None
    verdict = evaluate(data, profile, material=material)

    print(f"\n== {path.name} ==")
    print(f"   sha1[:14] = {hashlib.sha1(data).hexdigest()[:14]}   ({len(data)} bytes)")
    print(f"   score     = {verdict.score:.4f}")
    if verdict.hard_fail:
        print(f"   hard_fail = {verdict.hard_fail}")

    flags: dict[str, float] = {}
    metrics = verdict.metrics
    for name in SCALARS:
        value = float(getattr(metrics, name))
        flags[name] = value
        print(f"   {name:<18} {value:.4f}")
    for name, value in sorted((metrics.family_shares or {}).items()):
        flags[f"family.{name}"] = float(value)
        print(f"   family.{name:<11} {float(value):.4f}")
    for item in verdict.violations:
        print(f"   ! [{item.severity}] {item.metric}: {item.value:.4f} (期望 {item.expected})")
        print(f"     -> {item.directive}")
    return verdict.score, flags


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    left, right = Path(sys.argv[1]), Path(sys.argv[2])
    profile_key = sys.argv[3] if len(sys.argv) > 3 else "archival"
    material_key = sys.argv[4] if len(sys.argv) > 4 else None

    left_score, left_flags = _report(left, profile_key, material_key)
    right_score, right_flags = _report(right, profile_key, material_key)

    print("\n---- 结论 ----")
    print(f"  profile={profile_key}  material={material_key or '(无)'}")
    print(f"  A={left_score:.4f}  B={right_score:.4f}  delta={abs(left_score - right_score):.4f}")

    drifted = sorted(
        name
        for name in set(left_flags) & set(right_flags)
        if abs(left_flags[name] - right_flags[name]) > 1e-6
    )
    if drifted:
        print(f"  指标差异项: {', '.join(drifted)}")
        print("  [OK] 评估器对两张图给出了不同指标 -> 评估器是敏感的；")
        print("       若线上两轮分数完全一致，问题在「质检拿到哪张图」而不是「质检算法」")
    else:
        print("  [WARN] 两张字节不同的图，逐项指标完全一致。")
        print("         说明它们在统计意义上就是同一张，需人工看图确认。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
