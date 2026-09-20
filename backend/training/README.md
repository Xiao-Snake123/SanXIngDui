# QLoRA 微调：三星堆文物视觉理解与风格质检

本目录用 **Qwen2.5-VL-7B-Instruct + QLoRA（4bit NF4 + LoRA r=16）** 训练一个轻量
adapter，在系统里承担 `vlm` 与 `judge` 两个角色。

## 为什么需要它

| 维度 | 纯 API（qwen3-vl-plus） | 本地 7B + QLoRA |
|---|---|---|
| 成本模型 | 按图计费，评估批量跑一次就上百元 | 固定 GPU 成本 |
| 数据合规 | 文物影像需出内网 | 数据不出内网 |
| 专有纹饰辨识 | 通用模型对「纵目」「分铸接缝」粒度不足 | 可用三星堆语料专门强化 |
| 延迟 | 网络 RTT + 排队，2~4s | 4bit 7B 约 1.5~2.5s（4090） |
| 可解释性 | 黑盒 | 可看到 adapter 训练数据与 loss 曲线 |

生产上两者是**热备关系**：本地可用走本地，本地挂了自动回落 API
（见 `app/models/registry.py::_try_local_vlm`），对上层 Agent 完全透明。

## 硬件要求

| 配置 | 可行性 |
|---|---|
| 24GB（4090 / A5000） | ✅ 推荐：bs=1, grad_accum=8, max_length=3072 |
| 16GB（4060Ti 16G / A4000） | ⚠️ 需把 `--max-length` 降到 2048、`--max-pixels` 减半 |
| 12GB（3060） | ⚠️ 需 `--lora-r 8` 且关闭 `--train-vision`，序列长度 ≤1536 |
| 无 GPU | ❌ 训练不可行（推理亦不可行），系统会自动使用 API 通道 |

## 全流程

### 1) 准备标注清单

`data/raw/labels.jsonl`，每行一张图：

```json
{"image": "tree_0001.jpg", "object": "青铜神树", "era": "商代晚期", "category": "形制", "tags": ["九枝", "九鸟"], "text": "一号大型青铜神树修复后通高约396厘米……", "source": "三星堆博物馆馆藏说明", "authority": 1.0}
```

`text` 与检索语料 `backend/data/corpus/*.jsonl` 同构，可复用。

### 2) 生成多任务训练集

```bash
cd backend
python -m training.prepare_dataset \
  --manifest data/raw/labels.jsonl \
  --image-root data/raw/images \
  --tasks form_extract style_judge restore_advice \
  --output training/data/sxd_qlora.jsonl
```

三类任务样本共用一个 adapter：

| task | 学到的能力 | 在系统中的用途 |
|---|---|---|
| `form_extract` | 结构化形制/纹饰/材质/残损记录 | `vlm` 角色读参考图 |
| `style_judge` | 五维打分 + 时代错配 + 修改指令 | `judge` 角色做质检 |
| `restore_advice` | 修复策略与风险点 | 文物修复 Tab 的策略建议 |

> ⚠️ 脚本会统计 **弱监督占比**。若清单里没有真实影像（用史料文本反向构造），
> 该比例会很高。**弱监督样本必须人工抽检后才能用于训练**，
> 否则模型会把项目自己的推测学成「事实」。

### 3) 训练

```bash
python -m training.train_qlora \
  --data training/data/sxd_qlora.jsonl \
  --val-data training/data/sxd_qlora_val.jsonl \
  --output training/out/sxd-qwen25vl-lora \
  --epochs 2 --lora-r 16 --lora-alpha 32 \
  --batch-size 1 --grad-accum 8 --lr 1e-4
```

关键实现点（详见脚本内注释）：

- **assistant-only loss**：用 chat template 构造 prompt-only 序列确定助手片段起点，
  其余 label 置 `-100`，避免模型学成「复述图片特征」。
- **多图 batch 拼接**：`pixel_values` 按第 0 维 cat，同时 cat `image_grid_thw`，
  保证图像与文本不错位。
- **`prepare_model_for_kbit_training` + gradient checkpointing + paged_adamw_8bit**：
  把 7B 4bit 训练的显存峰值压到 24GB 以内。

### 4) 验收与回归

```bash
# 单图三任务
python -m training.inference_qlora --adapter training/out/sxd-qwen25vl-lora/final \
  --image data/raw/images/tree_0001.jpg --task all

# 批量质检回归：与线上 Agent 的质检结果对比
python -m training.inference_qlora --adapter training/out/sxd-qwen25vl-lora/final \
  --batch data/raw/images --task style_judge \
  --output var/metrics/lora_regression.jsonl
```

回归关注两个指标：**JSON 合法率**（结构化输出是否稳定）与
**与云端 VLM 判定的一致率**（是否值得切到本地）。

### 5) 接入服务

```bash
# backend/.env
LORA_ENABLED=true
LORA_BASE_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
LORA_ADAPTER_PATH=training/out/sxd-qwen25vl-lora/final
```

重启后端即生效。`GET /api/models` 会显示 `local_finetune.enabled = true`。

## 关于 GLM-4.5V

系统同样支持 GLM-4.5V 作为 `judge` / `vlm` 的备选通道：它和 Qwen 系列**同源不同族**，
在「异构校验」原则下，用 GLM 做裁判、Qwen 作出图理解，能进一步降低
「自己判自己卷」的风险。切换方式是在 `backend/.env` 中改模型名并把
`DASHSCOPE_BASE_URL` 指向对应网关（GLM 走智谱的 OpenAI 兼容端点）。
本仓库的 QLoRA 脚本以 Qwen2.5-VL 为默认基座，是因为它的视觉 token 压缩比
（每图 256~1280 token 可调）更适合「千级图文 + 单卡」的预算。
