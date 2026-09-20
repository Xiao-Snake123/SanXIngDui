"""验证用户反馈的那条坏提示词是否已被修好。

用户原话：「这什么鬼提示词，而且生成的图片和三星堆一点关系都没有」
坏样本特征：
  1. `w, a, r, m, , m, u, t, e, d, , g, o, l, d` —— 字符串被逐字符拆开
  2. 同一句里既有 hammered gold foil 又有 matte oxidized bronze  —— 自相矛盾
  3. `Evidence-based visual cues: 金箔; 质检红线; 哑光质感` —— 混入非视觉标签
"""

import sys

sys.path.insert(0, r"c:\Users\Administrator\Desktop\SXD\SanXIngDui\backend")

from app.agents import lexicon
from app.conversation.agent import build_rule_proposals, rule_intent
from app.quality.objective import effective_family_min
from app.quality.style_profiles import PROFILES

CASES = [
    ("黄金面具在三星堆祭祀坑出土现场，考古档案照片风格", "gold_mask"),
    ("把青铜大立人改成博物馆纪实摄影的风格", "standing_figure"),
    ("修复一件破损玉璋，最小干预修复", "jade_zhang"),
    ("象牙在祭祀坑中的摆放场景", "ivory"),
]

failures: list[str] = []


def _st(prompt: str) -> str:
    """截出 Surface truth 一句，方便肉眼核对。"""
    if "Surface truth:" not in prompt:
        return "(缺失)"
    return prompt.split("Surface truth:")[-1].split(". ")[0].strip()


for message, expected_relic in CASES:
    intent = rule_intent(message)
    relic = lexicon.resolve_relic(intent.get("subject"), message)
    print(f"\n=== {message}")
    print(f"  intent.subject = {intent.get('subject')}  -> relic = {relic.key if relic else None}")
    print(f"  material = {relic.material_spec.label if relic else 'None'}")

    if relic is None or relic.key != expected_relic:
        failures.append(f"{message}: 文物解析为 {relic.key if relic else None}，期望 {expected_relic}")

    proposals = build_rule_proposals(intent, [])
    for proposal in proposals:
        prompt = proposal["prompt"]

        # 1) 逐字符拆分：出现 "x, y, z," 形态的单字母序列
        tokens = [part.strip() for part in prompt.split(",")]
        single_letters = sum(1 for token in tokens if len(token) == 1 and token.isalpha())
        if single_letters >= 5:
            failures.append(f"{proposal['id']}: 出现逐字符拆分（{single_letters} 个单字母 token）")

        # 2) 材质自相矛盾：金器/玉器/象牙的提示词里不得出现氧化青铜的材质断言
        if relic is not None and relic.material != "oxidized_bronze":
            lowered = prompt.lower()
            for banned in ("matte oxidized bronze", "oxidized bronze surface", "granular patina"):
                if banned in lowered:
                    failures.append(
                        f"{proposal['id']}: {relic.material} 的提示词里出现青铜断言 {banned!r}"
                    )

        # 3) 非视觉标签
        for banned in ("质检红线", "学术争议", "评估维度"):
            if banned in prompt:
                failures.append(f"{proposal['id']}: 提示词混入非视觉标签 {banned!r}")

        # 4) Surface truth 必须与该文物材质一致
        if relic is not None:
            expect = relic.material_spec.surface_truth.split(",")[0]
            if expect.lower() not in prompt.lower():
                failures.append(
                    f"{proposal['id']}: Surface truth 缺失材质真相 {expect!r}"
                )

        print(f"    - {proposal['id']} profile={proposal['style_profile']}")
        print(f"      Surface truth 片段: {_st(prompt)}")
        print(f"      cues: {prompt.split('Evidence-based visual cues:')[-1].split('.')[0].strip() if 'Evidence-based visual cues:' in prompt else '(无)'}")

print("\n=== 黄金面具 + photo_real 的色域要求（原先是青铜口径）===")
gold = lexicon.MATERIALS["gold_foil"]
for profile_key in ("photo_real", "archival", "museum_doc"):
    merged = effective_family_min(PROFILES[profile_key], gold)
    print(f"  {profile_key:12s} profile={PROFILES[profile_key].family_min} -> merged={merged}")
    if "green" in merged:
        failures.append(f"{profile_key}: 黄金面具仍被要求出现锈绿（green）")

if failures:
    print("\nMATERIAL CHECK FAIL")
    for item in failures:
        print("  -", item)
    sys.exit(1)
print("\nMATERIAL CHECK PASS")
