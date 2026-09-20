"""诊断「回炉为什么无效 / 为什么 give_up」。

强制走本地占位图通道，跑一次会失败的复原任务，逐轮打印：
  * 提示词哈希与长度（回炉真的改了提示词吗？）
  * 提示词相对于上一轮的「新增部分」（回炉指令到底加在哪）
  * 生成的图像字节哈希（两轮出来的图是不是同一张）
  * 质检得分

把「提示词变了但图没变」与「提示词根本没变」区分开，才能定位责任在谁。
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402

# 强制占位通道：本脚本要复现的正是「没有任何真实出图通道」这一档
settings.freeimage_enabled = False
settings.dashscope_api_key = ""
settings.max_revisions = 2

from app.graph.runner import run_once  # noqa: E402

REQUEST = {
    "kind": "scene",
    "item": "黄金面具",
    "identity": "大祭司",
    "scene": "三星堆祭祀坑",
    "style": "考古档案照片",
    "prompt_override": {
        "prompt": (
            "Key artefact: the gold mask of Sanxingdui, hammered gold foil. "
            "Setting: an excavated sacrificial pit, layered soil strata. "
            "Style: archaeological archival photograph, flat even lighting. "
            "Surface truth: warm muted hammered gold, thin and slightly dull. "
            "This tail segment exists only to make the prompt long enough that the "
            "revision directives clearly land beyond the placeholder's display window. "
        ),
        "profile_key": "archival",
        "proposal_id": "scene:context",
        "proposal_title": "出土语境",
    },
}


def _hash_file(relative: str) -> tuple[str, int]:
    path = BACKEND / relative.lstrip("/").replace("media/", "var/outputs/")
    if not path.exists():
        return "(missing)", 0
    data = path.read_bytes()
    return hashlib.blake2b(data, digest_size=6).hexdigest(), len(data)


def main() -> int:
    result = asyncio.run(run_once(REQUEST))

    print("=" * 78)
    print("逐轮诊断")
    print("=" * 78)

    history = result.get("revision_history") or []
    previous_prompt = ""
    for index, item in enumerate(history):
        prompt = str(item.get("prompt") or "")
        digest = hashlib.blake2b(prompt.encode("utf-8"), digest_size=6).hexdigest()
        image_hash, image_bytes = _hash_file(str(item.get("image_url") or ""))

        print(f"\n[第 {index} 轮]")
        print(f"  provider      = {item.get('provider')}")
        print(f"  prompt 长度   = {len(prompt)}  hash={digest}")
        print(f"  图像          = {item.get('image_url')}  hash={image_hash}  {image_bytes} 字节")
        if index == 0:
            print("  （首轮基准）")
        else:
            added = prompt[len(previous_prompt):] if prompt.startswith(previous_prompt) else "(整段重写)"
            print(f"  新增指令      = {added[:200]!r}")
            if image_hash == _hash_file(str(history[index - 1].get('image_url') or ''))[0]:
                print("  ⚠ 与上一轮图像逐字节相同")
        previous_prompt = prompt

    qa = result.get("qa") or {}
    print("\n" + "=" * 78)
    print(f"最终：qa.passed={qa.get('passed')} score={qa.get('score')} "
          f"decision={qa.get('decision')} reason={qa.get('decision_reason')}")
    print(f"回炉次数：{result.get('revisions')}")
    print(f"质检意见（{len(qa.get('feedback') or [])} 条）：")
    for item in (qa.get("feedback") or [])[:6]:
        print(f"  · {item}")

    # 结论判定
    print("\n" + "=" * 78)
    if len(history) >= 2:
        first_hash = _hash_file(str(history[0].get("image_url") or ""))[0]
        second_hash = _hash_file(str(history[1].get("image_url") or ""))[0]
        print(f"两轮图像是否相同：{first_hash == second_hash}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
