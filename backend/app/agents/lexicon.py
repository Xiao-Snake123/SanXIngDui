"""文物词典 —— 把「中文界面选项」翻译成「可执行的视觉约束」。

这是本项目里最不起眼但最影响产出质量的一个模块。

通用文生图模型对「青铜大立人」的理解大约等价于「一个青铜人像」，会丢掉
高冠层数、三层交领、赤足方座等**决定风格一致性**的关键特征。因此这里为每件
核心文物维护一份人工整理的 `form_notes`（形制要点）与英文提示词，由 Planner
注入图像 prompt，同时作为质检 Agent 判断「形制忠实度」的先验。

词典是**配置而非代码逻辑**：新增文物只需加一条，不需要改 Agent。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MaterialSpec:
    """文物**材质**的单一事实源。

    为什么材质必须独立于「摄影风格」：
    风格档案描述的是「怎么拍」（光照、景深、色彩克制程度），材质描述的是
    「拍的是什么」（金就是金，玉就是玉）。把两者混在一起会出现下面这种自相矛盾的提示词：

        Key artefact: hammered gold foil ...
        Surface truth: matte oxidized bronze with dense granular patina ...

    模型拿到互相打架的约束只会瞎猜，这也是「生成的图片跟三星堆无关」的直接来源。
    更糟的是质检：原先所有档案都要求画面里 ≥10% 是锈绿，于是黄金面具被判不合格，
    回炉时还会把「补足青铜锈绿与土褐色调」注入提示词 —— 主动把黄金往青铜色上改。

    因此本表同时承担三件事：
      * `tokens` / `surface_truth` → 写进 prompt 的材质真相
      * `family_min` / `forbidden_families` → 客观质检的色彩家族约束
      * `negative` → 材质层面的负向词
    """

    key: str
    label: str
    tokens: tuple[str, ...]
    surface_truth: str
    family_min: dict[str, float] = field(default_factory=dict)
    forbidden_families: tuple[str, ...] = ()
    negative: tuple[str, ...] = ()


MATERIALS: dict[str, MaterialSpec] = {
    "oxidized_bronze": MaterialSpec(
        key="oxidized_bronze",
        label="青铜锈蚀",
        tokens=(
            "matte oxidized bronze surface",
            "dense granular patina in malachite green and earth brown",
            "rounded eroded edges, no mirror-like metal highlight",
        ),
        surface_truth=(
            "matte oxidized bronze with dense granular patina, rounded eroded edges, "
            "no mirror-like metal highlight, restrained saturation"
        ),
        family_min={"green": 0.10, "neutral": 0.15},
        negative=(
            "mirror-polished bronze",
            "brand-new shiny casting",
            "chrome-like metal highlight",
        ),
    ),
    "gold_foil": MaterialSpec(
        key="gold_foil",
        label="金箔 / 金面罩",
        tokens=(
            "ultra-thin hammered gold sheet with soft irregular curvature",
            "warm muted gold, not highly reflective chrome-like gold",
            "slightly dull burnished gold rather than polished mirror gold",
        ),
        surface_truth=(
            "warm muted hammered gold, thin and slightly dull rather than mirror-polished, "
            "faint hammer marks and soft irregular curvature, no green corrosion crust"
        ),
        family_min={"gold": 0.12, "neutral": 0.10},
        # 黄金器物上不该长青铜锈。这条否决的不是「风格偏好」，而是物理事实。
        forbidden_families=("green",),
        negative=(
            "green corrosion crust",
            "oxidised bronze body",
            "chrome-plated mirror gold",
            "polished mirror finish",
        ),
    ),
    "jade": MaterialSpec(
        key="jade",
        label="玉质",
        tokens=(
            "translucent celadon-green jade with natural veining",
            "waxy lustre, softly polished surface",
            "subtle internal cloudiness and mineral inclusions",
        ),
        surface_truth=(
            "translucent celadon-green jade with waxy lustre, natural veining and "
            "mineral inclusions, softly polished rather than glassy"
        ),
        family_min={"green": 0.15, "neutral": 0.10},
        forbidden_families=("gold",),
        negative=(
            "glass-like transparency",
            "bright emerald plastic sheen",
            "metal surface",
            "corroded bronze",
        ),
    ),
    "ivory": MaterialSpec(
        key="ivory",
        label="象牙",
        tokens=(
            "weathered ivory surface, pale cream with warm grey staining",
            "fine longitudinal grain cracks and delamination",
            "chalky matte surface",
        ),
        surface_truth=(
            "weathered ivory in pale cream with warm grey staining, fine longitudinal "
            "cracks, chalky matte surface, never metallic"
        ),
        family_min={"neutral": 0.25, "brown": 0.08},
        forbidden_families=("green", "gold"),
        negative=("metal surface", "bronze patina", "glossy plastic", "polished marble"),
    ),
}

DEFAULT_MATERIAL = MATERIALS["oxidized_bronze"]


@dataclass(frozen=True, slots=True)
class RelicSpec:
    key: str
    label: str
    en: str
    form_notes: str
    scale_hint: str
    # 材质按 key 引用 MATERIALS，而不是每个文物各写一遍 —— 保证同一材质只有一份定义
    material: str = DEFAULT_MATERIAL.key
    extra_tokens: tuple[str, ...] = field(default_factory=tuple)
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """把「漏写结尾逗号」这类错误在导入期就炸掉。

        事故复盘：`extra_tokens=("warm muted gold, ...")` 少了一个逗号，
        它于是成了 `str` 而不是 1 元组。`prompt_block()` 里的 `*self.extra_tokens`
        会把字符串按字符展开，提示词变成：

            ..., w, a, r, m, , m, u, t, e, d, , g, o, l, d, ...

        类型注解不会在运行时生效，所以这个错误一路静默走到了线上出图。
        在这里显式校验，代价是一次导入检查，收益是这一类 bug 永远不会再发生。
        """
        for name in ("extra_tokens", "aliases"):
            value = getattr(self, name)
            if isinstance(value, str):
                raise TypeError(
                    f"RelicSpec({self.key}).{name} 必须是元组，当前是字符串。"
                    f'通常是把 ("a", "b") 写成了 ("a, b") 或漏了结尾逗号：{value!r}'
                )
        if self.material not in MATERIALS:
            raise ValueError(
                f"RelicSpec({self.key}).material={self.material!r} 未在 MATERIALS 中定义"
            )

    @property
    def material_spec(self) -> MaterialSpec:
        return MATERIALS[self.material]

    def prompt_block(self) -> str:
        # 去重：material tokens、extra_tokens 与 form_notes 都由人手维护，
        # 难免出现同一句被写两遍（如「perforations along the edge for attachment」）。
        # 重复不仅浪费提示词预算，还会稀释真正想强调的部分。
        tokens: list[str] = []
        for token in (self.en, *self.material_spec.tokens, *self.extra_tokens):
            cleaned = str(token).strip().rstrip(".")
            if cleaned and cleaned.lower() not in {item.lower() for item in tokens}:
                tokens.append(cleaned)
        return f"{', '.join(tokens)}. {self.form_notes} Scale: {self.scale_hint}."


RELICS: tuple[RelicSpec, ...] = (
    RelicSpec(
        key="standing_figure",
        label="青铜大立人",
        en="the Bronze Standing Figure of Sanxingdui (the tallest complete bronze figure)",
        form_notes=(
            "tall flared crown decorated with spiral and beast-face motifs, "
            "three-layer crossed-collar long robe with incised dragon and silk-worm patterns, "
            "both hands raised in a hollow ring grip at chest height, barefoot standing on a square base, "
            "deliberately elongated arms and oversized palms"
        ),
        scale_hint="about 262 cm tall including the 90 cm base",
        extra_tokens=("immensely tall in frame", "columnar silhouette"),
        aliases=("大立人", "青铜立人", "立人像", "铜立人", "standing figure", "sxd_standing_figure"),
    ),
    RelicSpec(
        key="zongmu_mask",
        label="青铜纵目面具",
        en="the Bronze Zongmu (Protruding-Eye) Mask of Sanxingdui",
        form_notes=(
            "eyes protruding forward as cylindrical tubes, "
            "ears enormously elongated and spread flat to both sides, "
            "broad slightly parted mouth, nose bridge decorated with spiral cloud motifs, "
            "a square hole at the centre of the forehead, strongly abstracted non-realistic facial proportions"
        ),
        scale_hint="about 138 cm wide and 66 cm high, the largest mask found at the site",
        extra_tokens=("imposing frontal composition", "tubular protruding eyes catching the light"),
        aliases=("纵目", "纵目面具", "纵目青铜面具", "凸目面具", "sxd_zongmu_mask"),
    ),
    RelicSpec(
        key="gold_mask",
        label="黄金面具",
        en="the gold mask of Sanxingdui, hammered gold foil",
        form_notes=(
            "ultra-thin hammered gold sheet, raised nose ridge, pierced open eyes, "
            "downturned elongated ears, perforations along the edge for attachment, "
            "soft irregular curvature where the sheet was folded during burial"
        ),
        scale_hint="about 23 cm wide and 28 cm high, roughly 280 g",
        material="gold_foil",
        # 这里原先写的是「perforations along the edge for attachment」，
        # 与 form_notes 逐字重复，只是白占提示词预算。改成真正额外的视觉要点。
        extra_tokens=(
            "reads as a flattened sheet of gold rather than a solid cast sculpture",
            "both pierced eye openings clearly visible against the backing",
        ),
        aliases=("黄金面具", "金面罩", "金面具", "sxd_golden_mask"),
    ),
    RelicSpec(
        key="gold_scepter",
        label="金杖",
        en="the gold scepter of Sanxingdui, hammered gold sheath over a wooden core",
        form_notes=(
            "long slender gold sheath, upper section engraved in double-line intaglio with "
            "two back-to-back birds, two back-to-back fish, a feathered arrow, "
            "and two smiling human heads wearing tall crowns, finely engraved flowing lines"
        ),
        scale_hint="about 142 cm long and 2.3 cm in diameter, about 463 g",
        material="gold_foil",
        extra_tokens=("linear diagonal composition along the shaft",),
        aliases=("金杖", "权杖", "golden scepter"),
    ),
    RelicSpec(
        key="bronze_tree",
        label="青铜神树",
        en="the Great Bronze Sacred Tree of Sanxingdui",
        form_notes=(
            "three tiers of three drooping branches each, nine sacred birds perched on the branch tips, "
            "a bronze dragon with a long body hanging head-downward along the trunk, "
            "circular three-legged base with cloud motifs, resolutely symmetrical yet dynamic"
        ),
        scale_hint="about 396 cm tall after restoration, assembled from hundreds of fragments",
        extra_tokens=("vertical epic composition, tree filling the frame", "visible cast-on joints on branches"),
        aliases=("青铜神树", "神树", "铜神树", "sxd_bronze_tree"),
    ),
    RelicSpec(
        key="jade_zhang",
        label="玉璋",
        en="a jade zhang tablet of Sanxingdui",
        form_notes=(
            "long flat rectangular blade with an oblique cutting edge at one end, "
            "an inner tang at the other end for hafting, "
            "impressed tooth-like flanges along the sides, "
            "fine incised line-art bands showing figures, mountain shapes and cloud-thunder patterns"
        ),
        scale_hint="typically 30 to 70 cm in length",
        material="jade",
        extra_tokens=(),
        aliases=("玉璋", "边璋", "牙璋", "jade zhang"),
    ),
    RelicSpec(
        key="bronze_masks",
        label="铜人面具",
        en="a group of bronze human-face masks of Sanxingdui",
        form_notes=(
            "near-life-size to medium sized bronze faces, "
            "elongated almond eyes, prominent straight nose, thin closed lips, "
            "subtly different brow and jaw treatment in every individual piece, "
            "neutral expression without explicit emotion"
        ),
        scale_hint="from roughly 15 cm up to life size",
        material="oxidized_bronze",
        extra_tokens=("repoussé-thin bronze sheet forming",),
        aliases=("铜人面具", "青铜人头像", "人面具", "铜面具"),
    ),
    RelicSpec(
        key="sun_disc",
        label="青铜太阳形器",
        en="the bronze sun-form disc of Sanxingdui",
        form_notes=(
            "circular wheel-like form, a raised central dome, "
            "five radiating spokes connecting to an outer ring, "
            "visible cracks and re-cast repairs at the junctions"
        ),
        scale_hint="diameter ranging from about 28 cm to 85 cm",
        extra_tokens=("backlit halo composition",),
        aliases=("太阳形器", "太阳轮", "铜太阳形器", "太阳崇拜"),
    ),
    RelicSpec(
        key="bronze_zun",
        label="青铜尊",
        en="a bronze zun vessel of the Sanxingdui assemblage",
        form_notes=(
            "flared mouth, high ring foot, faceted shoulder, "
            "beast-face and cloud-thunder patterns cast in relief on the shoulder, "
            "a form shared with Shang-dynasty central-plains bronzes"
        ),
        scale_hint="typically 20 to 40 cm in height",
        extra_tokens=(),
        aliases=("青铜尊", "尊", "铜尊", "青铜罍", "罍"),
    ),
    RelicSpec(
        key="ivory",
        label="象牙",
        en="ritual elephant tusks from the Sanxingdui sacrificial pits",
        form_notes=(
            "thick curved tusks with weathered cracked surface, "
            "many deliberately cut or snapped and partly charred, "
            "stacked in layered arrangements inside the pit"
        ),
        scale_hint="some tusks exceeding 1 m in length",
        material="ivory",
        extra_tokens=("crosshatched layering emphasising ritual order",),
        aliases=("象牙", "祭祀象牙"),
    ),
)

RELIC_INDEX: dict[str, RelicSpec] = {}
for _spec in RELICS:
    RELIC_INDEX[_spec.label] = _spec
    for _alias in _spec.aliases:
        RELIC_INDEX.setdefault(_alias, _spec)

# 按稳定 key 索引。`RELIC_INDEX` 存的是中文 label 与用户可能输入的别名，
# 用它反查 `gold_mask` 这类内部 key 会 KeyError —— 两者用途不同，不要混用。
RELIC_BY_KEY: dict[str, RelicSpec] = {spec.key: spec for spec in RELICS}


IDENTITIES: dict[str, str] = {
    "大祭司": (
        "an ancient Shu high priest, wearing a tall flared crown and a plain three-layer "
        "crossed-collar robe of undyed hemp or silk, barefoot, holding a jade zhang in a hollow ring grip"
    ),
    "古蜀王者": (
        "a king of ancient Shu, wearing a tall crown and a layered robe with restrained "
        "dragon motifs, dignified upright posture, no later-dynasty regalia whatsoever"
    ),
    "青铜立像化身": (
        "an anthropomorphic embodiment of the Bronze Standing Figure itself, "
        "its silhouette and proportions exactly matching the bronze statue"
    ),
    "神树守护者": (
        "a guardian of the sacred bronze tree, standing beside the trunk, "
        "resting one hand on a drooping branch, candle-lit warm highlights"
    ),
    "纵目面具祭司": (
        "a ritual priest performing the Zongmu mask rite, holding the protruding-eye "
        "bronze mask with both hands lifted toward the sky"
    ),
    "部落首领": (
        "a tribal chief of the ancient Shu people, dressed in plain woven textile "
        "with a distinctive headdress, holding a jade tablet"
    ),
    "考古学者": (
        "a modern archaeologist in a clean white excavation suit working under "
        "controlled lighting inside a temperature-stabilised excavation cabin"
    ),
}

SCENES: dict[str, str] = {
    "博物馆展厅": (
        "a dark-toned modern museum exhibition hall, low ambient illuminance, "
        "focused soft spotlight on the artifact, low-reflectance glass case, "
        "neutral grey and matte black surroundings"
    ),
    "三星堆祭祀坑": (
        "an excavated sacrificial pit in the Sichuan basin, layered soil strata, "
        "scattered sherds and fragmented tusks, overcast diffuse daylight"
    ),
    "青铜神树祭坛": (
        "a ritual platform beneath the great bronze tree, weathered stone slabs, "
        "thin drifting haze, warm rim light from one side"
    ),
    "遗址考古现场": (
        "an active archaeological excavation site with measured grid strings and "
        "documentation markers, flat overcast light, no modern branding"
    ),
    "黑色背景展陈": (
        "a seamless matte black studio backdrop, single soft top light, "
        "no visible environment, pure artifact documentation framing"
    ),
    "古蜀神庙前庭": (
        "the forecourt of an ancient Shu temple, rammed-earth walls with faint red pigment, "
        "wooden pillars, dust suspended in shafts of daylight"
    ),
    # ── 祭祀场景（为「人物主持祭祀」这条链路补的）────────────────────────────
    # 之前没有这几个 key，用户说「在祭祀台前主持祭祀」时场景会回落到默认的
    # 「博物馆展厅」，于是提示词里写着 `a dark-toned modern museum exhibition hall`
    # 而主体是古蜀祭司 —— 一个现代展厅配一个三千年前的祭司，时代错配是必然的。
    "祭祀台": (
        "a low stone ritual altar on a rammed-earth platform before an ancient Shu temple, "
        "bronze vessels arranged on the steps, thin smoke rising from a ritual fire, "
        "warm directional daylight, unmistakeably ancient — no modern structure, "
        "no signage, no glazing"
    ),
    "祭坛": (
        "a raised ceremonial altar of packed earth and rough stone at the centre of an open "
        "forecourt, offerings and bronze vessels placed around it, drifting smoke, "
        "late-afternoon light raking across the packed ground"
    ),
    "神庙祭坛": (
        "the inner altar court of an ancient Shu temple, rammed-earth walls, "
        "row of bronze masks on wooden stands, shallow stone altar in the middle, "
        "firelight and daylight mixing, no later-dynasty architecture"
    ),
}

METHODS: dict[str, str] = {
    "最小干预修复": (
        "minimal-intervention restoration, keep every surviving fragment untouched and "
        "make the reconstructed areas visually distinguishable from original material"
    ),
    "写实复原": (
        "faithful realistic reconstruction of the missing parts, matching the original "
        "casting logic and surface patina, reconstructed regions clearly separable"
    ),
    "数字拼接": (
        "digital fragment reassembly, showing the join lines between fragments, "
        "no smoothing across the seams"
    ),
    "无损清洁": (
        "non-invasive surface cleaning visualisation, retaining stable malachite and azurite "
        "patina, never polishing to bare metal"
    ),
}

ARTIFACT_KINDS = {"artifact", "restoration", "文物修复"}
FIGURE_KINDS = {"figure", "人物还原"}
STYLE_KINDS = {"style", "风格迁移"}


def resolve_relic(*candidates: str | None) -> RelicSpec | None:
    for candidate in candidates:
        if not candidate:
            continue
        text = str(candidate).strip()
        if text in RELIC_INDEX:
            return RELIC_INDEX[text]
        for alias, spec in RELIC_INDEX.items():
            if alias and alias in text:
                return spec
    return None


def resolve_identity(value: str | None) -> str:
    if not value:
        return "a figure of ancient Shu wearing a plain woven robe"
    text = str(value).strip()
    if text in IDENTITIES:
        return IDENTITIES[text]
    for key, description in IDENTITIES.items():
        if key in text:
            return description
    # 前端选项形如「大祭司｜sxd_standing_figure, high priest」
    return text.split("｜")[-1].replace("｜", " ").strip()


def resolve_scene(value: str | None) -> str:
    if not value:
        return "a neutral dark studio environment"
    text = str(value).strip()
    if text in SCENES:
        return SCENES[text]
    for key, description in SCENES.items():
        if key in text:
            return description
    return text.split("｜")[-1].replace("｜", " ").strip()


def resolve_method(value: str | None) -> str:
    if not value:
        return METHODS["最小干预修复"]
    text = str(value).strip()
    if text in METHODS:
        return METHODS[text]
    for key, description in METHODS.items():
        if key in text:
            return description
    return text
