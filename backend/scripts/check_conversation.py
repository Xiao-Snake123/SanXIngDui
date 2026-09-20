"""意图分类与方案生成的快速自检（无 Key 规则路径）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.conversation.agent import build_rule_proposals, rule_intent  # noqa: E402

CASES = [
    "我想做个大祭司在青铜神树祭坛前的场景，博物馆纪实摄影风格",
    "还原一个古蜀武士的人物形象，全身像",
    "帮我修复这件破损青铜面具，用最小干预修复",
    "把这张图转成赛博古蜀风格，强度 80%",
    "金杖特写，考古档案照片",
    "青铜纵目面具",
]


def main() -> int:
    failures = 0
    for text in CASES:
        intent = rule_intent(text)
        proposals = build_rule_proposals(intent, [])
        params = [f"{p['title']} {p['params']['width']}x{p['params']['height']}" for p in proposals]
        print(f"输入: {text}")
        print(
            f"  意图: kind={intent['kind']} subject={intent['subject']} "
            f"identity={intent['identity']} scene={intent['scene']} style={intent['style']}"
        )
        print(f"  方案: {params}")
        assert len(proposals) >= 3, "至少要给出 3 个方案"
        prompts = {p["prompt"] for p in proposals}
        assert len(prompts) == len(proposals), "各方案的提示词必须彼此不同"
        assert all(p["rationale"] and p["risk"] for p in proposals)
        print()

    # 期望的意图分类断言
    expectations = [
        ("我想做个大祭司在青铜神树祭坛前的场景", "scene"),
        ("还原一个古蜀武士的人物形象", "figure"),
        ("帮我修复这件破损青铜面具", "artifact"),
        ("把这张图转成赛博古蜀风格", "style"),
    ]
    for text, expected in expectations:
        actual = rule_intent(text)["kind"]
        status = "OK " if actual == expected else "FAIL"
        if actual != expected:
            failures += 1
        print(f"[{status}] {expected:<9} <- {text} (实际 {actual})")

    print()
    print("INTENT CHECK", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
