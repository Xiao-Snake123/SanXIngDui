"""风格档案（Style Profile）—— 风格的**单一事实来源**。

一个反复出现的架构问题是：风格约束散落在 frontend 的选项文案、Planner 的 prompt、
质检的评分标准三处，改一处忘两处，导致「生成的图」和「检查的标准」不是一回事。

因此这里把每种风格收敛成一份结构化档案，同时供三方消费：
- Planner 取 `prompt_tokens` / `negative_tokens` 注入图像 prompt；
- 客观质检取 `saturation_range` / `specular_max` / `family_min` 等数值区间；
- VLM 裁判取 `rubric` 作为评分细则，避免自由发挥。

风格档案的数值区间来自对**馆藏文物官方影像**的统计（色调分布、饱和度、
高光占比的经验区间），而不是拍脑袋设定。修改时请连同 `EVAL` 基准一起回归。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StyleProfile:
    key: str
    label: str
    aliases: tuple[str, ...]
    prompt_tokens: tuple[str, ...]
    negative_tokens: tuple[str, ...]
    # 色彩家族最小占比（green=锈绿 / blue=蓝铜 / brown=土褐 / gold=金黄 / neutral=中性灰）
    family_min: dict[str, float] = field(default_factory=dict)
    saturation_range: tuple[float, float] = (0.05, 0.40)
    specular_max: float = 0.06          # 近白高光像素占比上限（哑光是氧化青铜的核心特征）
    edge_density_range: tuple[float, float] = (0.04, 0.32)
    contrast_range: tuple[float, float] = (0.06, 0.45)
    target_size: tuple[int, int] = (1024, 1280)
    creative: bool = False              # 创作型风格放宽客观阈值，主要交给 VLM 判
    rubric: str = ""

    def negative_prompt(self) -> str:
        return ", ".join(self.negative_tokens)


_ANACHRONISM_NEGATIVE = (
    "iron tools, porcelain, blue-and-white porcelain, Chinese characters inscription, "
    "Buddhist statue elements, paper books, modern furniture, glassware, plastic, "
    "modern clothing, ming dynasty robe, neon sign, text watermark, signature, logo"
)

_QUALITY_NEGATIVE = (
    "lowres, blurry, jpeg artifacts, deformed, extra limbs, bad anatomy, "
    "oversaturated, cartoon, anime, flat illustration, cgi, plastic texture, "
    "mirror-like metallic shine, fluorescent green"
)

PROFILES: dict[str, StyleProfile] = {
    "photo_real": StyleProfile(
        key="photo_real",
        label="真实照片风格",
        aliases=("真实照片风格", "照片", "写实", "photorealistic", "realistic photo"),
        prompt_tokens=(
            "photorealistic photograph",
            "shot on full-frame camera, 85mm lens",
            "natural soft light with gentle falloff",
            # 材质断言不写在这里：档案管「怎么拍」，材质管「拍的是什么」。
            # 原先此处写死“matte oxidized bronze surface, fine granular patina”，
            # 导致黄金面具 / 玉器 / 人物都被强行描述成氧化青铜。
            "true-to-material surface with no invented finish",
            "shallow depth of field",
            "faithful color rendition, restrained saturation",
        ),
        negative_tokens=(*_QUALITY_NEGATIVE.split(", "), *_ANACHRONISM_NEGATIVE.split(", ")),
        family_min={"green": 0.10, "brown": 0.08, "neutral": 0.15},
        saturation_range=(0.06, 0.32),
        specular_max=0.05,
        edge_density_range=(0.05, 0.28),
        contrast_range=(0.08, 0.40),
        rubric=(
            "1) 器表是否与所指材质一致（青铜为哑光锈层，金为哑金箔，玉为温润玉质），且无镜面反光；"
            "2) 色彩是否落在该材质的合理区间且不艳俗；"
            "3) 器物形制是否与实物一致（无凭空补配的部件）；"
            "4) 光影方向是否与场景逻辑一致。"
        ),
    ),
    "museum_doc": StyleProfile(
        key="museum_doc",
        label="博物馆纪实摄影",
        aliases=("博物馆纪实摄影", "博物馆", "展陈", "museum documentary"),
        prompt_tokens=(
            "museum documentary photograph",
            "focused soft spotlight with soft edge, the artifact clearly the focal point",
            "matte patina, no specular hotspot",
            "archival quality, true-to-artifact color",
        ),
        negative_tokens=(*_QUALITY_NEGATIVE.split(", "), *_ANACHRONISM_NEGATIVE.split(", ")),
        family_min={"green": 0.10, "blue": 0.04, "brown": 0.06, "neutral": 0.20},
        saturation_range=(0.05, 0.26),
        specular_max=0.03,
        edge_density_range=(0.04, 0.24),
        contrast_range=(0.10, 0.42),
        rubric=(
            "1) 背景是否为深色低照度展厅环境；"
            "2) 是否存在展柜玻璃反光或高光溢出；"
            "3) 器物是否被重点照明而非整体平光；"
            "4) 色彩是否克制冷调，避免暖黄滤镜感。"
        ),
    ),
    "cinematic": StyleProfile(
        key="cinematic",
        label="电影级写实",
        aliases=("电影级写实", "电影感", "cinematic"),
        prompt_tokens=(
            "cinematic still, anamorphic lens",
            "low-key dramatic lighting with practical rim light",
            "deep shadows, volumetric haze",
            "true-to-material surface, no invented finish, no specular hotspot",
        ),
        negative_tokens=(*_QUALITY_NEGATIVE.split(", "), *_ANACHRONISM_NEGATIVE.split(", ")),
        family_min={"green": 0.08, "brown": 0.06, "neutral": 0.15},
        saturation_range=(0.08, 0.36),
        specular_max=0.07,
        edge_density_range=(0.05, 0.30),
        contrast_range=(0.14, 0.50),
        rubric=(
            "1) 是否保留了大量暗部细节而非死黑；"
            "2) 轮廓光是否自然、不产生金属镜面感；"
            "3) 雾气/体积光是否适度，未遮蔽器物主体；"
            "4) 时代元素是否合规。"
        ),
    ),
    "archival": StyleProfile(
        key="archival",
        label="考古档案照片",
        aliases=("考古档案照片", "档案", "archival"),
        prompt_tokens=(
            "archaeological archival photograph",
            "flat even lighting, no artistic effect",
            "full artifact visible including damaged areas",
        ),
        negative_tokens=(*_QUALITY_NEGATIVE.split(", "), *_ANACHRONISM_NEGATIVE.split(", ")),
        family_min={"green": 0.08, "neutral": 0.20},
        saturation_range=(0.02, 0.18),
        specular_max=0.03,
        edge_density_range=(0.04, 0.26),
        contrast_range=(0.05, 0.30),
        rubric=(
            "1) 是否为平光、无明显艺术化处理；"
            "2) 残缺部位是否完整可见，未被美化掩盖；"
            "3) 是否具备标本记录的构图特征（正视、居中含比例参照）；"
            "4) 饱和度是否足够低。"
        ),
    ),
    "cyberpunk": StyleProfile(
        key="cyberpunk",
        label="赛伯朋克风格",
        aliases=("赛伯朋克风格", "赛博朋克", "赛博古蜀", "cyberpunk"),
        prompt_tokens=(
            "cyberpunk aesthetic reinterpretation",
            "neon accent lighting, teal and magenta rim light",
            "futuristic atmospheric fog",
            "keep the artifact silhouette and volumetric proportions intact",
        ),
        negative_tokens=("lowres, blurry, deformed, bad anatomy, extra limbs",),
        family_min={},
        saturation_range=(0.12, 0.65),
        specular_max=0.18,
        edge_density_range=(0.03, 0.40),
        contrast_range=(0.12, 0.60),
        creative=True,
        rubric=(
            "1) 创作型风格，色彩与光照放宽；"
            "2) 但**器物轮廓与体量比例必须保持三星堆特征**（这是唯一的硬约束）；"
            "3) 不得出现现代文字、品牌标识或现实品牌元素。"
        ),
    ),
    "epic_oil": StyleProfile(
        key="epic_oil",
        label="史诗油画风",
        aliases=("史诗油画风", "油画", "oil painting"),
        prompt_tokens=(
            "epic oil painting, visible brush strokes",
            "dramatic chiaroscuro lighting",
            "rich but earthy palette, aged varnish warmth",
        ),
        negative_tokens=(*_QUALITY_NEGATIVE.split(", "), *_ANACHRONISM_NEGATIVE.split(", ")),
        family_min={"brown": 0.12, "green": 0.05, "neutral": 0.10},
        saturation_range=(0.10, 0.42),
        specular_max=0.10,
        edge_density_range=(0.06, 0.35),
        contrast_range=(0.12, 0.50),
        rubric=(
            "1) 是否具备可辨识的笔触肌理而非照片质感；"
            "2) 用色是否为沉稳的土色系而非艳丽；"
            "3) 器物形制是否仍可辨识；"
            "4) 时代元素是否合规。"
        ),
    ),
    "gongbi": StyleProfile(
        key="gongbi",
        label="工笔重彩",
        aliases=("工笔重彩", "工笔", "gongbi"),
        prompt_tokens=(
            "gongbi meticulous brushwork, fine line drawing",
            "mineral pigment colors, layered flat washes",
            "clean linework defining every ornament detail",
        ),
        negative_tokens=(*_QUALITY_NEGATIVE.split(", "), *_ANACHRONISM_NEGATIVE.split(", ")),
        family_min={"green": 0.08, "brown": 0.06},
        saturation_range=(0.12, 0.45),
        specular_max=0.08,
        edge_density_range=(0.08, 0.40),
        contrast_range=(0.10, 0.45),
        rubric=(
            "1) 线条是否清晰、有笔锋，未糊成色块；"
            "2) 矿物色是否沉稳，未出现荧光色；"
            "3) 纹饰是否被逐一笔绘而非概括；"
            "4) 时代元素是否合规。"
        ),
    ),
    "restoration": StyleProfile(
        key="restoration",
        label="文物修复写实",
        aliases=("文物修复写实", "修复", "文物修复", "最小干预修复", "写实复原"),
        prompt_tokens=(
            "museum-grade artifact restoration visualisation",
            "reconstructed missing parts visually distinguishable from original fragments",
            "matte patina preserved, no polishing or cleaning artifacts",
            "even diffuse lighting, frontal documentation framing",
        ),
        negative_tokens=(
            *_QUALITY_NEGATIVE.split(", "),
            *_ANACHRONISM_NEGATIVE.split(", "),
            "restored area indistinguishable from original",
            "over-restored, polished bronze, brand new look",
        ),
        family_min={"green": 0.12, "brown": 0.06, "neutral": 0.12},
        saturation_range=(0.04, 0.26),
        specular_max=0.04,
        edge_density_range=(0.05, 0.28),
        contrast_range=(0.06, 0.35),
        rubric=(
            "1) 补配区域是否可被识别（不冒充原件）；"
            "2) 是否保留了残缺信息而非整体美化成完好；"
            "3) 锈色是否保留（不得打磨成新铜）；"
            "4) 形制是否忠实于出土状态。"
        ),
    ),
    "sketch": StyleProfile(
        key="sketch",
        label="考古素描",
        aliases=("考古素描", "素描", "线稿", "sketch"),
        prompt_tokens=(
            "archaeological documentation line drawing",
            "graphite hatching building form and volume",
            "no photographic shading, no colour fill",
            "clean contour lines with measured proportion marks",
        ),
        negative_tokens=(
            "photorealistic, colour photograph, oversaturated, 3d render, "
            "oil painting, watercolour, glossy highlight, text watermark, signature",
        ),
        family_min={"neutral": 0.65},
        saturation_range=(0.0, 0.14),
        specular_max=0.06,
        edge_density_range=(0.08, 0.42),
        contrast_range=(0.08, 0.45),
        rubric=(
            "1) 画面是否为单色线稿，无写实光影；"
            "2) 线条是否勾勒出器物的结构与纹饰层次；"
            "3) 是否避免出现摄影质感的材质表现；"
            "4) 器物形制是否可辨识。"
        ),
    ),
    "temple_mural": StyleProfile(
        key="temple_mural",
        label="神庙壁画",
        aliases=("神庙壁画", "壁画", "彩绘", "mural"),
        prompt_tokens=(
            "ancient temple mural painting on rammed earth wall",
            "mineral pigment palette: ochre, malachite green, carbon black, cinnabar",
            "aged and flaking surface with visible losses",
            "flat decorative rendering, no cast shadows",
        ),
        negative_tokens=(*_QUALITY_NEGATIVE.split(", "), *_ANACHRONISM_NEGATIVE.split(", ")),
        family_min={"brown": 0.14, "green": 0.06, "neutral": 0.10},
        saturation_range=(0.10, 0.42),
        specular_max=0.05,
        edge_density_range=(0.05, 0.32),
        contrast_range=(0.08, 0.40),
        rubric=(
            "1) 是否为矿物色平的壁画质感，而非照片；"
            "2) 墙面是否有剥落、开裂等老化痕迹；"
            "3) 用色是否为赭石、石绿、朱砂等矿物色系；"
            "4) 时代元素是否合规。"
        ),
    ),
    "surface_detail": StyleProfile(
        key="surface_detail",
        label="表面肌理特写",
        aliases=("表面肌理", "肌理特写", "surface detail", "macro texture"),
        # 为什么需要这个档案：
        # `bronze_texture` 的名字与内容都是青铜专属（prompt tokens 写死 malachite green 锈层、
        # family_min 要求 green>=0.18）。但它被「细节特写 / 病害特写 / 材质特写」这三个
        # 视觉角度当作通用档案复用 —— 于是黄金面具的微距图里被塞进「颗粒状石绿锈层」，
        # 质检还要求画面 18% 是绿色，不达标就回炉把金色改成青铜色。
        # 摄影语言（掠射光、高细节密度）与材质无关，所以这里只保留摄影语言。
        prompt_tokens=(
            "extreme close-up surface texture study with low-relief ornament",
            "spiral cloud-thunder pattern and beast-face motifs in shallow relief",
            "raking side light revealing every relief step and tool mark",
            "no material substitution: keep the artefact's own material exactly",
        ),
        negative_tokens=(*_QUALITY_NEGATIVE.split(", "), *_ANACHRONISM_NEGATIVE.split(", ")),
        # 色域交给材质档案决定，这里不预设（预设即等于替材质下结论）
        family_min={},
        saturation_range=(0.08, 0.40),
        specular_max=0.05,
        edge_density_range=(0.10, 0.40),
        contrast_range=(0.10, 0.45),
        rubric=(
            "1) 纹饰是否为可辨识的浅浮雕而非平面印花；"
            "2) 表面质感是否与所声称的材质一致（不得把金/玉/象牙画成青铜锈）；"
            "3) 是否存在镜面反光（应为哑光）；"
            "4) 器物轮廓是否保持。"
        ),
    ),
    "bronze_texture": StyleProfile(
        key="bronze_texture",
        label="青铜纹饰",
        aliases=("青铜纹饰", "青铜质感", "bronze texture"),
        # 这个档案是**青铜专属**的，只在用户明确要求「青铜纹饰」风格时使用；
        # 通用微距角度请用 `surface_detail`。
        prompt_tokens=(
            "cast bronze surface texture with low-relief ornament",
            "spiral cloud-thunder pattern and beast-face motifs in shallow relief",
            "dense granular patina with malachite green and azurite blue",
            "raking side light revealing every relief step",
        ),
        negative_tokens=(*_QUALITY_NEGATIVE.split(", "), *_ANACHRONISM_NEGATIVE.split(", ")),
        family_min={"green": 0.18, "blue": 0.05, "brown": 0.06},
        saturation_range=(0.10, 0.40),
        specular_max=0.04,
        edge_density_range=(0.10, 0.40),
        contrast_range=(0.10, 0.45),
        rubric=(
            "1) 纹饰是否为可辨识的浅浮雕而非平面印花；"
            "2) 锈层是否呈颗粒状且以石绿、石青为主；"
            "3) 是否存在镜面反光（应为哑光）；"
            "4) 器物轮廓是否保持。"
        ),
    ),
    "gold_relief": StyleProfile(
        key="gold_relief",
        label="黄金雕刻",
        aliases=("黄金雕刻", "金箔", "gold relief"),
        prompt_tokens=(
            "hammered gold foil relief, repoussé technique",
            "warm muted gold with soft dents and creases from folding",
            "double-line intaglio engraving, fine flowing lines",
            "diffuse light, no chrome-like specular flare",
        ),
        negative_tokens=(
            *_QUALITY_NEGATIVE.split(", "),
            *_ANACHRONISM_NEGATIVE.split(", "),
            "chrome, mirror finish, polished brass, jewellery advertisement look",
        ),
        family_min={"gold": 0.20, "brown": 0.05},
        saturation_range=(0.14, 0.44),
        specular_max=0.10,
        edge_density_range=(0.05, 0.32),
        contrast_range=(0.08, 0.42),
        rubric=(
            "1) 金色是否为柔和的哑光暖金，而非镜面镀铬感；"
            "2) 是否有捶揲留下的起伏与褶皱；"
            "3) 纹饰是否为细密阴刻线条；"
            "4) 器物形制是否保持。"
        ),
    ),
    "jade_texture": StyleProfile(
        key="jade_texture",
        label="玉石质感",
        aliases=("玉石质感", "玉质", "jade texture"),
        prompt_tokens=(
            "translucent tremolite jade surface",
            "soft subsurface scattering, waxy lustre",
            "natural veining and mineral inclusions",
            "even soft light, gentle rim glow",
        ),
        negative_tokens=(*_QUALITY_NEGATIVE.split(", "), *_ANACHRONISM_NEGATIVE.split(", ")),
        family_min={"green": 0.14, "neutral": 0.18},
        saturation_range=(0.04, 0.26),
        specular_max=0.07,
        edge_density_range=(0.04, 0.26),
        contrast_range=(0.06, 0.34),
        rubric=(
            "1) 是否为半透温润的玉质感而非塑料或玻璃；"
            "2) 是否有自然的纹理与杂质；"
            "3) 高光是否柔和；"
            "4) 器物轮廓与纹饰是否保持。"
        ),
    ),
}

DEFAULT_PROFILE = StyleProfile(
    key="default",
    label="通用复原",
    aliases=(),
    prompt_tokens=(
        "photorealistic artifact rendering",
        "matte oxidized bronze with fine granular patina",
        "restrained natural color, soft directional light",
    ),
    negative_tokens=(*_QUALITY_NEGATIVE.split(", "), *_ANACHRONISM_NEGATIVE.split(", ")),
    family_min={"green": 0.08, "neutral": 0.12},
    saturation_range=(0.05, 0.34),
    specular_max=0.06,
    edge_density_range=(0.04, 0.30),
    contrast_range=(0.06, 0.42),
    rubric=(
        "1) 器表是否哑光并有锈层颗粒感；"
        "2) 色彩是否落在青铜合理色域；"
        "3) 是否存在时代错配元素；"
        "4) 形制是否忠实。"
    ),
)


def resolve_profile(*candidates: str | None) -> StyleProfile:
    """把自然语言风格名解析成档案。

    匹配优先级（结果只取决于文本本身，与 PROFILES 的声明顺序无关）：
      1. 与某个别名完全相等
      2. 文本中包含某个别名 —— 取「命中的最长别名」所属档案
      3. 退一步，允许别名包含文本片段（如「赛博」→「赛博朋克」），同样取最长别名

    为什么第 2 步必须按长度比较，而不是「谁先声明谁赢」：
    `photo_real` 声明在 `archival` 之前，且带一个极泛的别名「照片」。
    原先的双向子串匹配会让「照片」先命中 photo_real，
    于是「把青铜面具改成考古档案照片的风格」被判成 photo_real ——
    更精确的「考古档案照片」虽然就在文本里，却永远轮不到。
    这会让解析结果取决于字典顺序，属于「看起来能跑、实际不可预测」的隐患。
    """
    normalized = [str(item).strip() for item in candidates if item and str(item).strip()]

    for needle in normalized:
        lowered = needle.lower()
        for profile in PROFILES.values():
            if any(lowered == alias.lower() for alias in profile.aliases):
                return profile

    for direction in ("alias_in_text", "text_in_alias"):
        for needle in normalized:
            lowered = needle.lower()
            best: tuple[int, StyleProfile] | None = None
            for profile in PROFILES.values():
                for alias in profile.aliases:
                    matched = alias.lower()
                    hit = matched in lowered if direction == "alias_in_text" else lowered in matched
                    if hit and (best is None or len(matched) > best[0]):
                        best = (len(matched), profile)
            if best is not None:
                return best[1]

    return DEFAULT_PROFILE


def all_profiles() -> list[dict]:
    return [
        {
            "key": profile.key,
            "label": profile.label,
            "aliases": list(profile.aliases),
            "creative": profile.creative,
            "saturation_range": list(profile.saturation_range),
            "specular_max": profile.specular_max,
            "family_min": profile.family_min,
            "rubric": profile.rubric,
        }
        for profile in [*PROFILES.values(), DEFAULT_PROFILE]
    ]
