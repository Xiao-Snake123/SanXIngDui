"""QLoRA adapter 推理验证脚本。

用途有两类：
1. **训练后验收**：对单张图跑三类任务，肉眼确认 adapter 真的学到了东西
   （这一步不能省 —— loss 下降不等于能力提升，多模态微调尤其如此）；
2. **批量回归**：对一批图跑 `style_judge`，把打分与线上 Agent 的质检结果对比，
   衡量「本地 7B+LoRA」与「云端大 VLM」之间的差距，决定是否值得切到本地。

用法
----
    # 单图三任务
    python -m training.inference_qlora --adapter training/out/sxd-qwen25vl-lora/final \
        --image data/raw/images/tree_0001.jpg --task all

    # 批量质检回归
    python -m training.inference_qlora --adapter ... --batch data/raw/images \
        --task style_judge --output var/metrics/lora_regression.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.models.llm import image_part, text_part  # noqa: E402
from app.models.local_vlm import LocalQwenVL  # noqa: E402
from training.prepare_dataset import (  # noqa: E402
    FORM_PROMPT,
    JUDGE_PROMPT_TEMPLATE,
    RESTORE_PROMPT,
)

TASK_PROMPTS = {
    "form_extract": FORM_PROMPT,
    "style_judge": JUDGE_PROMPT_TEMPLATE.format(
        style_label="博物馆纪实摄影",
        rubric=(
            "1) 器表是否为哑光且带颗粒感锈层；2) 色彩是否落在青铜锈绿—土褐—金黄区间；"
            "3) 形制是否与实物一致；4) 是否存在时代错配元素。"
        ),
        metrics={},
    ),
    "restore_advice": RESTORE_PROMPT,
}


async def run_one(runner: LocalQwenVL, image: str, task: str) -> dict[str, Any]:
    messages = [
        {
            "role": "user",
            "content": [text_part(TASK_PROMPTS[task]), image_part(image)],
        }
    ]
    result = await runner.chat(messages, max_new_tokens=900, temperature=0.0)

    parsed: Any
    try:
        from app.models.llm import _loads_lenient  # noqa: PLC2701

        parsed = _loads_lenient(result.text)
    except Exception:  # noqa: BLE001 - 解析失败也算有效观测，如实记录
        parsed = None

    return {
        "image": image,
        "task": task,
        "latency_ms": round(result.latency_ms, 1),
        "json_valid": parsed is not None,
        "parsed": parsed,
        "raw": result.text[:1200],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="QLoRA adapter 推理验证")
    parser.add_argument("--adapter", type=Path, required=True, help="adapter 目录（训练输出的 final/）")
    parser.add_argument("--base-model", default=None, help="覆盖基座模型路径")
    parser.add_argument("--image", type=Path, help="单张图片")
    parser.add_argument("--batch", type=Path, help="批量模式：图片目录")
    parser.add_argument(
        "--task",
        default="all",
        choices=[*TASK_PROMPTS.keys(), "all"],
    )
    parser.add_argument("--output", type=Path, default=None, help="批量模式的结果输出 JSONL")
    parser.add_argument("--limit", type=int, default=50, help="批量模式最多处理多少张")
    args = parser.parse_args()

    settings.lora_enabled = True
    settings.lora_adapter_path = str(args.adapter)
    if args.base_model:
        settings.lora_base_model = args.base_model

    runner = await LocalQwenVL.instance()
    if not await runner.available():
        print(
            "本地 VLM 不可用。请检查：\n"
            "  1) 是否已安装训练依赖（pip install -r backend/requirements-train.txt）\n"
            f"  2) adapter 目录是否存在：{args.adapter}\n"
            "  3) 是否有可用的 CUDA 设备",
            file=sys.stderr,
        )
        return 1

    tasks = list(TASK_PROMPTS) if args.task == "all" else [args.task]

    if args.image:
        for task in tasks:
            outcome = await run_one(runner, str(args.image), task)
            print("=" * 70)
            print(f"task={task}  latency={outcome['latency_ms']}ms  json_valid={outcome['json_valid']}")
            print(json.dumps(outcome["parsed"], ensure_ascii=False, indent=2) if outcome["parsed"] else outcome["raw"])
        return 0

    if not args.batch:
        parser.error("必须提供 --image 或 --batch")

    extensions = {".jpg", ".jpeg", ".png", ".webp"}
    images = sorted(
        path for path in args.batch.rglob("*") if path.suffix.lower() in extensions
    )[: args.limit]
    if not images:
        print(f"目录中没有图片: {args.batch}", file=sys.stderr)
        return 1

    outputs: list[dict[str, Any]] = []
    valid = 0
    for index, image in enumerate(images, start=1):
        for task in tasks:
            outcome = await run_one(runner, str(image), task)
            outputs.append(outcome)
            valid += int(outcome["json_valid"])
        print(f"[{index}/{len(images)}] {image.name}", flush=True)

    total = len(outputs)
    latencies = [item["latency_ms"] for item in outputs]
    summary = {
        "images": len(images),
        "calls": total,
        "json_valid_rate": round(valid / total, 4) if total else None,
        "avg_latency_ms": round(sum(latencies) / total, 1) if total else None,
        "p95_latency_ms": sorted(latencies)[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else None,
    }
    print("=" * 70)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as handle:
            for item in outputs:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"结果已写入: {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
