"""规划 Agent（Planner）—— 把自然语言目标拆解为可执行的子任务 DAG。

设计核心：**规则打底 + LLM 增强**，而不是纯 LLM 规划。

纯 LLM 规划在这个场景下有三个具体问题：
1. 会把「青铜大立人」的外形描述错（把三层交领写成两层、漏掉赤足方座），
   而这类形制错误会一路传导到出图与质检，属于**不可恢复错误**；
2. 无 Key 时整条流水线直接死掉，不符合「未配置也能启动」的要求；
3. 输出不稳定，同一输入两次规划出不同的 query 集合，评估无法复现。

所以这里的分工是：
- **规则层**（`build_rule_plan`）产出确定性的骨架：文物形制要点（来自人工整理的
  词典）、风格 token（来自风格档案）、检索 query 基线、画幅与采样参数。
  这一层在任何环境下都执行，保证下限。
- **LLM 层**（`refine_with_llm`）只做**增量**：补充检索视角、追加艺术化修饰词、
  生成验收标准。它的输出被约束为「只能追加，不能删改形制要点」。
"""

from __future__ import annotations

import json
from typing import Any

from app.agents import lexicon
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.core.tracing import TraceRecorder
from app.models.llm import Message
from app.models.registry import registry
from app.quality.style_profiles import PROFILES, StyleProfile, resolve_profile

logger = get_logger("app.agents.planner")

PROFILES_BY_KEY = {profile.key: profile.label for profile in [*PROFILES.values()]}


def plan_locked(plan: dict[str, Any]) -> bool:
    """该规划是否已锁定用户选定的提示词。"""
    return bool(plan.get("prompt_locked"))

PLANNER_SYSTEM = """你是三星堆文物数字复原项目的**任务规划专家**。
你会收到一份已经由确定性规则生成的规划骨架，你的任务是**在骨架之上做增量补全**，而不是重写它。

硬性约束（违反会被系统拒绝）：
1. 不得修改 `locked_form_notes` 中的任何形制特征描述；
2. 不得引入铁器、瓷器、汉字铭文、佛教元素、明清服饰等后世元素；
3. 检索 query 必须是中文，且每条聚焦一个具体方面（形制/纹饰/工艺/年代/展示方式），
   不要写成「三星堆 介绍」这种过宽的词；
4. 只输出 JSON。"""

PLANNER_TEMPLATE = """【用户请求】
{request_json}

【规则层生成的规划骨架】
{base_json}

【必须保留的形制要点（locked_form_notes）】
{locked}

请输出如下 JSON：
{{
  "goal": "<一句话概括本次复原目标>",
  "extra_queries": ["<补充检索视角，2-5 条中文 query>"],
  "visual_directives": ["<追加到图像 prompt 的英文修饰词，每条不超过 12 词，2-6 条>"],
  "style_constraints": ["<中文风格约束，用于质检参考，2-4 条>"],
  "acceptance_criteria": ["<可判定的验收标准，2-4 条>"],
  "negative_extras": ["<额外的英文负向词，针对本请求可能出现的错误，1-4 条>"]
}}"""


def resolve_subject_relic(request: dict[str, Any]) -> lexicon.RelicSpec | None:
    """把请求里各种可能写文物名的字段统一解析为一条文物记录。

    为什么要在计划里显式带上 `relic_key`：
    质检需要知道「拍的是什么材质」，而它拿到的只有 plan 与 request。
    如果不带 key，它只能拿自由文本（如「三星堆文物」）去猜 —— 这次就猜不到，
    于是回退到青铜口径的色域要求，反过来要求黄金面具补锈绿。
    解析一次、在状态里传递，比让每个下游节点各自猜一遍可靠。
    """
    return lexicon.resolve_relic(
        request.get("item"),
        request.get("artifact"),
        request.get("subject"),
        request.get("prompt"),
    )


def build_rule_plan(request: dict[str, Any]) -> dict[str, Any]:
    kind = str(request.get("kind") or "scene")
    profile = resolve_profile(
        request.get("style"),
        request.get("style_preset"),
        request.get("method"),
    )

    if kind in lexicon.FIGURE_KINDS:
        plan = _figure_plan(request, profile)
    elif kind in lexicon.ARTIFACT_KINDS:
        plan = _artifact_plan(request, profile)
    elif kind in lexicon.STYLE_KINDS:
        plan = _style_plan(request, profile)
    else:
        plan = _scene_plan(request, profile)

    # 统一注入文物标识与材质真相，让下游（出图 / 质检 / 文案）不必再靠文本猜
    relic = resolve_subject_relic(request)
    plan["relic_key"] = relic.key if relic else None
    plan["material_key"] = relic.material if relic else None
    plan["material_label"] = relic.material_spec.label if relic else None
    plan["surface_truth"] = relic.material_spec.surface_truth if relic else None
    return plan


# ── 各场景的骨架 ────────────────────────────────────────────────────────────
def _surface_truth_clause(relic: lexicon.RelicSpec | None, fallback_subject: str) -> str:
    """生成「材质真相」句子。

    文物能解析出来就用它自己的材质；解析不出来时**不能默认青铜** ——
    宁可说「忠实呈现上面描述的材质」，也不要把金器/玉器/人物说成氧化青铜。
    """
    if relic is None:
        return (
            f"Surface truth: faithful rendition of the material of {fallback_subject}, "
            "no invented surface finish."
        )
    return f"Surface truth: {relic.material_spec.surface_truth}."


def _scene_plan(request: dict[str, Any], profile: StyleProfile) -> dict[str, Any]:
    identity = str(request.get("identity") or "大祭司")
    scene = str(request.get("scene") or "博物馆展厅")
    item = str(request.get("item") or "青铜大立人")
    style_label = str(request.get("style") or profile.label)

    relic = lexicon.resolve_relic(item)
    identity_en = lexicon.resolve_identity(identity)
    scene_en = lexicon.resolve_scene(scene)
    relic_block = relic.prompt_block() if relic else f"the artifact described as {item}"

    prompt_parts = [
        f"{identity_en}.",
        f"Setting: {scene_en}.",
        f"Key artifact: {relic_block}",
        "Composition: the artifact occupies the visual anchor, the figure is turned three-quarter "
        "toward it, generous negative space around the object to preserve its mass.",
        f"Style: {', '.join(profile.prompt_tokens)}.",
        # 材质真相跟文物走。这里曾经写死「oxidized bronze green ... granular patina」，
        # 于是「黄金面具」的画面里同时被要求是金箔又是氧化青铜。
        _surface_truth_clause(relic, item),
    ]

    return {
        "goal": f"复原「{identity}」在「{scene}」前与「{item}」同框的{style_label}影像",
        "profile_key": profile.key,
        "profile_label": profile.label,
        "subtasks": [
            {"id": "T1", "agent": "retrieval", "objective": f"检索「{item}」的形制、纹饰与年代证据"},
            {"id": "T2", "agent": "restoration", "objective": "依据证据生成复原影像"},
            {"id": "T3", "agent": "quality", "objective": "校验风格一致性与时代合规"},
            {"id": "T4", "agent": "copywriting", "objective": "撰写配套科普文案"},
        ],
        "retrieval_queries": _dedupe(
            [
                item,
                f"{item} 形制",
                f"{item} 纹饰",
                identity,
                scene,
                f"{item} 年代",
                "三星堆 时代错配 禁忌",
            ]
        ),
        "retrieval_filters": {},
        "image_spec": {
            "prompt": " ".join(prompt_parts),
            "negative_prompt": ", ".join([*profile.negative_tokens]),
            "width": 1024,
            "height": 1280,
            "style_tokens": list(profile.prompt_tokens[:6]),
            # 只写真实生效的参数。steps/cfg/lora_strength 已被移除：
            # 它们是 ComfyUI 采样器旋钮，API 模型不接受，留着只会误导。
            "prompt_extend": False,
        },
        "locked_form_notes": relic.form_notes if relic else item,
        "style_constraints": [
            f"{profile.label}：保持哑光锈层质感与克制饱和度",
            "器物形制须与出土实物一致，不得臆造部件",
            "画面中不得出现铁器、瓷器、汉字铭文等后世元素",
        ],
        "copy_brief": {
            "audience": "博物馆观众 / 文化爱好者",
            "length": 220,
            "tone": "专业但通俗，避免术语堆砌",
            "must_include": [item, identity],
        },
        "acceptance_criteria": [
            f"器物形制与「{item}」实物特征一致",
            f"画面风格为{profile.label}且色域落在青铜合理区间",
            "无时代错配元素",
        ],
    }


def _figure_plan(request: dict[str, Any], profile: StyleProfile) -> dict[str, Any]:
    gender = str(request.get("gender") or "男性")
    rank = str(request.get("rank") or "贵族")
    era = str(request.get("era") or "鱼凫王朝")
    expression = str(request.get("expression") or "庄严肃穆")
    framing = str(request.get("detail") or "全身像")

    gender_en = {"男性": "male", "女性": "female", "神灵（无性别）": "genderless divine"}.get(
        gender, "unspecified"
    )
    framing_en = {
        "全身像": "full-body shot",
        "半身像": "medium shot from the waist up",
        "面部特写": "tight facial close-up",
        "侧身像": "profile three-quarter side view",
    }.get(framing, "full-body shot")
    expression_en = {
        "庄严肃穆": "solemn and composed, eyes gazing slightly upward",
        "虔诚膜拜": "devout and reverent, head slightly bowed",
        "威严凛冽": "stern and commanding, direct gaze",
        "神秘莫测": "enigmatic, neutral expression with no explicit emotion",
    }.get(expression, "solemn and composed")

    identity_en = lexicon.resolve_identity(rank)
    prompt_parts = [
        f"A {framing_en} of a {gender_en} figure of ancient Shu: {identity_en}.",
        f"Historical period: the {era} of the ancient Shu polity.",
        f"Expression: {expression_en}.",
        "Costume follows Sanxingdui evidence: plain woven hemp or silk, simplified cutting, "
        "cord or leather belt, no later-dynasty tailoring, no embroidery without evidence.",
        f"Style: {', '.join(profile.prompt_tokens)}.",
        "Matte skin and textile rendering, restrained palette, no glossy highlights.",
    ]

    return {
        "goal": f"还原{era}的{gender}{rank}人物形象（{framing}）",
        "profile_key": profile.key,
        "profile_label": profile.label,
        "subtasks": [
            {"id": "T1", "agent": "retrieval", "objective": f"检索{rank}的身份与服饰证据"},
            {"id": "T2", "agent": "restoration", "objective": "生成人物复原影像"},
            {"id": "T3", "agent": "quality", "objective": "校验风格一致性与时代合规"},
            {"id": "T4", "agent": "copywriting", "objective": "撰写人物背景科普文案"},
        ],
        "retrieval_queries": _dedupe(
            [
                f"{rank} 服饰",
                "青铜人头像 发型 笄发 辫发",
                f"{era}",
                "古蜀 人物形象 推断边界",
                "三星堆 服饰复原 间接证据",
            ]
        ),
        "retrieval_filters": {},
        "image_spec": {
            "prompt": " ".join(prompt_parts),
            "negative_prompt": ", ".join(
                [
                    *profile.negative_tokens,
                    "ming dynasty robe, embroidered court dress, iron weapon, porcelain ornament",
                ]
            ),
            "width": 1024,
            "height": 1280,
            "style_tokens": list(profile.prompt_tokens[:6]),
            "prompt_extend": False,
        },
        "locked_form_notes": (
            "服饰只能用素色麻/丝织物与简化裁剪；无同期织物实物，必须走低风险方案并在标注中说明为示意性复原"
        ),
        "style_constraints": [
            "服饰不得出现后世形制（明代官服、补子、刺绣纹样）",
            "肤色与织物应为哑光质感",
            f"整体呈现{profile.label}",
        ],
        "copy_brief": {
            "audience": "博物馆观众",
            "length": 200,
            "tone": "讲述感强，突出推论的边界",
            "must_include": [rank, era],
        },
        "acceptance_criteria": [
            "人物服饰无后世形制元素",
            "构图符合要求的景别",
            "风格与目标档案一致",
        ],
    }


def _artifact_plan(request: dict[str, Any], profile: StyleProfile) -> dict[str, Any]:
    artifact = str(request.get("artifact") or request.get("item") or "破损青铜面具")
    method = str(request.get("method") or "最小干预修复")
    mode = str(request.get("mode") or "数字展示")
    damage = request.get("damage")

    relic = lexicon.resolve_relic(artifact)
    method_en = lexicon.resolve_method(method)
    mode_en = {
        "数字展示": "digital exhibition rendering, clean neutral background",
        "博物馆展陈": "museum display context, dark low-illuminance hall, focused spotlight",
        "学术报告": "academic documentation plate, flat even light, scale reference present",
        "VR预览": "immersive viewing framing, slight camera perspective, ambient environment light",
    }.get(mode, "digital exhibition rendering")

    prompt_parts = [
        f"Restoration visualisation of {relic.en if relic else artifact}.",
        f"Restoration method: {method_en}.",
        f"Output mode: {mode_en}.",
        f"Form constraints: {relic.form_notes if relic else 'follow excavated form, do not invent parts'}.",
        f"Style: {', '.join(profile.prompt_tokens)}.",
        # 「stable patina」是青铜专属说法，玉/象牙/金器上不适用
        _surface_truth_clause(relic, artifact)
        + " Reconstructed areas must remain visually distinguishable from original fragments.",
    ]

    return {
        "goal": f"以「{method}」方式对「{artifact}」做{ mode }导向的复原呈现",
        "profile_key": profile.key,
        "profile_label": profile.label,
        "subtasks": [
            {"id": "T1", "agent": "retrieval", "objective": f"检索「{artifact}」的形制与病害特征"},
            {"id": "T2", "agent": "restoration", "objective": "生成修复复原影像"},
            {"id": "T3", "agent": "quality", "objective": "校验修复合规性与风格一致性"},
            {"id": "T4", "agent": "copywriting", "objective": "撰写修复说明与科普文案"},
        ],
        "retrieval_queries": _dedupe(
            [
                artifact,
                f"{artifact} 形制",
                f"{artifact} 病害 锈蚀",
                "文物修复原则 最小干预",
                "青铜病 有害锈",
                "三星堆 修复 工艺",
            ]
        ),
        "retrieval_filters": {},
        "image_spec": {
            "prompt": " ".join(prompt_parts),
            "negative_prompt": ", ".join(
                [
                    *profile.negative_tokens,
                    "over-restored, polished bronze, brand new look, "
                    "restored area indistinguishable from original",
                ]
            ),
            "width": 1024,
            "height": 1024,
            "style_tokens": list(profile.prompt_tokens[:6]),
            "prompt_extend": False,
        },
        "locked_form_notes": relic.form_notes if relic else artifact,
        "style_constraints": [
            "补配区域必须可识别，不得冒充原件",
            "保留残缺信息与稳定锈层，不得打磨成新铜",
            "修复方式须符合最小干预原则",
        ],
        "copy_brief": {
            "audience": "学术参考 / 博物馆观众",
            "length": 240,
            "tone": "审慎，明确说明这是数字推断而非实物状态",
            "must_include": [artifact, method],
        },
        "acceptance_criteria": [
            "补配区域与原残件在视觉上可区分",
            "稳定锈层被保留",
            "无时代错配元素",
        ],
        "evidence_context": {"damage_ratio": damage},
    }


def _style_plan(request: dict[str, Any], profile: StyleProfile) -> dict[str, Any]:
    preset = str(request.get("style_preset") or "青铜纹饰")
    strength = int(request.get("strength") or 75)

    preset_desc = {
        "青铜纹饰": "cast bronze ornament texture, low-relief spiral and beast-face patterns",
        "黄金雕刻": "hammered gold foil relief, warm muted gold, soft dents and creases",
        "玉石质感": "translucent tremolite jade, soft subsurface glow, natural veining",
        "考古素描": "archaeological line drawing, graphite hatching, no photographic shading",
        "神庙壁画": "ancient Shu temple mural, mineral pigment on rammed earth, aged and flaking",
        "赛博古蜀": "cyberpunk reimagining with teal and magenta rim light, futuristic haze",
    }.get(preset, preset)

    blend = max(0.1, min(1.0, strength / 100.0))
    # 强度不能只写成一个数字：模型看不懂 “blend weight 0.75”。
    # 换成可执行的指令句，三档强度才真的会在画面上拉开差别。
    if blend >= 0.85:
        strength_line = (
            f"Apply the {preset} style strongly and unmistakably; "
            "the subject silhouette must still be clearly recognisable."
        )
    elif blend >= 0.55:
        strength_line = (
            f"Balance the {preset} style with the subject's own material and form; "
            "neither should dominate."
        )
    else:
        strength_line = (
            f"Apply the {preset} style subtly; the subject's own material, form and "
            "composition must dominate."
        )
    prompt_parts = [
        f"Style transfer toward {preset}: {preset_desc}.",
        f"Style strength: {strength_line}",
        "Critical constraint: the subject's silhouette, volumetric proportions and "
        "ornament layout must stay recognisably Sanxingdui — the style changes, the form does not.",
        f"Auxiliary style tokens: {', '.join(profile.prompt_tokens[:4])}.",
    ]

    return {
        "goal": f"将输入图像迁移为「{preset}」风格，强度 {strength}%",
        "profile_key": profile.key,
        "profile_label": profile.label,
        "subtasks": [
            {"id": "T1", "agent": "retrieval", "objective": f"检索「{preset}」对应的材质与工艺证据"},
            {"id": "T2", "agent": "restoration", "objective": "执行风格迁移"},
            {"id": "T3", "agent": "quality", "objective": "校验主体轮廓是否保持"},
            {"id": "T4", "agent": "copywriting", "objective": "说明该风格与古蜀工艺的对应关系"},
        ],
        "retrieval_queries": _dedupe(
            [
                preset,
                f"{preset} 工艺",
                "三星堆 材质 质感",
                "三星堆 造型语汇",
            ]
        ),
        "retrieval_filters": {},
        "image_spec": {
            "prompt": " ".join(prompt_parts),
            "negative_prompt": ", ".join(profile.negative_tokens),
            "width": 1024,
            "height": 1024,
            "style_tokens": list(profile.prompt_tokens[:6]),
            "style_strength": round(blend, 2),
            "prompt_extend": False,
        },
        "locked_form_notes": "主体轮廓与体量比例必须保持三星堆特征，这是风格迁移的唯一硬约束",
        "style_constraints": [
            "主体轮廓与纹饰布局不得被风格化破坏",
            "不得出现现代品牌标识或字样",
        ],
        "copy_brief": {
            "audience": "文化爱好者",
            "length": 180,
            "tone": "简洁，解释风格与工艺的对应关系",
            "must_include": [preset],
        },
        "acceptance_criteria": ["主体轮廓仍可辨识为三星堆器物", "目标风格特征明确可辨"],
        "style_transfer": {"preset": preset, "strength": strength},
    }


# ── LLM 增强 ────────────────────────────────────────────────────────────────
async def refine_with_llm(
    base: dict[str, Any],
    request: dict[str, Any],
    tracer: TraceRecorder | None = None,
    *,
    lock_prompt: bool = False,
) -> tuple[dict[str, Any], str]:
    """在骨架之上做增量补全。失败时静默返回骨架（不降级为错误）。

    `lock_prompt=True` 表示用户已经明确选定了提示词方案：此时 LLM 不得改写
    `image_spec.prompt`，也不得追加视觉修饰词 —— 只能补充检索视角与验收标准。
    这是「用户的选择必须被尊重」这条产品规则在代码里的落点。
    """
    if not registry.enabled:
        return base, "rule"

    locked = str(base.get("locked_form_notes") or "")
    messages: list[Message] = [
        {"role": "system", "content": PLANNER_SYSTEM},
        {
            "role": "user",
            "content": PLANNER_TEMPLATE.format(
                request_json=json.dumps(request, ensure_ascii=False, indent=2),
                base_json=json.dumps(
                    {
                        "goal": base["goal"],
                        "retrieval_queries": base["retrieval_queries"],
                        "image_prompt": base["image_spec"]["prompt"],
                        "style_constraints": base["style_constraints"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                locked=locked,
            ),
        },
    ]

    try:
        payload, result = await registry.call_json(
            "planner",
            messages,
            required_keys=("goal", "extra_queries"),
            temperature=0.3,
            max_tokens=1600,
            tag="planner",
        )
    except ProviderUnavailable as exc:
        logger.warning("Planner LLM 不可用，使用纯规则规划: %s", exc)
        metrics.record_degradation(component="planner", reason=str(exc), fallback="rule_based")
        if tracer is not None:
            tracer.degraded("planner", reason=str(exc), fallback="rule_based")
        return base, "rule"

    if tracer is not None:
        tracer.llm(
            "planner", model=result.model, latency_ms=result.latency_ms,
            usage=result.usage, tag="planner",
        )

    merged = dict(base)
    merged["goal"] = str(payload.get("goal") or base["goal"])

    extra_queries = [str(item) for item in (payload.get("extra_queries") or []) if str(item).strip()]
    merged["retrieval_queries"] = _dedupe([*base["retrieval_queries"], *extra_queries])[:10]

    directives = [str(item).strip() for item in (payload.get("visual_directives") or []) if str(item).strip()]
    image_spec = dict(base["image_spec"])
    if directives and not lock_prompt:
        # 只做「追加」：形制要点在骨架里已锁定，LLM 的修饰词插在其后
        image_spec["prompt"] = image_spec["prompt"] + " " + " ".join(directives[:6]) + "."
        image_spec["llm_directives"] = directives[:6]

    negatives = [str(item).strip() for item in (payload.get("negative_extras") or []) if str(item).strip()]
    if negatives and not lock_prompt:
        image_spec["negative_prompt"] = image_spec["negative_prompt"] + ", " + ", ".join(negatives[:4])

    merged["image_spec"] = image_spec

    style_constraints = [
        str(item).strip() for item in (payload.get("style_constraints") or []) if str(item).strip()
    ]
    if style_constraints:
        merged["style_constraints"] = _dedupe([*base["style_constraints"], *style_constraints])[:6]

    criteria = [
        str(item).strip() for item in (payload.get("acceptance_criteria") or []) if str(item).strip()
    ]
    if criteria:
        merged["acceptance_criteria"] = _dedupe([*base["acceptance_criteria"], *criteria])[:6]

    return merged, "rule+llm"


# ── 图的节点入口 ────────────────────────────────────────────────────────────
def apply_prompt_override(
    base: dict[str, Any], override: dict[str, Any], request: dict[str, Any]
) -> dict[str, Any]:
    """把用户在对话中选定的方案固化为 image_spec。

    形制要点（locked_form_notes）仍从文物词典取，因为它同时也是质检 Agent
    判定「形制忠实度」的先验 —— 即使用户手改了 prompt，质检标准不能跟着变。
    """
    plan = dict(base)
    image_spec = dict(base.get("image_spec") or {})

    image_spec["prompt"] = str(override.get("prompt") or image_spec.get("prompt") or "")
    if override.get("negative_prompt"):
        image_spec["negative_prompt"] = str(override["negative_prompt"])
    for key, caster in (
        ("width", int),
        ("height", int),
    ):
        if override.get(key) is not None:
            try:
                image_spec[key] = caster(override[key])
            except (TypeError, ValueError):
                continue

    image_spec["source"] = "user_selected_proposal"
    image_spec["proposal_id"] = override.get("proposal_id")
    image_spec["proposal_title"] = override.get("proposal_title")
    # 选定方案一律关闭提示词增强 —— 用户选的就是这份文字，不允许模型改写。
    # 想开启必须显式传 prompt_extend=true（单独用于「故意让模型自由发挥」的场景）。
    image_spec["prompt_extend"] = bool(override.get("prompt_extend") or False)
    plan["image_spec"] = image_spec
    plan["prompt_locked"] = True

    if override.get("profile_key"):
        plan["profile_key"] = str(override["profile_key"])
        plan["profile_label"] = PROFILES_BY_KEY.get(
            str(override["profile_key"]), plan.get("profile_label")
        )

    if override.get("proposal_title"):
        plan["goal"] = f"按选定方案「{override['proposal_title']}」生成：{base.get('goal') or ''}"
    plan["style_constraints"] = [
        "用户已选定提示词方案，出图必须严格使用该提示词，不得自动改写",
        *list(plan.get("style_constraints") or []),
    ][:6]
    return plan


async def run(state: dict[str, Any]) -> dict[str, Any]:
    from app.core.context import get_run_context

    request = dict(state.get("request") or {})
    context = get_run_context()
    tracer = context.tracer if context else None

    base = build_rule_plan(request)

    override = request.get("prompt_override") or None
    if isinstance(override, dict) and override.get("prompt"):
        base = apply_prompt_override(base, override, request)
        if tracer is not None:
            tracer.emit(
                "prompt_override_applied",
                "planner",
                proposal_id=override.get("proposal_id"),
                proposal_title=override.get("proposal_title"),
                profile_key=base.get("profile_key"),
                prompt_chars=len(str(override.get("prompt") or "")),
            )

    if bool(request.get("fast", False)):
        # 快速模式：跳过 LLM 增量补全，直接用规则骨架（已含形制要点与风格 token）。
        plan, mode = base, "rule"
    else:
        plan, mode = await refine_with_llm(base, request, tracer, lock_prompt=bool(plan_locked(base)))
    plan["planner_mode"] = mode
    plan["prompt_locked"] = plan_locked(base)

    if tracer is not None:
        tracer.emit(
            "plan_ready",
            "planner",
            goal=plan["goal"],
            mode=mode,
            queries=plan["retrieval_queries"],
            profile=plan["profile_label"],
            prompt_locked=plan["prompt_locked"],
            subtasks=[item["objective"] for item in plan["subtasks"]],
        )

    logger.info("规划完成", extra={"mode": mode, "profile": plan["profile_key"], "goal": plan["goal"]})

    return {
        "plan": plan,
        "profile_key": plan["profile_key"],
        "next_agent": "supervisor",
        "completed": _append(state, "planner"),
        "steps": int(state.get("steps") or 0) + 1,
    }


def _append(state: dict[str, Any], mark: str) -> list[str]:
    completed = list(state.get("completed") or [])
    if mark not in completed:
        completed.append(mark)
    return completed


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(key)
    return output
