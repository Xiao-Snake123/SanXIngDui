"""Qwen2.5-VL QLoRA 微调脚本（PEFT + bitsandbytes 4bit）。

训练目标
--------
在 7B 基座上用一个轻量 adapter，把两件事同时做好：
1. **形制记录**（描述型）：稳定输出结构化的形制/纹饰/材质/残损字段；
2. **风格质检**（判别型）：五维打分 + 时代错配识别 + 可执行的修改指令。

选 QLoRA 而不是全参微调的原因很直接：7B 全参需要 ≥60GB 显存，
而 4bit NF4 量化 + LoRA(r=16) 只需约 12~16GB，消费级 4090 就能跑完，
并且产出的 adapter 只有几十 MB，便于随项目分发。

几个容易踩坑但很关键的点
------------------------
1. **只在 assistant 片段上算 loss**：多模态指令微调里，如果对图像 token 和
   用户提问也算 loss，模型会去「复述图片特征」而不是学回答范式。
   本脚本用 chat template 构造 prompt-only 序列来确定 assistant 的起始位置，
   把前面的 label 全部置 -100。
2. **多图 batch 的 pixel_values 拼接**：Qwen2-VL 系列把 patch 展平后放进
   `pixel_values`，batch 时按第 0 维 cat，同时 cat `image_grid_thw` 记录每张图
   的网格形状。搞错会导致图像和文本错位。
3. **不要冻结 vision encoder 后再指望识别能力提升**：本脚本默认
   `--train-vision` 关闭（省显存），但如果你要提升对三星堆专有纹饰的辨识粒度，
   应打开它并降低学习率。

用法
----
    # 1) 生成数据集
    python -m training.prepare_dataset --manifest data/raw/labels.jsonl \
        --image-root data/raw/images --output training/data/sxd_qlora.jsonl

    # 2) 训练
    python -m training.train_qlora \
        --data training/data/sxd_qlora.jsonl \
        --val-data training/data/sxd_qlora_val.jsonl \
        --output training/out/sxd-qwen25vl-lora

    # 3) 推理验证
    python -m training.inference_qlora --adapter training/out/sxd-qwen25vl-lora \
        --image data/raw/images/tree_0001.jpg
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class TrainConfig:
    base_model: str = "Qwen/Qwen2.5-VL-7B-Instruct"
    data: Path = BACKEND_ROOT / "training" / "data" / "sxd_qlora.jsonl"
    val_data: Path | None = None
    output: Path = BACKEND_ROOT / "training" / "out" / "sxd-qwen25vl-lora"
    epochs: float = 2.0
    batch_size: int = 1
    grad_accum: int = 8
    lr: float = 1e-4
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    max_length: int = 3072
    warmup_ratio: float = 0.03
    logging_steps: int = 5
    save_steps: int = 100
    seed: int = 42
    train_vision: bool = False
    min_pixels: int = 256 * 28 * 28
    max_pixels: int = 1280 * 28 * 28
    resume: bool = False


def _require(package: str, hint: str):
    try:
        return __import__(package)
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的友好提示
        raise SystemExit(
            f"缺少依赖 {package}。请先执行：\n"
            f"    pip install -r backend/requirements-train.txt\n{hint}"
        ) from exc


def parse_args() -> TrainConfig:
    parser = argparse.ArgumentParser(description="Qwen2.5-VL QLoRA 微调")
    parser.add_argument("--base-model", default=TrainConfig.base_model)
    parser.add_argument("--data", type=Path, default=TrainConfig.data)
    parser.add_argument("--val-data", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=TrainConfig.output)
    parser.add_argument("--epochs", type=float, default=TrainConfig.epochs)
    parser.add_argument("--batch-size", type=int, default=TrainConfig.batch_size)
    parser.add_argument("--grad-accum", type=int, default=TrainConfig.grad_accum)
    parser.add_argument("--lr", type=float, default=TrainConfig.lr)
    parser.add_argument("--lora-r", type=int, default=TrainConfig.lora_r)
    parser.add_argument("--lora-alpha", type=int, default=TrainConfig.lora_alpha)
    parser.add_argument("--lora-dropout", type=float, default=TrainConfig.lora_dropout)
    parser.add_argument("--max-length", type=int, default=TrainConfig.max_length)
    parser.add_argument("--save-steps", type=int, default=TrainConfig.save_steps)
    parser.add_argument("--logging-steps", type=int, default=TrainConfig.logging_steps)
    parser.add_argument("--seed", type=int, default=TrainConfig.seed)
    parser.add_argument(
        "--train-vision",
        action="store_true",
        help="同时训练 vision encoder（提升专有纹饰辨识，显存开销明显增大）",
    )
    parser.add_argument("--min-pixels", type=int, default=TrainConfig.min_pixels)
    parser.add_argument("--max-pixels", type=int, default=TrainConfig.max_pixels)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    return TrainConfig(
        base_model=args.base_model,
        data=args.data,
        val_data=args.val_data,
        output=args.output,
        epochs=args.epochs,
        batch_size=args.batch_size,
        grad_accum=args.grad_accum,
        lr=args.lr,
        lora_r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        max_length=args.max_length,
        save_steps=args.save_steps,
        logging_steps=args.logging_steps,
        seed=args.seed,
        train_vision=args.train_vision,
        min_pixels=args.min_pixels,
        max_pixels=args.max_pixels,
        resume=args.resume,
    )


def load_samples(path: Path) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


class QwenVLCollator:
    """把多模态样本打成 batch，并做 assistant-only loss 掩码。"""

    def __init__(self, processor, max_length: int) -> None:
        self.processor = processor
        self.tokenizer = processor.tokenizer
        self.max_length = max_length

    def __call__(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        import torch
        from qwen_vl_utils import process_vision_info

        features: list[dict[str, Any]] = []

        for sample in batch:
            messages = sample["messages"]
            prompt_messages = messages[:-1]

            full_text = self.processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=False
            )
            prompt_text = self.processor.apply_chat_template(
                prompt_messages, tokenize=False, add_generation_prompt=True
            )
            image_inputs, video_inputs = process_vision_info(messages)

            full = self.processor(
                text=[full_text],
                images=image_inputs or None,
                videos=video_inputs or None,
                return_tensors="pt",
                padding=False,
            )
            prompt = self.processor(
                text=[prompt_text],
                images=image_inputs or None,
                videos=video_inputs or None,
                return_tensors="pt",
                padding=False,
            )

            input_ids = full["input_ids"][0]
            prompt_len = min(prompt["input_ids"].shape[1], input_ids.shape[0])

            labels = input_ids.clone()
            labels[:prompt_len] = -100  # 只对 assistant 片段计算损失

            if input_ids.shape[0] > self.max_length:
                input_ids = input_ids[: self.max_length]
                labels = labels[: self.max_length]

            feature = {
                "input_ids": input_ids,
                "attention_mask": torch.ones_like(input_ids),
                "labels": labels,
            }
            for key in ("pixel_values", "image_grid_thw", "pixel_values_videos", "video_grid_thw"):
                if key in full:
                    feature[key] = full[key][0] if full[key].dim() > 1 else full[key]
            features.append(feature)

        padded = self.tokenizer.pad(
            [{"input_ids": f["input_ids"], "attention_mask": f["attention_mask"]} for f in features],
            padding=True,
            return_tensors="pt",
        )

        max_len = padded["input_ids"].shape[1]
        labels = torch.full((len(features), max_len), -100, dtype=torch.long)
        for index, feature in enumerate(features):
            length = feature["labels"].shape[0]
            labels[index, :length] = feature["labels"]

        result: dict[str, Any] = {
            "input_ids": padded["input_ids"],
            "attention_mask": padded["attention_mask"],
            "labels": labels,
        }
        for key in ("pixel_values", "pixel_values_videos"):
            parts = [f[key] for f in features if key in f]
            if parts:
                result[key] = torch.cat(parts, dim=0)
        for key in ("image_grid_thw", "video_grid_thw"):
            parts = [f[key] for f in features if key in f]
            if parts:
                result[key] = torch.cat([p.view(-1, p.shape[-1]) for p in parts], dim=0)
        return result


def main() -> None:
    config = parse_args()

    torch = _require("torch", "")
    _require("transformers", "")
    _require("peft", "")
    _require("bitsandbytes", "")

    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoProcessor,
        BitsAndBytesConfig,
        Qwen2_5_VLForConditionalGeneration,
        Trainer,
        TrainingArguments,
    )

    if not config.data.is_file():
        raise SystemExit(
            f"找不到训练数据 {config.data}\n"
            "请先运行：python -m training.prepare_dataset --manifest <你的标注清单>"
        )

    print(f"基座模型 : {config.base_model}")
    print(f"训练数据 : {config.data}")
    print(f"输出目录 : {config.output}")

    compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16

    processor = AutoProcessor.from_pretrained(
        config.base_model,
        min_pixels=config.min_pixels,
        max_pixels=config.max_pixels,
    )
    processor.tokenizer.padding_side = "right"

    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,   # 二次量化，再省约 0.4 bit/参数
        bnb_4bit_compute_dtype=compute_dtype,
    )

    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        config.base_model,
        quantization_config=quantization,
        torch_dtype=compute_dtype,
        device_map="auto",
        attn_implementation="sdpa",
    )
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model.config.use_cache = False

    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
    if config.train_vision:
        # 视觉塔的注意力层命名不同，需显式加入
        target_modules += ["qkv", "proj", "fc1", "fc2"]

    lora_config = LoraConfig(
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=target_modules,
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    train_samples = load_samples(config.data)
    val_samples = load_samples(config.val_data) if config.val_data and config.val_data.is_file() else []

    collator = QwenVLCollator(processor, config.max_length)
    config.output.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(config.output),
        num_train_epochs=config.epochs,
        per_device_train_batch_size=config.batch_size,
        per_device_eval_batch_size=config.batch_size,
        gradient_accumulation_steps=config.grad_accum,
        learning_rate=config.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=config.warmup_ratio,
        logging_steps=config.logging_steps,
        save_steps=config.save_steps,
        save_total_limit=3,
        bf16=compute_dtype == torch.bfloat16,
        fp16=compute_dtype == torch.float16,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        optim="paged_adamw_8bit",       # 分页优化器，避免显存峰值 OOM
        report_to=["none"],             # 需要时改成 ["wandb"]
        remove_unused_columns=False,    # 多模态字段必须保留
        dataloader_num_workers=2,
        eval_strategy="steps" if val_samples else "no",
        eval_steps=config.save_steps if val_samples else None,
        save_strategy="steps",
        seed=config.seed,
        data_seed=config.seed,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_samples,
        eval_dataset=val_samples or None,
        data_collator=collator,
    )

    trainer.train(resume_from_checkpoint=config.resume or None)

    adapter_dir = config.output / "final"
    trainer.save_model(str(adapter_dir))
    processor.save_pretrained(str(adapter_dir))

    (adapter_dir / "training_manifest.json").write_text(
        json.dumps(
            {
                "base_model": config.base_model,
                "train_samples": len(train_samples),
                "val_samples": len(val_samples),
                "lora_r": config.lora_r,
                "lora_alpha": config.lora_alpha,
                "target_modules": target_modules,
                "epochs": config.epochs,
                "lr": config.lr,
                "train_vision": config.train_vision,
                "max_length": config.max_length,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("=" * 70)
    print(f"adapter 已保存: {adapter_dir}")
    print("接下来：把 backend/.env 配成")
    print("    LORA_ENABLED=true")
    print(f"    LORA_BASE_MODEL={config.base_model}")
    print(f"    LORA_ADAPTER_PATH={adapter_dir}")
    print("=" * 70)


if __name__ == "__main__":
    sys.exit(main())
