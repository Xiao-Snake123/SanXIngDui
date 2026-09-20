"""提示词方案生成 —— 「推荐几个生图提示词」的核心逻辑。

设计要点
--------
**方案之间必须真的不一样。**
如果四个方案只是把同一句话换个说法，用户选哪个都没区别，这个功能就是装饰。
因此每个方案绑定一套**不同的视觉策略**：机位、光位、景别、主体占比、
目标风格档案、画幅全部不同 —— 它们对应四种不同的拍摄意图，
而不是四段同义改写。

**主体是谁，必须先定下来（`role`）。**
这个字段是被一次真实事故逼出来的。用户要的是「大祭司戴着青铜纵目面具
在祭祀台前主持祭祀」，交付的却是一张**只有面具的考古纪实照**：
画面里没有祭司、没有祭台、没有仪式，还多了卷尺、比例尺和红底金字横幅。

根因是两点叠加：

1. `build_prompt` 里 `if kind == "figure" or (identity and not relic)` ——
   `kind="scene"` 且解析到文物时**整段跳过人物描述**。提示词里因此
   没有一个字说祭司长什么样；模型被要求「加个人」时没有锚点，
   只能退回它最强的先验：现代人、动漫女性。回炉六轮全是这类形象。
2. 场景/人物复用的是**文物视角的构图**（`the artefact in the middle ground`、
   `scale reference`、`reportage framing`），于是模型照着「拍一件文物」执行，
   连卷尺都是提示词自己请来的。

所以现在：`role` 决定**顺序、篇幅、前缀措辞、景别**；
词典决定**事实**（形貌、材质、身份外观）；策略决定**机位与光线**。
三者不越界。

一条不能破的规矩：**降级不等于不描述。**
即使主体是文物、人被降为「承载者」，人也必须被完整描述 ——
只禁「现代服装」而不给古代服装的替代，模型照样只能画出现代人。

降级可用性
----------
本模块**不依赖任何模型**：`build_proposal` 完全由人工整理的文物词典与
风格档案驱动。LLM 只负责「润色方案说明与追加视觉修饰词」，不参与骨架构造。
这样在没有 API Key 的环境里，「推荐提示词」依然是可用且专业的功能，
而不是退化成一句「请配置 API Key」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents import lexicon
from app.quality.style_profiles import DEFAULT_PROFILE, PROFILES, StyleProfile
from app.rag.text import truncate

# ── 主体角色 ────────────────────────────────────────────────────────────────
# 画面主体是谁。它决定块序与篇幅，**不决定「描不描述」**。
ROLE_PERSON = "person"      # 主角是人（祭司、王者…）
ROLE_ARTEFACT = "artefact"  # 主角是器物
ROLE_SCENE = "scene"        # 主角是整个场面
ROLES: tuple[str, ...] = (ROLE_PERSON, ROLE_ARTEFACT, ROLE_SCENE)

# kind → 该 kind 的默认主体。
# `scene` 不在表里：它可能是「人在场景中」（→ person），
# 也可能真的是「场面全景」（→ scene），要由意图内容定，见 resolve_role。
DEFAULT_ROLE_BY_KIND: dict[str, str] = {
    "figure": ROLE_PERSON,
    "artifact": ROLE_ARTEFACT,
    "style": ROLE_ARTEFACT,
}

# 出现这些词说明用户在说「人」的事 —— 用于把 scene 判成 person。
PERSON_SIGNALS: tuple[str, ...] = (
    "主持", "佩戴", "戴着", "拿着", "手持", "举起", "站", "跪", "上身", "全身",
    "半身", "人物", "人像", "服饰", "冠", "祭司", "巫师", "王者",
)

# 出现这些词说明用户要的是**场面**而不是画中人 —— 优先级高于 PERSON_SIGNALS。
# 存在的意义：多轮对话里上一轮提到过人物，「人在场」这个标志会粘住，
# 之后用户说「来一张全景」时不该继续画人物特写。给他一个说法推翻它。
SCENE_SUBJECT_SIGNALS: tuple[str, ...] = (
    "全景", "场面", "俯瞰", "鸟瞰", "布局", "远景", "格局", "整体环境",
)

# 「用户要的是场面」的完整判据：上面的主体信号，外加「场景」这个词本身。
# `resolve_role` 与决策层的追问闸门都用它 —— 两处口径必须一致，
# 否则会出现「判成场面、却又因为没锚点而反问」这种自相矛盾。
SCENE_REQUEST_WORDS: tuple[str, ...] = (*SCENE_SUBJECT_SIGNALS, "场景")


# ── 意图来源账本（stated）──────────────────────────────────────────────────
# 「用户到底说没说」必须有单一事实来源，否则每个取值都要再配一个 `_explicit`
# 影子标志（历史上就有 `identity_explicit`）。影子标志的问题是：加一个默认值就要
# 多配一个标志，下游只要有一处忘了查标志、直接读了值，就把「系统假设」当成
# 「用户事实」——「祭司被塞进器物特写」「博物馆被塞进祭祀坑」两次事故都是这么来的。
#
# 现在约定：`intent[STATED_KEY]` 是「有用户依据的槽位名」列表（可跨轮继承）。
# 判据是 **该值是否扎根于用户请求**，而不是「键存不存在」：
#   - 用户明说「博物馆纪实摄影」        → style 进 stated；
#   - 由场景推断出文物（祭祀坑→纵目面具）→ subject 进 stated（有依据的推断）；
#   - 缺省补的「大祭司 / 黑色背景展陈」  → **不进** stated（纯假设）。
STATED_KEY = "stated"


def was_stated(intent: dict[str, Any] | None, slot: str) -> bool:
    """该槽位是否有用户依据（而非系统缺省补的假设）。"""
    return slot in ((intent or {}).get(STATED_KEY) or ())


def mark_stated(intent: dict[str, Any], *slots: str) -> None:
    """把槽位登记进来源账本。

    每次都生成**新**列表再赋值，避免就地修改与别处共享的列表 ——
    `resolve_intent` 里的 `dict(baseline)` 会共享同一个 stated 列表。
    """
    stated = [item for item in (intent.get(STATED_KEY) or []) if isinstance(item, str)]
    for slot in slots:
        if slot not in stated:
            stated.append(slot)
    intent[STATED_KEY] = stated


def resolve_role(kind: str, intent: dict[str, Any]) -> str:
    """决定「谁是画面主体」。

    优先级：
    1. `intent["subject_role"]` —— 意图解析器显式给出的结论（最可信）；
    2. `kind` 的默认值 —— `figure`→人、`artifact`/`style`→器物；
    3. `scene` 的分岔 —— 提到了人物或其动作就判 `person`，否则 `scene`。

    第 3 条是被那次事故逼出来的：用户说的是「大祭司…主持祭祀的场景」，
    kind 落在 `scene`，于是走了场面策略、人物描述被跳过。
    「场景」这个词不该把画里的人抹掉。

    注意**不能**用 `intent["identity"]` 是否存在来判断人：
    `rule_intent` 会给每个意图都补上缺省身份「大祭司」，它永远有值。
    所以这里查的是 `was_stated`：「原文到底提没提到人」。
    """
    explicit = str(intent.get("subject_role") or "").strip().lower()
    if explicit in ROLES:
        return explicit
    kind = str(kind or "scene")
    if kind == "scene":
        if was_stated(intent, "identity"):
            return ROLE_PERSON
        # 「拍器物」不该被写成「一场祭祀」：用户点名了器物、又没有任何场面诉求时，
        # 主体就是器物。这是那次事故（面具被写成祭祀场面）的**规则层版本**——
        # 有了单方案决策后，判错主体的代价从「挑错一个」变成「没有别的可选」，
        # 所以这一层必须独立判准，不能只靠 LLM 兜底。
        brief = str(intent.get("brief") or "")
        if was_stated(intent, "subject") and not any(
            word in brief for word in SCENE_REQUEST_WORDS
        ):
            return ROLE_ARTEFACT
        return ROLE_SCENE
    return DEFAULT_ROLE_BY_KIND.get(kind, ROLE_SCENE)


@dataclass(frozen=True, slots=True)
class ProposalStrategy:
    key: str
    role: str             # 主体是谁：person / artefact / scene
    title: str
    angle: str            # 一句话说清视觉角度（给用户看）
    profile: str          # 目标风格档案 key
    camera: str           # 英文：机位 / 镜头
    light: str            # 英文：光位 / 光质
    composition: str      # 英文：构图 / 主体占比
    size: tuple[int, int]
    # 风格/效果强度（0~1），仅用于**生成提示词措辞**与界面展示。
    #
    # 这里原本是 steps / cfg / lora_strength 三个参数，它们是扩散采样器的旋钮，
    # 只有 ComfyUI 那条链路才消费。改成 API 出图后它们不再生效，
    # 继续留着就只是让界面显示「steps 32 · cfg 6 · LoRA 0.85」这种不生效的假参数。
    # 而「风格强度」这件事本身是真实的：它改为写进提示词的定语与动词强度。
    # None 表示这个方案不涉及强度概念。
    strength: float | None
    rationale: str        # 为什么给这个方案
    risk: str             # 已知风险
    tags: tuple[str, ...] = field(default_factory=tuple)

    def strength_phrase(self) -> str:
        """把强度翻译成提示词里的英文措辞（模型能理解的那种）。

        用 "the subject" 而不是 "the artefact"：人物还原与场面复原的主体
        都不是器物，写死 artefact 会让模型把画中人当成一件展品。
        """
        if self.strength is None:
            return ""
        if self.strength >= 0.85:
            return (
                "Apply the target style strongly and unmistakably; "
                "the subject silhouette must still be clearly recognisable."
            )
        if self.strength >= 0.6:
            return (
                "Balance the target style with the subject's own material and form; "
                "neither should dominate."
            )
        return (
            "Apply the target style subtly; the subject's own material, form and "
            "composition must dominate."
        )


# ════════════════════════════════════════════════════════════════════════════
#  人物策略 —— 主角是**人**
# ════════════════════════════════════════════════════════════════════════════
PERSON_STRATEGIES: tuple[ProposalStrategy, ...] = (
    ProposalStrategy(
        key="ritual_medium",
        role=ROLE_PERSON,
        title="祭祀现场",
        angle="中景 · 人物为主体 · 祭台与器物作环境",
        profile="cinematic",
        camera="eye-level, 35mm lens, camera at the priest's chest height, slight three-quarter angle",
        light="warm directional side daylight, thin smoke haze catching the light, "
        "shadows readable rather than crushed",
        composition="the priest as the visual anchor occupying the central third in full figure, "
        "arms raised in a ritual gesture, the stone altar and bronze vessels in the middle "
        "ground, the rammed-earth forecourt receding behind",
        size=(1024, 1280),
        strength=None,
        rationale="最贴近「主持祭祀」这一诉求的一版：人物是主体，祭台与器物交代他在哪儿、"
        "在做什么，人和场都在。",
        risk="人物与环境的比例容易失衡 —— 祭台过大就会变回「拍器物」。",
        tags=("叙事", "人物为主", "中景"),
    ),
    ProposalStrategy(
        key="figure_closeup",
        role=ROLE_PERSON,
        title="人物近景",
        angle="半身 · 冠饰与祭服可辨 · 面部细节",
        profile="photo_real",
        camera="waist-up framing, 85mm lens, camera at the figure's eye level",
        light="soft directional daylight from upper left, gentle gradient across face and robe",
        composition="the priest from the waist up, head and crown occupying the upper third, "
        "robe ornament and crossed collar clearly legible, the ritual setting falling away "
        "in soft focus",
        size=(1024, 1280),
        strength=None,
        rationale="用来核对人物形象是否成立的一版：冠饰、祭服、面具与面部在同一景别里，"
        "一眼能看出「是不是一个古蜀祭司」。",
        risk="近景会放大面部与手部，模型在五官上最容易出戏（现代感、动漫化）。",
        tags=("人物为主", "半身", "可核对"),
    ),
    ProposalStrategy(
        key="figure_wide",
        role=ROLE_PERSON,
        title="人物全景",
        angle="全身 · 交代典礼场面 · 低机位显体量",
        profile="cinematic",
        camera="full-figure framing, 28mm lens, camera at hip height, slight low angle",
        light="overcast diffuse daylight with a warm rim from the side, smoke drifting across frame",
        composition="the priest standing full figure at the centre, the altar platform and vessels "
        "below and around him, forecourt and rammed-earth walls filling the background",
        size=(1024, 1280),
        strength=None,
        rationale="既要人也要场面的一版：全身立像能同时交代人物的体量感与祭仪的完整空间。",
        risk="全身景别下人物细节占比小，服饰纹样可能糊掉。",
        tags=("人物为主", "全身", "场合"),
    ),
    ProposalStrategy(
        key="ritual_detail",
        role=ROLE_PERSON,
        title="法器细节",
        angle="近景 · 手中器物 · 祭仪动作的着力点",
        profile="surface_detail",
        camera="medium close-up, 100mm lens, focus on the priest's hands and what they hold",
        light="raking side light revealing surface relief and the grain of the material",
        composition="the priest's hands and the object they hold filling most of the frame, "
        "a sliver of the robe and the mask edge visible at the top so the figure stays present",
        size=(1024, 1024),
        strength=None,
        rationale="祭祀动作的着力点在手与法器上。这一版用来确认「手里拿的是什么」不被画错。",
        risk="景别太近时人物可能被裁掉，画面会变回「器物特写」——已用构图约束保住人的存在。",
        tags=("人物为主", "细节", "微距"),
    ),
)


# ════════════════════════════════════════════════════════════════════════════
#  场面策略 —— 主角是**整个场面**
# ════════════════════════════════════════════════════════════════════════════
SCENE_STRATEGIES: tuple[ProposalStrategy, ...] = (
    ProposalStrategy(
        key="panorama",
        role=ROLE_SCENE,
        title="祭祀全景",
        angle="广角 · 典礼格局可读 · 人小场大",
        profile="cinematic",
        camera="wide establishing shot, 24mm lens, slightly elevated viewpoint, deep depth of field",
        light="warm late-afternoon daylight raking across the forecourt, smoke columns catching the light",
        composition="the whole ritual layout readable — the stone altar at the centre with bronze "
        "vessels arranged around it, figures assembled in two rows, rammed-earth walls closing "
        "the background",
        size=(1280, 960),
        strength=None,
        rationale="最完整的一版：不突出任何单件，用来交代祭仪的空间关系与人的位置。",
        risk="主体分散，单件器物与人物的细节都会被牺牲；质检的细节密度指标可能偏低。",
        tags=("场面为主", "横构图", "全景"),
    ),
    ProposalStrategy(
        key="overhead",
        role=ROLE_SCENE,
        title="俯瞰格局",
        angle="略高位 · 布局关系 · 器物的陈列秩序",
        profile="photo_real",
        camera="slightly elevated three-quarter overview, 24mm lens, camera above head height",
        light="flat overcast daylight, no hard shadows, the layout fully legible",
        composition="the altar platform and its arrangement seen as a whole — the priest and the "
        "vessels forming one readable group, forecourt floor and pits visible around them",
        size=(1024, 1024),
        strength=None,
        rationale="用来读「东西是怎么摆的」：器物与人的相对位置在俯视下最清楚。",
        risk="俯视会削弱三星堆器物的体量压迫感，画面偏「平面图」。",
        tags=("场面为主", "俯视", "布局"),
    ),
    ProposalStrategy(
        key="atmosphere",
        role=ROLE_SCENE,
        title="环境氛围",
        angle="大远景 · 烟雾与暮色 · 人小境大",
        profile="cinematic",
        camera="wide shot, 35mm lens, camera at chest height, foreground elements framing the view",
        light="low-key dusk light, deep shadows, haze and smoke filling the middle ground",
        composition="the ritual space as an atmosphere — the altar and figures small within a large "
        "dark smoke-filled forecourt, the temple structure looming behind",
        size=(1280, 960),
        strength=None,
        rationale="最有史诗感的一版：把人的渺小与神庙的宏大放在一起，适合做封面。",
        risk="暗部占比大，客观质检的对比度与可见细节指标容易压线，可能要回炉提亮。",
        tags=("场面为主", "氛围", "远景"),
    ),
    ProposalStrategy(
        key="night_ritual",
        role=ROLE_SCENE,
        title="夜间祭祀",
        angle="夜景 · 火光为主光源 · 剪影与半照",
        profile="cinematic",
        camera="wide shot, 28mm lens, camera at eye level",
        light="night scene lit only by the ritual fire, warm firelight against deep blue darkness, "
        "smoke lit from below",
        composition="the forecourt at night, the altar fire the brightest point in frame, figures "
        "gathered around it as silhouettes and half-lit forms",
        size=(1280, 960),
        strength=None,
        rationale="换一套光源逻辑来检验：只看火光时，器物形制与人的轮廓还立不立得住。",
        risk="单一火光会让整体色温偏暖，与「克制、低饱和」的风格档案冲突。",
        tags=("场面为主", "夜景", "火光"),
    ),
)


# ════════════════════════════════════════════════════════════════════════════
#  文物策略 —— 主角是**器物**
# ════════════════════════════════════════════════════════════════════════════
ARTIFACT_STRATEGIES: tuple[ProposalStrategy, ...] = (
    ProposalStrategy(
        key="documentation",
        role=ROLE_ARTEFACT,
        title="现状记录",
        angle="平光 · 全器含残缺 · 修复前基准",
        profile="archival",
        camera="straight-on frontal documentation view, 50mm lens, framing the whole object including losses",
        light="soft even diffuse light that reveals the surface, minimal cast shadow",
        composition="the subject centred and filling the frame, every damage boundary "
        "kept visible",
        size=(1024, 1024),
        strength=None,
        rationale="修复工作的第一步永远是「如实记录现状」。这一版不做任何补配美化，作为后续修复的对照基准。",
        risk="看起来「不好看」，容易被误认为没修复；需要在界面上说明它是基准图。",
        tags=("基准", "无美化", "合规"),
    ),
    ProposalStrategy(
        key="minimal_intervention",
        role=ROLE_ARTEFACT,
        title="最小干预复原",
        angle="补配可识别 · 保留残缺 · 可逆",
        profile="restoration",
        camera="three-quarter view, 60mm lens, both the intact and the reconstructed regions readable",
        light="soft directional light from upper left, gentle gradient across the surface",
        composition="object centred, the reconstructed areas clearly separable from the original fragments",
        size=(1024, 1024),
        strength=None,
        rationale="符合文物修复行业规范的一版：补配部分可识别、原始表面保留、不冒充原件。学术报告可直接用。",
        risk="「可识别」的边界靠模型把握，容易做过头变成明显的色块拼接，需要质检盯补配自然度。",
        tags=("规范", "学术", "可逆"),
    ),
    ProposalStrategy(
        key="damage_detail",
        role=ROLE_ARTEFACT,
        title="病害特写",
        angle="微距 · 病害断面 · 诊断依据",
        profile="surface_detail",
        camera="macro close-up on the damaged region, 100mm macro lens",
        light=(
            "raking side light to reveal surface deterioration, powdering and fracture surfaces"
        ),
        composition="the damage zone filling most of the frame, surrounding intact surface as context",
        size=(1024, 1024),
        strength=None,
        rationale=(
            "用于判断病害类型（表面劣化 vs 结构性断裂）与断面形态，"
            "是决定「要不要动、怎么动」的依据。"
        ),
        risk="微距下模型容易把表面画成抽象纹理，失去诊断价值；需要比对客观指标的细节密度。",
        tags=("诊断", "病害", "微距"),
    ),
    ProposalStrategy(
        key="exhibition",
        role=ROLE_ARTEFACT,
        title="展陈效果",
        angle="展厅灯光 · 修复后观感 · 面向公众",
        profile="museum_doc",
        camera="slightly elevated three-quarter view, 50mm lens, object occupying the visual anchor",
        light="focused soft spotlight with soft edge, the subject clearly the brightest area in frame",
        composition="the subject presented as a display piece, clearly the focal point",
        size=(1024, 1280),
        strength=None,
        rationale="面向公众展示的一版：修复成果在真实展陈环境里是什么观感，适合宣传与社媒传播。",
        risk="展厅氛围光容易让器物整体偏暖，与「克制、低饱和」的风格档案冲突。",
        tags=("公众", "展陈", "传播"),
    ),
)


# ════════════════════════════════════════════════════════════════════════════
#  风格迁移策略 —— 同一主体的不同风格强度
# ════════════════════════════════════════════════════════════════════════════
STYLE_STRATEGIES: tuple[ProposalStrategy, ...] = (
    ProposalStrategy(
        key="transfer_light",
        role=ROLE_ARTEFACT,
        title="轻度迁移",
        angle="风格强度 45% · 主体形制优先",
        profile="default",
        camera="same viewpoint and framing as the source image, no camera change",
        light="preserve the source lighting direction",
        composition="preserve the source composition exactly",
        size=(1024, 1024),
        strength=0.45,
        rationale="最保守的一版：只换材质与色调，形制与构图完全不动，适合「想换质感但怕走样」的场景。",
        risk="强度低时风格特征可能不明显，容易被评价为「没变化」。",
        tags=("保守", "保形制", "低强度"),
    ),
    ProposalStrategy(
        key="transfer_balanced",
        role=ROLE_ARTEFACT,
        title="均衡迁移",
        angle="风格强度 70% · 风格与形制兼顾",
        profile="default",
        camera="same viewpoint as the source, minor focal-length compression",
        light="re-light the subject to suit the target style while keeping form readable",
        composition="keep the subject silhouette and ornament layout, allow the background to be restyled",
        size=(1024, 1024),
        strength=0.7,
        rationale="默认推荐：风格特征清晰可辨，同时主体轮廓仍然一眼能认出是三星堆器物。",
        risk="强度处在临界区间，不同主体表现差异较大，建议与轻度/强烈版本对比后再定。",
        tags=("均衡", "推荐", "中强度"),
    ),
    ProposalStrategy(
        key="transfer_strong",
        role=ROLE_ARTEFACT,
        title="强烈迁移",
        angle="风格强度 95% · 风格表现优先",
        profile="default",
        camera="stylised perspective adapted to the target style",
        light="fully re-lit to match the target style",
        composition="the subject is re-rendered in the target idiom, silhouette must still be recognisable",
        size=(1024, 1024),
        strength=0.95,
        rationale="视觉冲击最强的一版，适合明确「就要这个风格」的场景。",
        risk="强度过高时主体形制最容易被风格吞掉；质检会重点检查轮廓可辨识度，大概率需要回炉。",
        tags=("冲击力", "高风险", "高强度"),
    ),
    ProposalStrategy(
        key="transfer_material",
        role=ROLE_ARTEFACT,
        title="材质特写",
        angle="风格作用于材质细节 · 微距",
        profile="surface_detail",
        camera="macro close-up on the surface, 100mm macro lens",
        light="raking light to make the transferred material read clearly",
        composition="surface texture filling the frame, no wide context",
        size=(1024, 1024),
        strength=0.9,
        rationale="把风格迁移的作用点从「整体画面」收窄到「材质表面」，能最直观地看出目标风格到底是什么质感。",
        risk="失去整体语境，不适合作为成品图，更像技术验证图。",
        tags=("材质", "验证", "微距"),
    ),
)


# role → 策略集。**不存在 kind → 策略集**：构图取决于主体是谁，
# 不取决于用户在表单里点了哪个 tab。
# 注意拼写：role 用 `artefact`（与 ROLE_ARTEFACT 一致），
# 而 `kind` 的取值是 `artifact`（对外接口的历史写法，不能改）。
# 这两处不一致是既成事实，写代码时以 `ROLE_*` 常量为准。
ROLE_SETS: dict[str, tuple[ProposalStrategy, ...]] = {
    ROLE_PERSON: PERSON_STRATEGIES,
    ROLE_ARTEFACT: ARTIFACT_STRATEGIES,
    ROLE_SCENE: SCENE_STRATEGIES,
}


def strategies_for(kind: str, intent: dict[str, Any] | None = None) -> tuple[ProposalStrategy, ...]:
    """按 kind + intent 选出该给的四个方案。

    风格迁移是唯一的例外：它的主体是「源图」，四种强度档位自成一套，
    不适用人物/器物/场面的划分。
    """
    kind = str(kind or "scene")
    if kind == "style":
        return STYLE_STRATEGIES
    return ROLE_SETS[resolve_role(kind, intent or {})]


def _by_key(group: tuple[ProposalStrategy, ...], key: str) -> ProposalStrategy:
    """按 key 取组内策略；取不到时退回组内第一项（组永远非空）。"""
    for strategy in group:
        if strategy.key == key:
            return strategy
    return group[0]


def pick_strategy(kind: str, intent: dict[str, Any] | None = None) -> ProposalStrategy:
    """替 AI 选出**一个**最合适的视觉角度。

    取向与 `strategies_for` 相反：那边把四个角度摆出来让用户挑（表单思维），
    这边是 AI 自己拍板。依据只能来自用户**已经说过**的信息；没有依据时
    回落到该主体最稳妥的角度 —— 而不是反问用户（要不要反问由决策层统一裁决）。

    判据刻意只用两类信号：**意图槽位**（method/scene/strength）与 `brief` 里的
    原始关键词。够用且可预测；真要更细的取舍交给下游润色模型。
    """
    intent = intent or {}
    kind = str(kind or "scene")
    text = str(intent.get("brief") or "")

    if kind == "style":
        if any(word in text for word in ("材质", "质感", "肌理")):
            return _by_key(STYLE_STRATEGIES, "transfer_material")
        try:
            strength = int(intent.get("strength") or 0)
        except (TypeError, ValueError):
            strength = 0
        if strength >= 85:
            return _by_key(STYLE_STRATEGIES, "transfer_strong")
        if 0 < strength <= 45:
            return _by_key(STYLE_STRATEGIES, "transfer_light")
        return _by_key(STYLE_STRATEGIES, "transfer_balanced")

    role = resolve_role(kind, intent)
    group = ROLE_SETS[role]

    if role == ROLE_ARTEFACT:
        if kind == "artifact" or intent.get("method"):
            return _by_key(group, "minimal_intervention")
        if any(word in text for word in ("病害", "断面", "开裂", "残缺", "锈蚀")):
            return _by_key(group, "damage_detail")
        if "展陈" in text or str(intent.get("scene") or "") in {"博物馆展厅", "黑色背景展陈"}:
            return _by_key(group, "exhibition")
        return _by_key(group, "documentation")

    if role == ROLE_PERSON:
        if any(word in text for word in ("全身", "全景", "立像")):
            return _by_key(group, "figure_wide")
        if any(word in text for word in ("半身", "近景", "面部", "五官", "特写")):
            return _by_key(group, "figure_closeup")
        if any(word in text for word in ("法器", "手中", "细节")):
            return _by_key(group, "ritual_detail")
        return _by_key(group, "ritual_medium")

    # ROLE_SCENE
    if any(word in text for word in ("俯瞰", "布局", "格局", "陈列", "怎么摆")):
        return _by_key(group, "overhead")
    if any(word in text for word in ("夜", "火光", "篝火")):
        return _by_key(group, "night_ritual")
    if any(word in text for word in ("氛围", "烟雾", "暮色", "史诗")):
        return _by_key(group, "atmosphere")
    return _by_key(group, "panorama")


# ── 提示词组装 ──────────────────────────────────────────────────────────────
# 人物/场面任务里，正向提示词若写了 reportage / scale reference 这类
# 考古记录照的范式词，模型就会照着加卷尺、比例尺、红底金字横幅 ——
# 实测就是这么来的。这一组把它们显式禁掉。
_RITUAL_NEGATIVE = (
    "modern person, contemporary face, modern hairstyle, straight long black hair, "
    "cosplay, plastic costume, sneakers, eyeglasses, "
    "tape measure, ruler, scale bar, measuring tool, exhibit label, caption banner, "
    "red banner with Chinese characters, museum signage"
)

# 器物/展陈类画面里，背景塞进现代观众是模型很常见的填充物 ——
# 实测 VLM 就抓到了「背景展柜玻璃反光中可见现代游客穿牛仔裤」。
# 观众不在画面承诺范围内，显式排除；人为主体/场面为主体时不能加这条
# （那两个 role 本来就要画古代人物）。
_BYSTANDER_NEGATIVE = (
    "modern visitors, exhibition crowd, bystanders, people in the background, "
    "reflection of people, contemporary clothing in the background"
)

# 戴在脸上的器物（用于把「面具」写成 worn 而不是 displayed）
_WORN_RELIC_KEYS = frozenset({"zongmu_mask", "gold_mask", "bronze_masks"})


def _worn_phrase(relic: Any) -> str:
    """器物与人物的关系措辞。

    面具必须写成「戴在他脸上」，否则模型会把它画成展柜里的标本 ——
    那正是最初那次失败：画面里只有一件面具，没有人。
    """
    if getattr(relic, "key", "") in _WORN_RELIC_KEYS:
        return "worn on the figure's face as a ritual mask, not displayed as a museum specimen"
    return "carried or placed alongside the figure, integrated into the scene rather than displayed"


def _sentence(text: str) -> str:
    """把一段话收尾成单句。

    `relic.prompt_block()` 自带句点，而它内部的 `scale_hint` 结尾也常有标点，
    拼起来会产出 `...found at the site..` 这种双句点 —— 实测出现过。
    统一在这里收尾，而不是去改词典（词典的句点是对的，错的是拼接）。
    """
    return text.strip().rstrip(".") + "."


def build_prompt(
    *,
    kind: str,
    intent: dict[str, Any],
    strategy: ProposalStrategy,
    profile: StyleProfile,
    evidence_cues: list[str] | None = None,
) -> tuple[str, str]:
    """把「主体 + 情境 + 视觉策略 + 风格 token + 史料线索」拼成出图 prompt。

    块序由 `strategy.role` 决定，不由 `kind` 猜 —— 第一块权重最高，
    主体必须排在最前面。
    """
    blocks: list[str] = []

    subject = str(intent.get("subject") or "").strip()
    relic = lexicon.resolve_relic(subject)
    scene = str(intent.get("scene") or "").strip()
    identity = str(intent.get("identity") or "").strip()
    # 人物描述：只要意图里有身份就一定要给出来（哪怕角色是「承载者」）。
    # 只禁「现代服装」而不给古代服装的替代，模型只能画出现代人。
    person = lexicon.resolve_identity(identity) if identity else ""
    # 器物主体的任务里，只有用户**明确提到人**时才补承载者，
    # 否则「给面具拍证件照」会被塞进一个不相干的祭司。
    # 判据是来源账本，不是「identity 有没有值」—— 后者被缺省补成了「大祭司」。
    bearer_needed = bool(person) and was_stated(intent, "identity")
    # 小模型写的主体动作句（见 `agent.INTENT_SYSTEM` 的 `rewrite` 字段）。
    #
    # 分工是刻意的：**外观来自词典，动作与主次来自模型。**
    # 外观必须由词典给 —— 模型写材质服饰会编（「青铜」写成「黄铜」、
    # 「三层祭服」写成「唐装」，实测都出现过）。而「他在做什么、谁是画面主角」
    # 是规则写不好的，交给模型一两句话就够。
    rewrite = str(intent.get("rewrite") or "").strip()

    # 主体块。分支**只看 `strategy.role`**，不再看 `kind`。
    #
    # 事故复盘：原实现用 `kind` 选分支、用 `role` 选子分支，两者不一致时就会错配。
    # 实测输入「青铜纵目面具在博物馆展厅里，电影级写实风格」：
    #   * LLM 正确判出 `subject_role=artefact`（主体确实是器物），方案层也对
    #     （拿到器物策略集，四个方案是现状存档／可逆修复／病害特写／展陈实况）；
    #   * 但 `kind` 仍是 `scene`，于是进了 `if kind in {"scene","figure"}`，
    #     而 `role` 既不等于 person、`else` 又默认当成场面 ——
    #     一张「拍面具」的图被写成「一场祭祀仪式」，还凭空多出一个祭司。
    #
    # 只有 `kind == "style"` 例外：风格迁移的主体是源图，与三分法无关。
    if kind == "style":
        preset = str(intent.get("style_preset") or intent.get("style") or "").strip()
        if preset:
            blocks.append(f"Style transfer target: {preset}.")
        if subject:
            blocks.append(f"Subject: {subject} (must stay recognisably Sanxingdui).")
        if bearer_needed:
            blocks.append(f"Bearer (secondary, must still read as an ancient Shu priest): {person}.")
    elif strategy.role == ROLE_PERSON:
        if person:
            # 外观（词典型）在前，动作与主次（模型写）在后，拼成第一块。
            # 没有 `rewrite` 时退回原来写死的锚点句，行为与之前一致。
            blocks.append(
                _sentence(
                    f"Main subject: {person}. "
                    + (
                        rewrite
                        if rewrite
                        else "The figure is the visual anchor of the image, "
                        "occupying the central third in full figure."
                    )
                )
            )
        if relic:
            blocks.append(f"Worn or held by the figure: {relic.prompt_block()} — {_worn_phrase(relic)}.")
        if scene:
            blocks.append(
                f"Setting (background context only): {lexicon.resolve_scene(scene)}."
            )
    elif strategy.role == ROLE_SCENE:
        if scene:
            blocks.append(
                _sentence(
                    f"Main subject: the scene as a whole — {lexicon.resolve_scene(scene)}. "
                    + (
                        rewrite
                        if rewrite
                        else "The space must read as one inhabited place, "
                        "not a single object on display."
                    )
                )
            )
        # 只有原文提到过人时才画人。
        # `identity` 被 `rule_intent` 用 `setdefault` 填成「大祭司」，**永远非空** ——
        # 直接拿它判断，会把一个祭司塞进每一个场面提示词里，
        # 哪怕用户只说了「展厅」和「面具」。器物分支早有这个门控，
        # 这里漏了；漏的代价就是这次凭空长出来的人。
        if person and bearer_needed:
            blocks.append(f"Central figure (middle distance): {person}.")
        if relic:
            if person and bearer_needed:
                blocks.append(f"Worn or carried by the central figure: {relic.prompt_block()}.")
            else:
                blocks.append(f"Objects within the scene: {relic.prompt_block()}.")
    else:  # ROLE_ARTEFACT
        base = _sentence(
            relic.prompt_block() if relic else (subject or "a damaged Sanxingdui bronze")
        )
        blocks.append(
            _sentence(f"Subject artefact: {base} {rewrite}") if rewrite else f"Subject artefact: {base}"
        )
        # 修复手法只在「真给了方式」或「本就是修复任务」时输出 ——
        # 「面具在展厅里」不该被写成「修复方式：最小干预」。
        method = str(intent.get("method") or "").strip()
        if method or kind == "artifact":
            blocks.append(f"Restoration approach: {lexicon.resolve_method(method or None)}.")
        mode = str(intent.get("mode") or "").strip()
        if mode:
            blocks.append(f"Presentation mode: {mode}.")
        # 器物主体的任务同样要交代环境，否则「在展厅里」这个信息整个丢掉
        if scene:
            blocks.append(f"Setting (background context): {lexicon.resolve_scene(scene)}.")
        if bearer_needed:
            blocks.append(
                f"Bearer (secondary, out of focus — must still read as an ancient Shu "
                f"priest): {person}."
            )

    # 环境（背景、光源氛围、地面）一律只由「场景」提供，策略与风格档案
    # 只描述与场景无关的处理方式（居中、平光显表面、低饱和、残缺可见）。
    # 历史上策略/风格曾把「中性灰底 / no shadow / 展厅」写死进构图与光线块，
    # 与真实的祭祀坑/祭坛场景直接冲突，出图模型只能把器物画成贴在土上的 CG 模型。
    # 把环境从这里彻底剥离后，任何场景都不会再与处理方式打架。
    blocks.append(f"Composition: {strategy.composition}.")
    blocks.append(f"Camera: {strategy.camera}.")
    blocks.append(f"Lighting: {strategy.light}.")
    blocks.append(f"Style: {', '.join(profile.prompt_tokens)}.")

    # 风格强度只能靠提示词措辞表达 —— API 模型没有强度旋钮。
    # 这样「轻度/均衡/强烈」三档的差异是真实生效的，而不是界面上的一个数字。
    strength_phrase = strategy.strength_phrase()
    if strength_phrase:
        blocks.append(f"Style strength: {strength_phrase}")

    # Surface truth 描述的是「材质真相」，因此必须来自文物自身，不能写死。
    # 旧实现无条件输出 "matte oxidized bronze with dense granular patina"：
    #   黄金面具 → 同时被告知是金箔又是氧化青铜，自相矛盾；
    #   人物还原 → 把人物的皮肤与布衣描述成青铜锈层，完全跑偏。
    # 人物为主体时这一块要**跳过**：那块青铜锈层描述的可能是他戴的面具，
    # 写在最外层会让模型把整幅画面的材质都理解成青铜。
    material = relic.material_spec if relic else None
    if material is not None and strategy.role != ROLE_PERSON:
        blocks.append(f"Surface truth: {material.surface_truth}.")
    elif material is None:
        blocks.append(
            "Surface truth: faithful rendition of the actual material described above, "
            "no invented surface finish."
        )

    if evidence_cues:
        cues = "; ".join(str(item) for item in evidence_cues[:4] if str(item).strip())
        if cues:
            blocks.append(f"Evidence-based visual cues: {cues}.")

    # 用户原话整句兜底 —— **只在模型没写出 `rewrite` 时才用**。
    #
    # 这一块曾经是「用户的真实诉求在提示词里唯一的落点」，而且是最后一块。
    # 后果实测过：整段模板都在讲器物（材质、机位、比例尺），
    # 用户要的「有个祭司在主持祭祀」是末尾一句脚注 —— 模型自然只画器物。
    # 现在那句话由 `rewrite` 写在**第一块**，这里就不该再复述一遍：
    # 重复会稀释第一块的权重，白白吃掉免费通道只有 256 字的提示词预算。
    brief = str(intent.get("brief") or "").strip()
    if brief and not rewrite:
        blocks.append(f"Additional requirement from the user: {truncate(brief, 240)}.")

    negative_parts = [profile.negative_prompt()]
    if material is not None:
        negative_parts.append(", ".join(material.negative))
    if strategy.role in {ROLE_PERSON, ROLE_SCENE}:
        negative_parts.append(_RITUAL_NEGATIVE)
    if strategy.role == ROLE_ARTEFACT:
        negative_parts.append(_BYSTANDER_NEGATIVE)
    if kind == "artifact":
        negative_parts.append(
            "over-restored, polished bronze, brand new look, "
            "restored area indistinguishable from original"
        )
    if kind == "style":
        negative_parts.append("deformed silhouette, unrecognisable subject, lost ornament layout")

    return " ".join(blocks), ", ".join(part for part in negative_parts if part)


def build_proposal(
    *,
    kind: str,
    intent: dict[str, Any],
    strategy: ProposalStrategy,
    evidence_cues: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    rationale_override: str | None = None,
    extra_directives: list[str] | None = None,
    profile_override: str | None = None,
) -> dict[str, Any]:
    """组装一个方案。

    `profile_override` 优先于 strategy 自带的 profile：风格迁移时，
    「用户要的目标风格」才是原真性的标尺，不能让策略里写死的档位盖掉它。
    """
    profile = PROFILES.get(profile_override or strategy.profile, DEFAULT_PROFILE)
    prompt, negative = build_prompt(
        kind=kind, intent=intent, strategy=strategy, profile=profile, evidence_cues=evidence_cues
    )
    if extra_directives:
        prompt = prompt + " " + " ".join(str(item).strip() for item in extra_directives[:5]) + "."

    return {
        "id": f"{kind}:{strategy.key}",
        "strategy": strategy.key,
        # 主体是谁必须透出到界面：用户选的其实是「这版主角是人／是器物／是场面」，
        # 比让他理解机位与景别直观得多。
        "subject_role": strategy.role,
        "title": strategy.title,
        "angle": strategy.angle,
        "style_profile": profile.key,
        "style_label": profile.label,
        "prompt": prompt,
        "negative_prompt": negative,
        "rationale": rationale_override or strategy.rationale,
        "risk": strategy.risk,
        "tags": list(strategy.tags),
        # 只上报真实生效的参数。
        # 原先这里放的是 steps / cfg / lora_strength，它们是 ComfyUI 采样器的旋钮，
        # 改成 API 出图后一个都不会被下发 —— 界面上却照旧写着
        # 「steps 32 · cfg 6 · LoRA 0.85」，等于向用户展示一组假旋钮。
        "params": {
            "width": strategy.size[0],
            "height": strategy.size[1],
            "style_strength": strategy.strength,
            "prompt_extend": False,
        },
        "evidence_ids": (evidence_ids or [])[:5],
        "generated_by": "rule",
    }


def to_image_spec(proposal: dict[str, Any]) -> dict[str, Any]:
    """把用户选中的方案转成 Planner 可直接消费的 `image_spec`。"""
    params = proposal.get("params") or {}
    return {
        "prompt": proposal.get("prompt") or "",
        "negative_prompt": proposal.get("negative_prompt") or "",
        "width": int(params.get("width") or 1024),
        "height": int(params.get("height") or 1280),
        # 默认关闭：提示词是我们拼好且承诺逐字使用的
        "prompt_extend": bool(params.get("prompt_extend") or False),
        "style_tokens": [],
        "source": "user_selected_proposal",
        "proposal_id": proposal.get("id"),
        "proposal_title": proposal.get("title"),
    }
