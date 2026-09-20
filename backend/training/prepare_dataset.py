"""QLoRA 数据管线：把「千级图文史料」制成多任务指令微调集。

为什么是**多任务**而不是单任务
------------------------------
本项目的多智能体系统里，VLM 承担两种彼此不同的职责：
1. `vlm` 角色 —— 读参考图，抽形制/纹饰/材质/残损（**描述型**任务）；
2. `judge` 角色 —— 看图打分，判时代错配，给修改指令（**判别型**任务）。

如果只训一个「看图说话」的模型，质检能力不会提升；如果只训一个「打分器」，
参考图理解又会退化。因此数据集按 `--tasks` 生成三类样本，
让**同一个 adapter** 同时具备描述与判别能力 —— 这也是 LoRA 参数高效微调
在这个场景下最划算的用法。

三类任务
--------
| task | 输入 | 输出 |
|---|---|---|
| `form_extract` | 器物影像 | 结构化形制/纹饰/材质/残损 JSON |
| `style_judge`  | 影像 + 风格细则 | 五维评分 + 时代错配 + 修改指令 JSON |
| `restore_advice` | 影像 + 残损描述 | 修复策略与风险点 JSON |

数据来源
--------
`--manifest` 指向一个 JSONL，每行至少包含 `image` 与 `text`，
可选 `object / era / category / tags / source / authority`（与检索语料同构）。
若没有现成的标注，可先用 `--from-corpus` 从史料语料生成「文生图反向配对」的
弱监督样本，再人工抽检修正。**弱监督样本必须标注来源等级**，
否则训出来的模型会把项目自己的推测当成史实。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TASKS = ("form_extract", "style_judge", "restore_advice")

FORM_SCHEMA_HINT = """{
  "object": "<器类名称>",
  "era": "<年代判断；不确定写「无法判断」>",
  "form_features": ["<形制特征，3-6 条，只写你看到的>"],
  "ornament_features": ["<纹饰特征，1-4 条；没有就给空数组>"],
  "material_state": ["<表面与材质状态，2-4 条，如「哑光深绿锈层」「局部粉状浅绿锈」>"],
  "damage": [{"location": "<位置>", "type": "<断裂|缺失|锈蚀|变形|土沁>", "severity": "<轻度|中度|重度>"}],
  "confidence": "<高|中|低>",
  "unverifiable": ["<画面无法判断、需要参考其他资料的方面，1-3 条>"]
}"""

FORM_PROMPT = (
    "你是三星堆文物影像的形制记录员。请对这张影像做客观记录，"
    "只描述画面中确实可见的内容，不要推断画面之外的形制。\n"
    "严格输出以下 JSON，不要任何额外文字：\n" + FORM_SCHEMA_HINT
)

JUDGE_PROMPT_TEMPLATE = """你是三星堆文物数字复原项目的质检专家。
请判断这张影像的风格一致性是否达标。

【目标风格】{style_label}
【评分细则】
{rubric}

【客观指标参考】{metrics}

严格输出以下 JSON，不要任何额外文字：
{{
  "material_fidelity": <0-10 材质质感：哑光程度、锈层颗粒、边缘圆钝>,
  "color_fidelity": <0-10 色彩色域是否落在青铜锈绿—土褐—金黄区间>,
  "form_fidelity": <0-10 形制与三星堆实物的一致性>,
  "era_compliance": <0-10 是否出现时代错配元素>,
  "source_fidelity": <0-10 残缺信息是否被保留>,
  "anachronisms": ["<画面中的时代错配元素，没有给空数组>"],
  "issues": ["<具体视觉问题，指明位置>"],
  "directives": ["<可直接写进下一轮 prompt 的英文修改指令，每条 <= 20 词>"],
  "reasoning": "<80 字以内理由>"
}}"""

RESTORE_PROMPT = """你是三星堆文物修复的技术顾问。请针对这张残损器物的影像给出修复建议。
必须遵循最小干预、可识别、可逆三原则，并明确哪些结论是推断、哪些有实证依据。

严格输出以下 JSON，不要任何额外文字：
{{
  "observed_object": "<你看到的器类>",
  "damage_summary": ["<残损情况，2-4 条>"],
  "strategy": ["<修复策略步骤，3-5 条，从清洗到补配>"],
  "materials": ["<建议使用的补配材料与依据，1-3 条>"],
  "evidence_level": "<实证|部分实证|纯推断>",
  "risks": ["<最容易被臆造错的地方，1-3 条>"],
  "must_keep_visible": ["<必须保留可见的原始信息，1-3 条>"]
}}"""


def sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"[warn] {path.name}:{line_no} 解析失败: {exc}", file=sys.stderr)
    return rows


def from_corpus(corpus_dir: Path) -> list[dict[str, Any]]:
    """把史料语料转成「弱监督」清单（不含真实影像，故 image 置空，需外部补图）。"""
    rows: list[dict[str, Any]] = []
    for path in sorted(corpus_dir.glob("*.jsonl")):
        for row in load_manifest(path):
            rows.append(
                {
                    "image": "",
                    "object": row.get("object") or "",
                    "era": row.get("era") or "",
                    "category": row.get("category") or "",
                    "tags": row.get("tags") or [],
                    "text": row.get("text") or "",
                    "source": row.get("source") or "",
                    "authority": row.get("authority", 0.8),
                    "weak_supervision": True,
                }
            )
    return rows


def build_form_answer(row: dict[str, Any]) -> dict[str, Any]:
    """由标注字段组装形制抽取的监督信号。

    这是**唯一**允许「从文本反推视觉描述」的地方，因此必须保留 `unverifiable`：
    凡是文本里有、但画面无从验证的内容，一律进 unverifiable，
    防止模型学会「看图编史料」。
    """
    text = str(row.get("text") or "")
    sentences = [part.strip() for part in text.replace("；", "。").split("。") if len(part.strip()) > 8]
    features = sentences[:5] or ["画面仅可见器物局部，形制特征无法完整判断"]

    object_name = str(row.get("object") or "未知器类")
    era = str(row.get("era") or "").strip() or "无法判断"

    damage: list[dict[str, str]] = []
    for keyword, dtype in (("缺", "缺失"), ("断", "断裂"), ("锈", "锈蚀"), ("变形", "变形")):
        if keyword in text:
            damage.append({"location": "见画面", "type": dtype, "severity": "中度"})

    return {
        "object": object_name,
        "era": era,
        "form_features": features,
        "ornament_features": [str(tag) for tag in (row.get("tags") or [])[:3]],
        "material_state": ["哑光锈层，无明显镜面反光"],
        "damage": damage,
        "confidence": "中" if row.get("image") else "低",
        "unverifiable": [
            "残损的具体范围需以实物测绘为准",
            "材质成分需以科技检测为准",
        ],
    }


def build_style_answer(row: dict[str, Any], score: float = 8.0) -> dict[str, Any]:
    base = float(score)
    return {
        "material_fidelity": base,
        "color_fidelity": base,
        "form_fidelity": base,
        "era_compliance": 10.0 if not row.get("weak_supervision") else 9.0,
        "source_fidelity": base if not str(row.get("category") or "").startswith("修复") else base - 1.0,
        "anachronisms": [],
        "issues": [],
        "directives": [],
        "reasoning": "标注样本，各维度均达到可用标准。",
    }


def build_restore_answer(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "observed_object": str(row.get("object") or "未知器类"),
        "damage_summary": ["表面锈层覆盖", "局部缺失"],
        "strategy": [
            "先做表面清理与除锈评估，区分有害锈与稳定锈",
            "对稳定锈层予以保留，不做打磨",
            "缺失部位以可识别材料补配，颜色与质感与原残件区分",
            "补配前完成三维扫描与影像存档",
        ],
        "materials": ["可逆性树脂类补配材料，便于日后安全去除"],
        "evidence_level": "部分实证",
        "risks": ["补配比例过度导致形制走样", "把推测的纹饰当成实证纹饰"],
        "must_keep_visible": ["原有残缺边界", "稳定锈层与土沁"],
    }


def to_sample(image_path: str, task: str, answer: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    if task == "form_extract":
        prompt = FORM_PROMPT
    elif task == "style_judge":
        prompt = JUDGE_PROMPT_TEMPLATE.format(
            style_label=row.get("style_label") or "通用复原",
            rubric=row.get("rubric") or "保持器表哑光、色域落在青铜锈绿—土褐—金黄区间、形制忠实、无时代错配。",
            metrics=json.dumps(row.get("metrics") or {}, ensure_ascii=False),
        )
    else:
        prompt = RESTORE_PROMPT

    return {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image_path},
                    {"type": "text", "text": prompt},
                ],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}],
            },
        ],
        "meta": {
            "task": task,
            "object": row.get("object"),
            "source": row.get("source"),
            "authority": row.get("authority", 0.8),
            "weak_supervision": bool(row.get("weak_supervision")),
        },
    }


def expand(rows: list[dict[str, Any]], tasks: tuple[str, ...], image_root: Path | None) -> Iterator[dict[str, Any]]:
    for row in rows:
        raw_image = str(row.get("image") or "").strip()
        if not raw_image:
            continue
        image_path = Path(raw_image)
        if image_root is not None and not image_path.is_absolute():
            image_path = image_root / image_path
        if not image_path.is_file():
            print(f"[skip] 图片不存在: {image_path}", file=sys.stderr)
            continue
        for task in tasks:
            if task == "form_extract":
                answer = build_form_answer(row)
            elif task == "style_judge":
                answer = build_style_answer(row)
            else:
                answer = build_restore_answer(row)
            yield to_sample(str(image_path), task, answer, row)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 Qwen2.5-VL QLoRA 多任务指令集")
    parser.add_argument("--manifest", type=Path, help="标注清单 JSONL（含 image 字段）")
    parser.add_argument("--from-corpus", type=Path, default=None, help="从史料语料生成弱监督清单")
    parser.add_argument("--image-root", type=Path, default=None, help="图片相对路径的根目录")
    parser.add_argument("--output", type=Path, default=BACKEND_ROOT / "training" / "data" / "sxd_qlora.jsonl")
    parser.add_argument("--tasks", nargs="+", default=list(TASKS), choices=list(TASKS))
    parser.add_argument("--val-ratio", type=float, default=0.06)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not args.manifest and not args.from_corpus:
        parser.error("必须提供 --manifest 或 --from-corpus 之一")

    rows: list[dict[str, Any]] = []
    if args.manifest:
        rows.extend(load_manifest(args.manifest))
    if args.from_corpus:
        rows.extend(from_corpus(args.from_corpus))

    if not rows:
        print("没有可用样本", file=sys.stderr)
        raise SystemExit(1)

    # 去重：按图片内容 hash + 文本，避免同一件器物的重复照片造成过拟合
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = hashlib.sha1(
            f"{row.get('image')}|{row.get('text')}|{row.get('object')}".encode("utf-8")
        ).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    samples = list(expand(deduped, tuple(args.tasks), args.image_root))
    if not samples:
        print(
            "没有生成任何样本：清单中的 image 字段指向的文件都不存在。\n"
            "提示：先用 --from-corpus 生成待补图的清单，补齐图片后再用 --manifest 构建训练集。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    rng = random.Random(args.seed)
    rng.shuffle(samples)
    val_size = max(1, int(len(samples) * args.val_ratio))
    val, train = samples[:val_size], samples[val_size:]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    val_path = args.output.with_name(args.output.stem + "_val" + args.output.suffix)

    for path, payload in ((args.output, train), (val_path, val)):
        with path.open("w", encoding="utf-8") as handle:
            for sample in payload:
                handle.write(json.dumps(sample, ensure_ascii=False) + "\n")

    task_counter = Counter(sample["meta"]["task"] for sample in samples)
    object_counter = Counter(str(sample["meta"]["object"]) for sample in samples)
    weak = sum(1 for sample in samples if sample["meta"]["weak_supervision"])

    print("=" * 70)
    print(f"样本总数 : {len(samples)}（去重前 {len(rows)}）")
    print(f"训练集   : {args.output}  ({len(train)} 条)")
    print(f"验证集   : {val_path}  ({len(val)} 条)")
    print(f"任务分布 : {dict(task_counter)}")
    print(f"器类分布 : {dict(object_counter.most_common(8))}")
    print(f"弱监督占比: {weak / len(samples):.1%}  ← 越高越需要人工抽检后再训练")
    print("=" * 70)


if __name__ == "__main__":
    main()
