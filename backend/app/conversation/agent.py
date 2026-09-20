"""会话式规划 Agent —— 「GPT 式对话 + 提示词方案推荐」的后端。

与传统表单式界面的差别
----------------------
表单式界面要求用户**先知道有什么选项**才能操作（所以必须把「大祭司 / 博物馆展厅 /
青铜大立人」这些枚举铺满屏幕）。对话式界面把这一步反过来：用户用自然语言说需求，
Agent 负责理解意图、查史料、给出几个**视觉角度真正不同**的提示词方案供选择。

一次对话轮次里的实际动作
------------------------
1. **意图解析**：从自由文本里抽出 kind / 文物 / 身份 / 场景 / 风格 / 修复方式；
   有 LLM 时用结构化输出，没有时用关键词规则 —— 两条路都会产出同构的 intent。
2. **史料 grounding**：用 intent 派生检索 query，召回真实史料。
   方案里的 prompt 会带上史料线索，且方案卡片会标注引用了哪些史料。
3. **并行产出**：
   - 流式生成自然语言回复（先复述理解、再讲依据、再说明方案差异）；
   - 结构化生成四个方案的「说明 + 追加修饰词」，合并到规则骨架上。

第 3 步的两路是 `asyncio.gather` 并发的，因此首字延迟取决于流式那一路，
不会因为等结构化输出而变慢。

降级原则与全系统一致：**没有 API Key 时，这依然是一个可用的功能**，
而不是一句「请配置 Key」。规则路径产出的四个方案在视觉策略、画幅、
采样参数上是完整且专业可用的，只是少了自然语言润色。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from app.agents import lexicon
from app.conversation import strategies
from app.conversation.smalltalk import SMALLTALK_EXAMPLES, classify_smalltalk
from app.conversation.store import ChatSession
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.core.tracing import TraceRecorder
from app.models.llm import Message, text_part
from app.models.registry import registry
from app.qa.answerer import answer as qa_answer
from app.quality.style_profiles import PROFILES, resolve_profile
from app.rag.store import get_retriever
from app.rag.text import truncate

logger = get_logger("app.conversation.agent")


# ════════════════════════════════════════════════════════════════════════════
#  意图解析（规则层）
# ════════════════════════════════════════════════════════════════════════════
KIND_SIGNALS: dict[str, tuple[tuple[str, ...], int]] = {
    "artifact": (("修复", "修补", "补全", "残缺", "破损", "病害", "锈蚀", "补配", "断裂", "裂开", "断成"), 3),
    "style": (
        ("风格迁移", "迁移", "转成", "换成", "改成", "油画", "工笔", "壁画", "素描",
         "赛博", "青铜纹饰", "黄金雕刻", "玉石质感", "卡通", "国风", "cosplay"),
        3,
    ),
    "scene": (
        ("场景", "场面", "全景", "俯瞰", "布局", "格局", "站在", "面前", "背景", "同框",
         "展厅", "祭祀坑", "祭祀台", "祭坛", "神庙", "遗址", "前庭"),
        2,
    ),
    "figure": (("人物还原", "还原人物", "人物形象", "形象", "发型", "服饰", "全身像", "半身像", "面部特写", "侧身像"), 2),
}

# 优先级：分数相同时按此顺序。artifact / style 是强意图，排在最前。
KIND_PRIORITY = ("artifact", "style", "scene", "figure")

# 场景 → 默认文物：用户只说「青铜神树祭坛的场景」时，
# 应该自然带出神树，而不是回落到通用默认值
SCENE_DEFAULT_RELIC: dict[str, str] = {
    "青铜神树祭坛": "青铜神树",
    "三星堆祭祀坑": "青铜纵目面具",
    "博物馆展厅": "青铜大立人",
    "遗址考古现场": "青铜大立人",
    "黑色背景展陈": "青铜纵目面具",
    "古蜀神庙前庭": "青铜大立人",
    # 祭祀场景里最常出现、也最具辨识度的是纵目面具
    "祭祀台": "青铜纵目面具",
    "祭坛": "青铜纵目面具",
    "神庙祭坛": "青铜纵目面具",
}

STYLE_KEYWORDS = ("真实照片风格", "博物馆纪实摄影", "电影级写实", "考古档案照片", "赛伯朋克风格", "赛博朋克")
STRENGTH_RE = re.compile(r"(\d{1,3})\s*%")

FOLLOWUPS: dict[str, tuple[str, ...]] = {
    "scene": (
        "把机位压低一点，做成仰视，强调体量感",
        "换成电影级写实风格，暗部再多留一点细节",
        "加上大祭司与器物同框，人物做半侧身",
    ),
    "figure": (
        "改成半身像，重点在面部与冠饰",
        "发型按辫发来做，不要笄发",
        "把服饰简化成素色麻织物，避免任何刺绣",
    ),
    "artifact": (
        "补配区域再明显一点，要一眼能看出是后补的",
        "重点拍断裂面，我要看病害类型",
        "换成博物馆展陈的灯光氛围",
    ),
    "style": (
        "风格再强一点，我要看到明显的变化",
        "主体轮廓要保持，不能被风格吃掉",
        "来一张材质特写，我想看表面质感",
    ),
}


# 规则层在缺省时补的默认值。**这些是系统假设，不是用户事实** ——
# 一律不进 `intent["stated"]`，且集中在这一处，方便一眼看清
# 「我们到底擅自替用户假设了什么」。散落各处的 setdefault 正是上一版的问题：
# 值一旦存进 intent，就再也分不清「用户说的」还是「我们猜的」。
_INTENT_DEFAULTS: dict[str, str] = {
    "identity": "大祭司",
    "scene": "黑色背景展陈",
    "style": "电影级写实",
}


def _fill(intent: dict[str, Any], slot: str, value: str) -> None:
    """只在槽位为空时补值（把空串与缺键一视同仁）。"""
    if not intent.get(slot):
        intent[slot] = value


def _apply_defaults(intent: dict[str, Any]) -> None:
    """补齐缺省槽位，保证下游永远拿到可用值。

    默认值绝不登记为 `stated` —— 它是「用户没说、我们替他想」的部分，
    下游凡是要区分二者的地方一律查 `strategies.was_stated`。
    """
    _fill(intent, "scene", _INTENT_DEFAULTS["scene"])
    # 文物缺省依赖场景：先定场景，再由「场景→默认文物」推断，推断不出才退到通用值。
    # 只有当**场景本身是用户给的**时，推出的文物才算有依据（祭祀坑→纵目面具）；
    # 若场景也是缺省补的，那文物属于「假设的假设」，不能进账本。
    if not intent.get("subject"):
        derived = SCENE_DEFAULT_RELIC.get(str(intent.get("scene") or ""))
        if derived:
            intent["subject"] = derived
            if strategies.was_stated(intent, "scene"):
                strategies.mark_stated(intent, "subject")
        else:
            intent["subject"] = "青铜大立人"
    _fill(intent, "identity", _INTENT_DEFAULTS["identity"])
    _fill(intent, "style", _INTENT_DEFAULTS["style"])


def rule_intent(message: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """从自由文本里抽意图。**不依赖任何模型**，且总是返回完整结构。

    用加权打分而非「第一个匹配就返回」，是因为一句话里往往同时出现多个意图词
    （「大祭司在神树祭坛前的**场景**」既含人物身份又含场景）。
    先前用顺序匹配时，'祭司' 命中人物还原规则导致整句被判定成人物还原任务 ——
    这正是需要打分 + 优先级的原因。
    """
    text = message or ""
    prior: dict[str, Any] = dict(previous or {})
    intent: dict[str, Any] = dict(prior)
    # `rewrite` 是「这一句」的主体动作句，不能跟着上一轮继承下来 ——
    # 否则用户换了主体，提示词第一块还在描述上一个画面
    # （比如从「祭司主持祭祀」换成「面具特写」，第一块仍是那个祭司）。
    # 其余槽位可以继承（用户常常只补充一个维度），但这句话必须每轮重写。
    intent.pop("rewrite", None)
    # 来源账本必须始终存在（哪怕为空列表）—— 下游 `was_stated` 与调用方都会直接读它。
    # 同时把跨轮继承来的账本归一化成干净的字符串列表。
    intent[strategies.STATED_KEY] = [
        slot for slot in (intent.get(strategies.STATED_KEY) or []) if isinstance(slot, str)
    ]

    # ── 意图分类：加权打分 ──────────────────────────────────────────────────
    scores = {kind: 0 for kind in KIND_SIGNALS}
    for kind, (keywords, weight) in KIND_SIGNALS.items():
        hits = sum(1 for keyword in keywords if keyword in text)
        if hits:
            scores[kind] = hits * weight
    best = max(KIND_PRIORITY, key=lambda kind: (scores[kind], -KIND_PRIORITY.index(kind)))
    intent["kind"] = best if scores.get(best, 0) > 0 else str(prior.get("kind") or "scene")

    # ── 场景识别 ────────────────────────────────────────────────────────────
    matched_scene: str | None = None
    for candidate in lexicon.SCENES:
        if candidate in text:
            matched_scene = candidate
            break
    if matched_scene:
        intent["scene"] = matched_scene
        strategies.mark_stated(intent, "scene")

    # 关键细节：先摘掉场景名再做文物匹配。
    # 否则「青铜神树祭坛」里的「青铜神树」会被误判为本次的核心文物。
    relic_text = text
    if matched_scene:
        relic_text = relic_text.replace(matched_scene, " ")

    relic = lexicon.resolve_relic(relic_text)
    if relic:
        intent["subject"] = relic.label
        strategies.mark_stated(intent, "subject")

    # 登记「原文里到底有没有提到人」。下游据 `was_stated` 决定是否补承载者／画中人，
    # 不再需要 `identity_explicit` 这种影子标志 —— 因为缺省身份不写进账本。
    for candidate in lexicon.IDENTITIES:
        if candidate in text:
            intent["identity"] = candidate
            strategies.mark_stated(intent, "identity")
            break

    for candidate in STYLE_KEYWORDS:
        if candidate in text:
            intent["style"] = candidate
            strategies.mark_stated(intent, "style")
            break

    for candidate in lexicon.METHODS:
        if candidate in text:
            intent["method"] = candidate
            strategies.mark_stated(intent, "method")
            intent["kind"] = "artifact"
            break

    match = STRENGTH_RE.search(text)
    if match:
        intent["strength"] = max(10, min(100, int(match.group(1))))
        strategies.mark_stated(intent, "strength")

    # 风格迁移预设（青铜纹饰 / 黄金雕刻 / 玉石质感 / 考古素描 / 神庙壁画 / 赛博古蜀）
    if intent["kind"] == "style":
        profile = resolve_profile(text)
        if profile.key != "default":
            intent["style_preset"] = profile.label
            strategies.mark_stated(intent, "style_preset")
            _fill(intent, "style", profile.label)
            strategies.mark_stated(intent, "style")

    intent["brief"] = truncate(text, 240)
    # 补缺省（身份／场景／风格／文物）。默认值**不**登记 stated ——
    # 它就是「用户没说、我们替他假设」的部分。集中在这一个函数里，
    # 不再散落成三处 `setdefault`。
    _apply_defaults(intent)

    # ── 主体裁决 ────────────────────────────────────────────────────────────
    # 这一步决定「谁排第一块、给多少篇幅」，是整条提示词组装里最关键的一环。
    # 起因：用户要「大祭司戴面具主持祭祀」，kind 落在 scene，
    # 而旧的块序逻辑在 scene + 有文物时会跳过人物描述 —— 提示词里
    # 没有一个字说祭司长什么样，交付的是一张只有面具的考古纪实照。
    #
    # 判据是「有没有用户依据」（`was_stated`），不是「identity 键有没有值」——
    # 后者被缺省补成「大祭司」，对任何意图都成立。
    kind = str(intent.get("kind") or "scene")
    role = ""
    if kind in {"scene", "figure"}:
        if any(word in text for word in strategies.SCENE_SUBJECT_SIGNALS):
            # 用户明确要「场面」—— 优先级最高，用来推翻多轮里粘住的「人在场」。
            role = strategies.ROLE_SCENE
        elif strategies.was_stated(intent, "identity") or any(
            word in text for word in strategies.PERSON_SIGNALS
        ):
            role = strategies.ROLE_PERSON
    intent["subject_role"] = role or strategies.resolve_role(kind, intent)
    return intent


def missing_slots(intent: dict[str, Any]) -> list[str]:
    """返回仍然缺失、值得追问的信息槽位（用于回复结尾的自然追问）。

    判据是**来源账本**（用户真的说过没有），不是「键有没有值」——
    缺省会把 scene / identity 补满，用「键有没有值」判断会让追问永远不触发
    （历史 bug：scene / figure 分支的追问其实是死代码）。
    """
    kind = intent.get("kind")
    missing: list[str] = []
    if kind in {"scene", "figure"}:
        # 只有「主体是人」时才追问身份：纯场面请求不需要凭空加一个人。
        if intent.get("subject_role") == strategies.ROLE_PERSON and not strategies.was_stated(
            intent, "identity"
        ):
            missing.append("人物身份")
        if not strategies.was_stated(intent, "scene"):
            missing.append("场景地点")
    elif kind == "artifact":
        if not strategies.was_stated(intent, "method"):
            missing.append("修复方式")
    elif kind == "style":
        if not strategies.was_stated(intent, "style_preset"):
            missing.append("目标风格")
    return missing


# ════════════════════════════════════════════════════════════════════════════
#  意图解析（LLM 层）
# ════════════════════════════════════════════════════════════════════════════
# **这条链路每轮对话都要走一次，用户在等它。**
#
# 所以这里的取舍与别处相反：不是「让模型多干点」，而是**让模型少干、干准**。
# 三件事共同保证它快：
#   1. 小快模型（`model_intent`，不再是 planner 的 qwen3-max）；
#   2. 输入短 —— 候选词表按原文预筛，上一轮只带关键字段，历史只带 2 轮；
#   3. 输出短 —— 短字段 + 一句 ≤30 词的改写，`max_tokens=220`。
#
# 「不编造」靠的不是提示词自觉，而是**分工**：
# 材质、形制、尺寸这些最终会写进出图提示词的事实全部来自词典（`lexicon`），
# 模型碰不到。它只做两件规则做不好的事 —— 判**谁是主角**、写**主体在做什么**。
INTENT_SYSTEM = """你是三星堆数字复原项目的意图解析器。把用户的话压成 JSON，不要解释。

【kind】任务类型，与 role 正交：
- figure 通用复原（人物/器物/场面）
- scene  figure 的场面分支
- artifact 仅「修复/补全/3D复原 残缺器物」用
- style  仅明确做风格迁移时用（油画/水墨/赛博朋克/卡通/工笔/cosplay/国风…）
主体是器物 ≠ artifact：面具特写、现状记录、展陈都是 figure+role=artefact。

【role】谁当主角：person 人 / artefact 器物 / scene 整个场面

【rewrite】一句英文≤30词，只写「主角在做什么+画面地位」。
别写外观/材质/机位/光线/风格（系统补）。尤其别写 bronze/gold/jade/silver 等材质形容词、close-up/wide 等机位、lighting、style（不要重复风格名）。
person→动作+锚点占中景全身；artefact→器物如何呈现+占主体；scene→空间关系+全貌可读

【硬约束】identity/scene/subject 只从候选表选，没有留空""。style 没提就留空，别默认博物馆纪实摄影。没说人别默认大祭司，没说场景别默认博物馆。

示例
输入：大祭司戴青铜纵目面具在祭祀台前主持祭祀的场景
输出：{"kind":"figure","role":"person","identity":"大祭司","scene":"祭祀台","subject":"青铜纵目面具","style":"","method":"","style_preset":"","strength":0,"rewrite":"He stands before the stone altar with both arms raised and is the visual anchor of the image in full figure.","brief":"复原戴面具的大祭司在祭祀台前主持祭祀"}"""

INTENT_TEMPLATE = """【用户原话】{message}
【最近对话】{history}
【上一轮】{previous}
【候选】文物：{relics}；身份：{identities}；场景：{scenes}
【输出】{{"kind":"scene|figure|artifact|style","role":"person|artefact|scene","identity":"","scene":"","subject":"","style":"","method":"","style_preset":"","strength":0,"rewrite":"","brief":""}}"""

# 意图解析的延迟预算。超时就退回规则解析继续走 ——
# 用户等的是一个画面，不是一个完美槽位；慢一秒的代价比槽位差一档更大。
INTENT_TIMEOUT_SECONDS = 6.0


def _shortlist(text: str, values: list[str], limit: int = 4) -> str:
    """按原文命中情况预筛候选，压住输入长度。

    为什么不全表塞进去：输入 token 直接决定首包延迟，而这条链路每轮都要走。
    也不能只给命中的 —— 一个都没命中时，模型需要几个候选才能把口语映射到规范名，
    否则它只能留空，槽位就白丢了。
    """
    hit = [item for item in values if item in text]
    rest = [item for item in values if item not in hit]
    return "、".join((hit + rest)[:limit])


def _relic_candidates(text: str, limit: int = 4) -> str:
    """文物要同时匹配标签与别名。

    用户说的是「纵目」，规范名却是「青铜纵目面具」；只按标签筛的话，
    候选表里不会有它，模型只能留空 —— 而它就藏在别名里。
    """
    hit = [
        spec.label
        for spec in lexicon.RELICS
        if spec.label in text or any(alias in text for alias in spec.aliases)
    ]
    rest = [spec.label for spec in lexicon.RELICS if spec.label not in hit]
    return "、".join((hit + rest)[:limit])


def _prior_slots(prior: dict[str, Any]) -> str:
    """上一轮只带「结论 + 用户真说过的事实」，不带整份 JSON。

    两个要点：
    1. 整份 intent 有十几个字段（含 brief、evidence_ids 之类），
       塞进去只是让输入变长，对「这句在说什么」没有帮助；
    2. **只带 `stated` 里的事实槽位** —— 缺省补的「大祭司 / 黑色背景展陈」
       不能冒充「用户上一轮说过的」，否则意图 LLM 会被我们自己的缺省值带偏。
    计算得出的结论（kind / subject_role）不是用户事实，单独始终带上。
    """
    keys = ("kind", "subject_role")
    facts = ("identity", "scene", "subject")
    slim = {key: prior[key] for key in keys if prior.get(key)}
    slim.update(
        {
            key: prior[key]
            for key in facts
            if prior.get(key) and strategies.was_stated(prior, key)
        }
    )
    return json.dumps(slim, ensure_ascii=False) if slim else "（无）"


def _looks_like_repair(text: str) -> bool:
    """原文是否真的在说修复/补全残缺器物。

    用来拦住模型把「主体是器物」误判成 kind=artifact——
    一张「面具材质特写」不该继承修复专属的负向词与策略。
    """
    return any(sig in (text or "") for sig in KIND_SIGNALS["artifact"][0])


async def resolve_intent(
    session: ChatSession, message: str, tracer: TraceRecorder | None
) -> tuple[dict[str, Any], str]:
    baseline = rule_intent(message, session.last_intent or None)
    if not registry.enabled:
        return baseline, "rule"

    messages: list[Message] = [
        {"role": "system", "content": INTENT_SYSTEM},
        {
            "role": "user",
            "content": INTENT_TEMPLATE.format(
                message=truncate(message, 400),
                history=session.transcript(2) or "（无）",
                previous=_prior_slots(session.last_intent or {}),
                relics=_relic_candidates(message),
                identities=_shortlist(message, list(lexicon.IDENTITIES)),
                scenes=_shortlist(message, list(lexicon.SCENES)),
            ),
        },
    ]

    try:
        payload, result = await asyncio.wait_for(
            registry.call_json(
                "intent",
                messages,
                # 不用 `required_keys` 卡格式：缺字段会抛错 → 整条候选链重试 →
                # 白等两轮。下面用白名单校验代替，格式不合只忽略那一个字段。
                temperature=0.0,
                max_tokens=220,
                tag="chat_intent",
            ),
            timeout=INTENT_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("意图解析超时（>%.1fs），退回规则解析", INTENT_TIMEOUT_SECONDS)
        metrics.record_degradation(component="chat_intent", reason="timeout", fallback="rule")
        if tracer is not None:
            tracer.degraded("conversation", reason="意图解析超时", fallback="rule_intent")
        return baseline, "rule"
    except ProviderUnavailable as exc:
        logger.info("意图解析 LLM 不可用，使用规则解析: %s", exc)
        metrics.record_degradation(component="chat_intent", reason=str(exc), fallback="rule")
        if tracer is not None:
            tracer.degraded("conversation", reason=str(exc), fallback="rule_intent")
        return baseline, "rule"

    if tracer is not None:
        tracer.llm("conversation", model=result.model, latency_ms=result.latency_ms,
                   usage=result.usage, tag="chat_intent")

    merged = dict(baseline)

    # role 用**白名单**校验。小模型偶尔会写「人」「主角」「人物」这类词，
    # 直接塞进去会让下游 `resolve_role` 收到无法识别的值而降级到默认分支。
    role = str(payload.get("role") or payload.get("subject_role") or "").strip().lower()
    if role in strategies.ROLES:
        merged["subject_role"] = role

    # 小模型也可能把枚举写错，同样校验；不合法就保留规则层的判断。
    kind = str(payload.get("kind") or "").strip().lower()
    if kind in {"scene", "figure", "artifact", "style"}:
        # kind=artifact 必须是修复/补全/3D复原类任务。模型常把「主体是器物」误判成
        # artifact，给一张文物特写套上「别修太新」的负向约束。没有修复信号就回落 figure。
        if kind == "artifact" and not _looks_like_repair(message):
            kind = "figure"
        merged["kind"] = kind

    for key in ("subject", "identity", "scene", "style", "method", "style_preset", "brief"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            # method 与 kind 同源：非修复任务里模型偶发幻觉出「修复方式」，
            # 会在出图提示词里多出一段 Restoration approach，把展示图往修复图带。
            if key == "method" and kind != "artifact" and not _looks_like_repair(message):
                continue
            merged[key] = value.strip()
            # LLM 抽出的也是「用户事实」，登记进来源账本（brief 只是原话复述，不算槽位）。
            if key != "brief":
                strategies.mark_stated(merged, key)

    # rewrite 直接进第一块，长度 / 语言必须设防 —— 小模型一旦跑偏会写出一整段，
    # 把提示词预算吃光（免费通道只有 256 字）；或混进中文破坏下游英文提示词。
    rewrite = payload.get("rewrite")
    if isinstance(rewrite, str):
        rewrite = rewrite.strip()
        # 必须是一句英文（提示词要求）。出现任何中文直接丢弃，退回规则锚点句。
        if rewrite and not any("\u4e00" <= ch <= "\u9fff" for ch in rewrite):
            words = rewrite.split()
            if len(words) > 30:  # 词数上限，超了截到 30 词，防止写成长段落
                rewrite = " ".join(words[:30])
            merged["rewrite"] = truncate(rewrite, 240)

    try:
        strength = int(payload.get("strength") or 0)
        if 10 <= strength <= 100:
            merged["strength"] = strength
    except (TypeError, ValueError):
        pass
    return merged, "llm"


# ════════════════════════════════════════════════════════════════════════════
#  自然语言回复（流式）
# ════════════════════════════════════════════════════════════════════════════
REPLY_SYSTEM = """你是「古蜀智脑」，三星堆文物数字复原项目的规划型 AI 助手。
用户会用自然语言描述想复原的画面，你会先理解需求、查证史料，再给出几个视觉角度不同的提示词方案。

回复要求（这是**对话**，不是报告）：
1. 第一句复述你理解到的复原目标，让用户确认没理解错；
2. 第二句说明你从史料里查到的关键依据，要具体（器物尺寸、形制特征、材质状态），
   不要写「三星堆历史悠久」这类空话；史料里没有的内容必须说「史料中没有直接记载」；
3. 第三句说明**你为这次选定的视觉角度**（一句话，例如「我按中景祭祀现场来做」）以及为什么这样选；
   不要列举你没选的方案，也不要说「给你几个方案挑」—— 创作决策由你做完，用户只需要否决权；
4. 结尾用一句自然的话邀请调整（例如「想换机位或加人物，直接说」）。

篇幅 120~200 字。不要用 markdown 标题、列表符号或加粗。
不要编造任何史实、年代或文物名称。语气克制专业，像一位考古工作者在跟同事讨论。"""

REPLY_TEMPLATE = """【用户诉求】
{message}

【解析出的意图】
{intent}

【检索到的史料依据】
{evidence}

【你已选定的方案】
{proposals}

请直接输出你要对用户说的话。"""


def _intent_for_prompt(intent: dict[str, Any]) -> str:
    """序列化给模型看的意图：**剔掉内部来源账本**（`stated`）。

    `stated` 是「用户说没说」的记账，属于我们的内部状态，不是意图本身。
    注入回复 / 润色模型只会增加噪音，还可能诱导它去复述「哪些字段是默认补的」。
    """
    return json.dumps(
        {key: value for key, value in intent.items() if key != strategies.STATED_KEY},
        ensure_ascii=False,
    )


def _reply_prompt(
    message: str, intent: dict[str, Any], evidence: list[dict[str, Any]], proposal_titles: list[str]
) -> str:
    evidence_block = (
        "\n".join(
            f"[{index + 1}] 《{item.get('title')}》（{item.get('source')}）：{truncate(str(item.get('text') or ''), 300)}"
            for index, item in enumerate(evidence[:3])
        )
        or "（本次未检索到直接相关的史料）"
    )
    return REPLY_TEMPLATE.format(
        message=truncate(message, 300),
        intent=_intent_for_prompt(intent),
        evidence=evidence_block,
        proposals="、".join(proposal_titles),
    )


async def _stream_reply(
    message: str,
    intent: dict[str, Any],
    evidence: list[dict[str, Any]],
    titles: list[str],
    tracer: TraceRecorder | None,
) -> AsyncIterator[str]:
    if not registry.enabled:
        return
    spec = registry.spec("copywriter")
    client = registry.client_for_model(spec.primary)
    messages: list[Message] = [
        {"role": "system", "content": REPLY_SYSTEM},
        {"role": "user", "content": _reply_prompt(message, intent, evidence, titles)},
    ]
    async for delta in client.stream_chat(messages, temperature=0.5, max_tokens=700, tag="chat_reply"):
        yield delta
    if tracer is not None:
        tracer.llm("conversation", model=spec.primary, latency_ms=0.0, usage={}, tag="chat_reply_stream")


# ════════════════════════════════════════════════════════════════════════════
#  方案润色（结构化）
# ════════════════════════════════════════════════════════════════════════════
REFINE_SYSTEM = """你是三星堆复原项目的视觉方案专家。
系统已经基于人工整理的文物词典与风格档案，为几个固定的视觉策略生成了提示词骨架。
你的任务是**润色与补充**，不是重写：

- `rationale`：用中文说明这个方案适合什么使用场景（25~50 字，具体，不要空话）；
- `extra_directives`：追加到该方案提示词末尾的英文修饰词（1~4 条，每条不超过 12 词）；
- `title`：可以微调标题让它更贴切（不超过 8 个汉字）。

硬性约束：不得建议任何现代器物、铁器、瓷器、汉字铭文、佛教元素、明清服饰。
所有**文物形制描述已由系统锁定**，你的修饰词不能与之冲突（例如不能说「戴上王冠」
如果骨架里写的是「高冠」）。只输出 JSON。"""

REFINE_TEMPLATE = """【用户诉求】
{message}

【解析出的意图】
{intent}

【检索到的史料线索】
{cues}

【待润色的方案骨架】
{skeletons}

请输出：
{{
  "proposals": [
    {{"strategy": "<原样返回策略 key>", "title": "<标题>", "rationale": "<中文说明>", "extra_directives": ["<英文修饰词>"]}}
  ]
}}"""


async def _refine_proposals(
    message: str,
    intent: dict[str, Any],
    cues: list[str],
    skeleton: list[dict[str, Any]],
    tracer: TraceRecorder | None,
) -> dict[str, dict[str, Any]]:
    """返回 {strategy_key: {title, rationale, extra_directives}}；失败时返回空 dict 走规则。"""
    if not registry.enabled:
        return {}

    brief = [
        {
            "strategy": item["strategy"],
            "title": item["title"],
            "angle": item["angle"],
            "existing_prompt": truncate(item["prompt"], 420),
        }
        for item in skeleton
    ]
    messages: list[Message] = [
        {"role": "system", "content": REFINE_SYSTEM},
        {
            "role": "user",
            "content": REFINE_TEMPLATE.format(
                message=truncate(message, 300),
                intent=_intent_for_prompt(intent),
                cues="；".join(cues[:5]) or "（无）",
                skeletons=json.dumps(brief, ensure_ascii=False, indent=2),
            ),
        },
    ]

    try:
        payload, result = await registry.call_json(
            "planner", messages, required_keys=("proposals",), temperature=0.3,
            max_tokens=1400, tag="chat_proposals",
        )
    except ProviderUnavailable as exc:
        logger.info("方案润色不可用，使用规则方案: %s", exc)
        metrics.record_degradation(component="chat_proposals", reason=str(exc), fallback="rule")
        if tracer is not None:
            tracer.degraded("conversation", reason=str(exc), fallback="rule_proposals")
        return {}

    if tracer is not None:
        tracer.llm("conversation", model=result.model, latency_ms=result.latency_ms,
                   usage=result.usage, tag="chat_proposals")

    refined: dict[str, dict[str, Any]] = {}
    for item in payload.get("proposals") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("strategy") or "").strip()
        if not key:
            continue
        directives = [
            str(value).strip()
            for value in (item.get("extra_directives") or [])
            if str(value).strip()
        ]
        refined[key] = {
            "title": str(item.get("title") or "").strip(),
            "rationale": str(item.get("rationale") or "").strip(),
            "extra_directives": directives[:4],
        }
    return refined


# ════════════════════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════════════════════
def _retrieval_queries(intent: dict[str, Any]) -> list[str]:
    kind = str(intent.get("kind") or "scene")
    subject = str(intent.get("subject") or "")
    queries = [subject, f"{subject} 形制"]
    if kind == "artifact":
        queries += [f"{subject} 病害 锈蚀", "文物修复原则 最小干预", "青铜病 有害锈"]
    elif kind == "figure":
        queries += [f"{intent.get('identity') or '大祭司'} 服饰", "古蜀 人物形象 推断边界", "三星堆 服饰复原 间接证据"]
    elif kind == "style":
        queries += [f"{intent.get('style_preset') or subject} 工艺", "三星堆 材质 质感", "三星堆 造型语汇"]
    elif strategies.resolve_role(kind, intent) == strategies.ROLE_PERSON:
        # 主体是人时，「这个祭司该长什么样」才是要检索的东西 ——
        # 问器物的纹饰对画人没有帮助。
        person = str(intent.get("identity") or "大祭司")
        queries += [
            f"{person} 服饰",
            f"{person} 形象",
            f"{intent.get('scene') or ''} 祭祀",
            "三星堆 时代错配 禁忌",
        ]
    else:
        queries += [
            f"{subject} 纹饰",
            str(intent.get("scene") or ""),
            "三星堆 时代错配 禁忌",
            "三星堆 造型语汇",
        ]
    seen: set[str] = set()
    ordered: list[str] = []
    for query in queries:
        key = query.strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered[:8]


def _cues_from_evidence(evidence: list[dict[str, Any]]) -> list[str]:
    """无 LLM 时从史料标签里抽视觉线索。

    只保留「看起来像视觉描述」的标签（形制/材质/纹饰相关）。

    纯白名单不够用：原先的 visual_hints 里有「质」「金」这类单字，
    「质检红线」因为含「质」被误判成视觉线索，于是提示词里出现了
    `Evidence-based visual cues: 金箔; 质检红线; 哑光质感` ——「质检红线」是质量管理标签，
    对出图毫无意义，只会稀释真正想表达的内容。
    所以这里加一道黑名单：命中元信息词的标签直接丢弃。
    """
    visual_hints = (
        "哑光", "颗粒", "圆钝", "锈", "绿", "蓝", "金", "质", "纹", "饰", "凸", "柱",
        "耳", "冠", "袍", "座", "枝", "鸟", "龙", "色", "光", "影",
    )
    # 语料里用于描述「标准/争议/流程」而非「外观」的标签
    meta_terms = (
        "红线", "质检", "争议", "评估", "维度", "标准", "原则", "规范", "流程",
        "要求", "注意事项", "学术", "研究", "结论",
    )

    cues: list[str] = []
    for chunk in evidence[:4]:
        for tag in chunk.get("tags") or []:
            tag = str(tag).strip()
            if not tag or tag in cues:
                continue
            if any(term in tag for term in meta_terms):
                continue
            if any(hint in tag for hint in visual_hints):
                cues.append(tag)
        if len(cues) >= 5:
            break
    return cues[:5]


def _style_transfer_profile(intent: dict[str, Any]) -> str:
    """风格迁移要用「用户指名的风格」做质检标尺。

    为什么不能沿用它原来写死的 `default`：
    StyleProfile 是单一事实源 —— prompt 的 Style 块、质检的数值区间、anachronism 负向词
    都从它派生。`default` 是个空档案（没有数值区间、没有负向词），意味着风格迁移这一支
    的「自检」其实是空转：prompt 里没有目标风格 token，质检也没有可比的基准。

    因此：识别出预设就用预设；识别不出来则回退到 `photo_real`（一个真实的摄影档案），
    而绝不能回退到 `default` ——宁可让质检要求「像一张照片」，也不要让质检什么都不要求。
    """
    candidates = [
        str(intent.get("style_preset") or ""),
        str(intent.get("style") or ""),
    ]
    resolved = resolve_profile(*candidates)
    return resolved.key if resolved.key != "default" else "photo_real"


def build_rule_proposals(
    intent: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    cues: list[str] | None = None,
    refined: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    kind = str(intent.get("kind") or "scene")
    evidence_ids = [str(chunk.get("doc_id")) for chunk in evidence[:5]]
    refined = refined or {}
    profile_override = _style_transfer_profile(intent) if kind == "style" else None
    proposals: list[dict[str, Any]] = []

    # 传入 intent：策略集要按**主体**（人／器物／场面）选，不能只看 kind。
    for strategy in strategies.strategies_for(kind, intent):
        patch = refined.get(strategy.key) or {}
        proposal = strategies.build_proposal(
            kind=kind,
            intent=intent,
            strategy=strategy,
            evidence_cues=cues,
            evidence_ids=evidence_ids,
            rationale_override=patch.get("rationale") or None,
            extra_directives=patch.get("extra_directives") or None,
            profile_override=profile_override,
        )
        if patch.get("title"):
            proposal["title"] = patch["title"]
        if patch:
            proposal["generated_by"] = "rule+llm"
        proposals.append(proposal)
    return proposals


def build_decision_proposal(
    intent: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    cues: list[str] | None = None,
    refined: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """AI 拍板后产出的**单个**方案（可直接出图）。

    与 `build_rule_proposals` 的分工：那个是「列出所有角度」的探查原语
    （诊断脚本与评测仍在用），这里只产出一个 —— 由 `pick_strategy` 选定。
    """
    kind = str(intent.get("kind") or "scene")
    strategy = strategies.pick_strategy(kind, intent)
    patch = (refined or {}).get(strategy.key) or {}
    proposal = strategies.build_proposal(
        kind=kind,
        intent=intent,
        strategy=strategy,
        evidence_cues=cues,
        evidence_ids=[str(chunk.get("doc_id")) for chunk in evidence[:5]],
        rationale_override=patch.get("rationale") or None,
        extra_directives=patch.get("extra_directives") or None,
        profile_override=_style_transfer_profile(intent) if kind == "style" else None,
    )
    if patch.get("title"):
        proposal["title"] = patch["title"]
    if patch:
        proposal["generated_by"] = "rule+llm"
    return proposal


# 追问时给用户「一句话就能开始」的锚点示例。追问的目的是拿到一个立足点，
# 不是让用户填表 —— 所以给示例，而不是罗列字段。
ASK_EXAMPLES: tuple[str, ...] = (
    "青铜纵目面具在祭祀坑出土现场",
    "大祭司在祭祀台前主持祭祀",
    "金杖的材质特写",
)


def clarifying_question(intent: dict[str, Any]) -> str:
    """只有在**连画什么都无从谈起**时才追问，否则一律由 AI 自行决策。

    取向与表单式界面相反：表单要求用户先知道所有选项才能操作；
    这里要求 AI 先把能定的都定了。只有用户没给任何锚点时才值得问 ——
    否则问出来的都是 AI 本可以合理假设的东西（风格、机位、光线）。

    锚点不只看词典槽位：用户说「来一张祭祀场面的俯瞰布局」时，场景槽位可能没命中
    词典 key，但「场面 / 俯瞰 / 布局」已经把画面类型说清楚了，照样算有依据。
    """
    anchors = ("subject", "scene", "identity", "style", "method", "style_preset", "strength")
    if any(strategies.was_stated(intent, slot) for slot in anchors):
        return ""
    brief = str(intent.get("brief") or "")
    if any(word in brief for word in strategies.SCENE_REQUEST_WORDS):
        return ""
    return "这次想复原哪件器物、哪个场景或哪个人物？说一个关键词我就开始，其余的交给我定。"


def _fallback_reply(intent: dict[str, Any], evidence: list[dict[str, Any]], proposals: list[dict[str, Any]]) -> str:
    kind_label = {"scene": "场景复原", "figure": "人物还原", "artifact": "文物修复", "style": "风格迁移"}.get(
        str(intent.get("kind")), "场景复原"
    )
    parts = [
        f"我理解你要做的是一次「{kind_label}」：{intent.get('brief') or intent.get('subject')}。",
    ]

    if evidence:
        top = evidence[0]
        parts.append(
            f"我检索到 {len(evidence)} 条相关史料，其中《{top.get('title')}》提到"
            f"{truncate(str(top.get('text') or ''), 70)}——这几条已经作为形制依据写进了下面的提示词。"
        )
    else:
        parts.append("本次没有检索到直接相关的史料，下面的提示词仅依据项目内置的形制通识撰写，建议人工复核。")

    # 措辞是「我替你定了」，不是「给你几个挑」—— 这是本轮产品取向的落点。
    head = proposals[0] if proposals else {}
    parts.append(
        f"这次我直接按「{head.get('title') or '默认角度'}」来做：{head.get('rationale') or ''}"
        "想换机位、光位，或者要加人物与器物同框，说一声我就改；也可以直接开始生成。"
    )
    return "".join(parts)


# ════════════════════════════════════════════════════════════════════════════
#  非创作消息闸门（闲聊 / 领域提问）
# ════════════════════════════════════════════════════════════════════════════
# 「你是谁」不是生图需求 —— 提问类消息交给 qa.answerer（带引用、答不出就明说），
# 不再被意图解析硬掰成「你想复原……」。
_QUESTION_WORDS = ("是什么", "什么是", "为什么", "为啥", "怎么", "如何", "什么时候", "何时",
                   "多少", "哪里", "哪儿", "谁", "介绍", "讲讲", "吗",
                   # 100 题压测（scripts/chat_probe.py）暴露的缺口：裸疑问词与
                   # 「多高 / 几号坑 / 有没有」这类没被上面覆盖的问法
                   "什么", "哪", "几", "多高", "多长", "多大", "多重",
                   "有没有", "是不是", "知不知道")
# 出现这些词说明用户在谈创作或改方案，绝不是在问知识
_QA_FORBIDDEN = ("复原", "修复", "修补", "还原", "生成", "画", "绘", "创作", "提示词",
                 "风格", "场景", "迁移", "海报", "视频", "照片", "改", "换成", "加上",
                 "放大", "缩小", "压低", "特写", "构图", "光", "修")


def _looks_like_question(message: str) -> bool:
    text = message.strip()
    if not 2 <= len(text) <= 60:
        return False
    if any(word in text for word in _QA_FORBIDDEN):
        return False
    return text.endswith(("？", "?")) or any(word in text for word in _QUESTION_WORDS)


async def _qa_reply(message: str) -> tuple[str, list[dict[str, Any]]] | None:
    """把领域问题转交 qa.answerer。

    返回 (回答文本, 引用列表)；链路异常返回 None（交回创作管线兜底）。
    答不准（语料无据）时不编造 —— 返回一句明说的引导话术。
    """
    try:
        # top_n 用 settings 默认值（6）：给模型更宽的证据面，减少「其实语料有
        # 相关记载却被 top-3 截掉」造成的误拒。
        result = await qa_answer(message)
    except Exception as exc:  # noqa: BLE001 - QA 失败不应阻断对话
        logger.warning("QA 链路失败: %s", exc)
        metrics.inc("sxd_chat_qa_failed_total")
        return None
    if result.refused or not result.answer:
        return (
            "这个问题我在语料里没有找到可靠记载，不能编一个有出处的假答案。"
            "你可以换个说法再问，或者直接描述想复原的场景，我来出方案。",
            [],
        )
    return result.answer, result.citations


# ════════════════════════════════════════════════════════════════════════════
#  方案数量决策：呈现几个、哪个是推荐项，都交给 AI
# ════════════════════════════════════════════════════════════════════════════
SELECT_SYSTEM = """你是三星堆文物复原项目的视觉方案决策助手。
系统已基于文物词典与风格档案，为一条用户诉求生成了若干个候选视觉角度（每个含 key、标题、角度说明）。
你只决定一件事：**应该向用户呈现几个、AI 最推荐哪一个**。

准则：
- 默认只呈现 1 个——诉求已足够明确、或各角度差异很小时，不要堆方案。
- 仅当存在 2~3 个**真正不同的可取方向**（如纪实 vs 艺术化、全景 vs 特写、不同主体站位）时才呈现多个。
- 必须指定一个 recommended（AI 选定的默认项）；其余只是「可选方向」，用户不必选。
- 数量不超过候选集合，且不超过 3。

只输出 JSON，不要解释：
{"present": ["key1", "key2"], "recommended": "key1"}"""

SELECT_TEMPLATE = """用户诉求：{message}

意图：{intent}

可依据史料线索：{cues}

候选视觉角度（present / recommended 里的 key 必须与下列完全一致）：
{candidates}
"""


async def _select_proposals(
    message: str,
    intent: dict[str, Any],
    candidates: list[dict[str, Any]],
    cues: list[str],
    tracer: TraceRecorder | None,
) -> tuple[list[str], str]:
    """由 AI 决定呈现几个方案、哪个是推荐项。

    返回 (有序 key 列表, 推荐 key)。无 Key / 解析失败 / 越界时回落到单方案。
    """
    kind = str(intent.get("kind") or "scene")
    fallback = strategies.pick_strategy(kind, intent)
    valid_keys = {c["strategy"] for c in candidates}
    if not registry.enabled:
        return [fallback.key], fallback.key
    try:
        briefs = [
            {
                "key": c["strategy"],
                "title": c["title"],
                "angle": c.get("angle"),
                "role": c.get("subject_role"),
            }
            for c in candidates
        ]
        messages: list[Message] = [
            {"role": "system", "content": SELECT_SYSTEM},
            {
                "role": "user",
                "content": SELECT_TEMPLATE.format(
                    message=truncate(message, 300),
                    intent=_intent_for_prompt(intent),
                    cues="；".join(cues[:5]) or "（无）",
                    candidates=json.dumps(briefs, ensure_ascii=False, indent=2),
                ),
            },
        ]
        payload, _ = await registry.call_json(
            "copywriter", messages, required_keys=("present", "recommended"),
            temperature=0.3, max_tokens=200, tag="chat_select",
        )
        raw = payload.get("present")
        if isinstance(raw, str):
            raw = [raw]
        present = [str(k) for k in (raw or []) if str(k) in valid_keys]
        seen: set[str] = set()
        present = [k for k in present if not (k in seen or seen.add(k))][:3]
        if not present:
            present = [fallback.key]
        recommended = str(payload.get("recommended") or "")
        if recommended not in present:
            recommended = present[0]
        if tracer is not None:
            tracer.llm("conversation", model="copywriter", latency_ms=0.0, usage={}, tag="chat_select")
        return present, recommended
    except Exception as exc:  # noqa: BLE001
        logger.warning("方案选择失败，回退单方案: %s", exc)
        metrics.record_degradation(component="chat_select", reason=str(exc)[:200], fallback="single")
        return [fallback.key], fallback.key


async def run_chat(
    session: ChatSession,
    message: str,
    tracer: TraceRecorder,
) -> AsyncIterator[dict[str, Any]]:
    """执行一轮对话，产出事件流供 SSE 转发。"""

    # ── 闸门一：闲聊 / 身份 / 问候 ─────────────────────────────────────────
    # 「你是谁」「你好」不该走进生图管线。固定话术即可，不查史料、不出方案。
    smalltalk = classify_smalltalk(message)
    if smalltalk is not None:
        category, reply = smalltalk
        tracer.emit("conversation_smalltalk", "conversation", category=category)
        yield {"type": "decision", "mode": "smalltalk", "category": category}
        yield {"type": "proposals", "items": [], "mode": "rule"}
        yield {"type": "delta", "text": reply}
        yield {"type": "message", "text": reply, "mode": "rule"}
        yield {"type": "followups", "items": list(SMALLTALK_EXAMPLES)}
        metrics.inc("sxd_chat_turns_total", mode="smalltalk")
        logger.info(
            "对话轮次完成（闲聊/问候，未进入创作管线）",
            extra={"session_id": session.session_id, "category": category},
        )
        return

    # ── 闸门二：领域提问（「纵目面具为什么外凸」）──────────────────────────
    # 只在会话还没有任何方案时生效 —— 已有方案后的「能不能放大一点？」是修改
    # 指令，必须留在创作管线里。
    if _looks_like_question(message) and not any(t.proposals for t in session.turns):
        qa = await _qa_reply(message)
        if qa is not None:
            text, citations = qa
            evidence_items = [
                {
                    "doc_id": item.get("doc_id"),
                    "title": item.get("title"),
                    "text": str(item.get("quote") or item.get("text") or ""),
                    "source_type": item.get("source_type"),
                    "url": item.get("url"),
                }
                for item in citations
            ]
            tracer.emit("conversation_qa", "conversation", citations=len(citations))
            yield {"type": "decision", "mode": "qa"}
            yield {"type": "evidence", "items": evidence_items, "meta": {"mode": "qa"}, "cues": []}
            yield {"type": "proposals", "items": [], "mode": "rule"}
            yield {"type": "delta", "text": text}
            yield {"type": "message", "text": text, "mode": "rule"}
            yield {"type": "followups", "items": list(SMALLTALK_EXAMPLES)}
            metrics.inc("sxd_chat_turns_total", mode="qa")
            logger.info(
                "对话轮次完成（领域提问，转交 QA）",
                extra={"session_id": session.session_id, "citations": len(citations)},
            )
            return

    intent, intent_mode = await resolve_intent(session, message, tracer)

    yield {"type": "intent", "intent": intent, "mode": intent_mode}

    # ── 史料 grounding ──────────────────────────────────────────────────────
    queries = _retrieval_queries(intent)
    evidence: list[dict[str, Any]] = []
    retrieval_meta: dict[str, Any] = {}
    try:
        retriever = await get_retriever()
        chunks, diagnostics = await retriever.search_multi(queries, top_n=4)
        evidence = [chunk.to_dict() for chunk in chunks]
        retrieval_meta = {
            "queries": queries,
            "hits": len(chunks),
            "diagnostics": diagnostics.to_dict(),
        }
    except Exception as exc:  # noqa: BLE001 - 检索失败不应阻断对话
        logger.warning("对话检索失败: %s", exc)
        metrics.inc("sxd_chat_retrieval_failed_total")

    cues = _cues_from_evidence(evidence)
    tracer.emit(
        "conversation_grounded",
        "conversation",
        queries=queries,
        hits=len(evidence),
        cues=cues,
        top=[item.get("title") for item in evidence[:3]],
    )

    yield {
        "type": "evidence",
        "items": evidence,
        "meta": retrieval_meta,
        "cues": cues,
    }

    # ── 决策：先判「要不要问」，再决定「怎么做」─────────────────────────────
    #
    # 这是与「表单式界面」分道扬镳的地方：默认由 AI 把创作决策做完，
    # 只在连画什么都无从谈起时才反问一句；不再每次摆四个方案让用户挑。
    question = clarifying_question(intent)
    if question:
        yield {"type": "decision", "mode": "ask", "question": question}
        # 协议上仍然发 proposals 帧（空列表），前端不必为「追问」分支特判。
        yield {"type": "proposals", "items": [], "mode": "rule"}
        yield {"type": "delta", "text": question}
        yield {"type": "message", "text": question, "mode": "rule"}
        yield {"type": "followups", "items": list(ASK_EXAMPLES)}
        metrics.inc("sxd_chat_turns_total", mode="ask")
        logger.info(
            "对话轮次完成（信息不足，转为追问）",
            extra={"session_id": session.session_id, "kind": intent.get("kind")},
        )
        return

    # ── AI 拍板：呈现几个方案、哪个是推荐项，都由模型决定 ─────────────────
    # 数量不再写死成 1：内容、数量、推荐项都交给 AI；用户只在某些情况下被
    # 邀请改主意，而不是被迫从四个里挑。
    candidates = build_rule_proposals(intent, evidence, cues=cues)
    kind = str(intent.get("kind") or "scene")
    provisional = strategies.pick_strategy(kind, intent)
    titles = [provisional.title]

    # 选择调用与流式回复并发 —— 首字延迟只取决于回复流，选择调用的耗时被掩盖。
    select_task = asyncio.create_task(_select_proposals(message, intent, candidates, cues, tracer))

    reply_chunks: list[str] = []
    try:
        async for delta in _stream_reply(message, intent, evidence, titles, tracer):
            reply_chunks.append(delta)
            yield {"type": "delta", "text": delta}
    except Exception as exc:  # noqa: BLE001 - 流式中断后走模板兜底
        logger.warning("对话回复流式生成失败: %s", exc)
        metrics.record_degradation(component="chat_reply", reason=str(exc)[:200], fallback="template")

    try:
        selected_keys, recommended_key = await select_task
    except Exception:  # noqa: BLE001
        selected_keys, recommended_key = [provisional.key], provisional.key

    selected = [c for c in candidates if c["strategy"] in set(selected_keys)]
    selected.sort(key=lambda c: selected_keys.index(c["strategy"]))

    # ── 结构化润色（只润色被选中的方案）───────────────────────────────────
    refine_task = asyncio.create_task(_refine_proposals(message, intent, cues, selected, tracer))
    try:
        refined = await refine_task
    except Exception as exc:  # noqa: BLE001
        logger.warning("方案润色失败: %s", exc)
        refined = {}

    built = build_rule_proposals(intent, evidence, cues=cues, refined=refined)
    built_by_key = {b["strategy"]: b for b in built}
    proposals: list[dict[str, Any]] = []
    for key in selected_keys:
        proposal = dict(built_by_key.get(key) or next(c for c in candidates if c["strategy"] == key))
        proposal["recommended"] = key == recommended_key
        proposals.append(proposal)

    reply = "".join(reply_chunks).strip()
    if not reply:
        reply = _fallback_reply(intent, evidence, proposals)
        # 无流式内容时一次性下发，保证前端始终能看到完整回复
        yield {"type": "delta", "text": reply}

    recommended = next((p for p in proposals if p["recommended"]), proposals[0])
    mode = "rule+llm" if (reply_chunks and refined) else ("llm" if reply_chunks else "rule")

    yield {
        "type": "decision",
        "mode": "decide",
        "strategy": recommended["strategy"],
        "title": recommended["title"],
        "rationale": recommended.get("rationale") or "",
        "recommended_key": recommended_key,
        "count": len(proposals),
    }
    yield {"type": "proposals", "items": proposals, "mode": mode}
    yield {"type": "message", "text": reply, "mode": mode}

    followups = list(FOLLOWUPS.get(str(intent.get("kind")), FOLLOWUPS["scene"]))
    yield {"type": "followups", "items": followups[:3]}

    metrics.inc("sxd_chat_turns_total", mode=mode)
    logger.info(
        "对话轮次完成",
        extra={
            "session_id": session.session_id,
            "kind": intent.get("kind"),
            "strategy": proposal["strategy"],
            "mode": mode,
        },
    )


__all__ = [
    "run_chat",
    "rule_intent",
    "missing_slots",
    "clarifying_question",
    "build_rule_proposals",
    "build_decision_proposal",
    "FOLLOWUPS",
    "ASK_EXAMPLES",
    "PROFILES",
]
