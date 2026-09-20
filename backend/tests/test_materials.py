"""材质维度测试。

围绕一次真实事故建立防线：
黄金面具的提示词里同时出现 `hammered gold foil` 与 `matte oxidized bronze with
dense granular patina` —— 自相矛盾；而且提示词开头是

    Key artefact: the gold mask of Sanxingdui, hammered gold foil, w, a, r, m, , m, u, t, e, d, ...

字符串被逐字符拆开。两个问题叠加的结果就是「生成的图片跟三星堆无关」。

本文件锁住三件事：
  1. 材质字段的类型安全（漏逗号必须导入即报错）
  2. 提示词里材质断言的唯一性（不得出现与文物材质矛盾的描述）
  3. 质检色域要求随材质变化（黄金不该被要求出现锈绿）
"""

from __future__ import annotations

import pytest

from app.agents import lexicon
from app.conversation.agent import _cues_from_evidence, build_rule_proposals, rule_intent
from app.conversation.strategies import (
    ARTIFACT_STRATEGIES,
    PERSON_STRATEGIES,
    SCENE_STRATEGIES,
    STYLE_STRATEGIES,
    build_prompt,
)

# 策略集按**主体**（人／器物／场面）分组，不再按 kind。
# 这里给每组配一个该组会真实遇到的 kind，用来构造 build_prompt 的入参。
STRATEGY_GROUPS: tuple[tuple[str, tuple], ...] = (
    ("figure", PERSON_STRATEGIES),
    ("scene", SCENE_STRATEGIES),
    ("artifact", ARTIFACT_STRATEGIES),
    ("style", STYLE_STRATEGIES),
)
from app.quality import objective
from app.quality.style_profiles import PROFILES, all_profiles, resolve_profile


class TestMaterialTypeSafety:
    def test_missing_trailing_comma_is_rejected_at_import(self):
        """`("a, b")` 与 `("a", "b")` 的区别，必须由类型校验兜住。

        `from __future__ import annotations` 让类型注解在运行时完全失效，
        所以漏写结尾逗号不会被任何东西发现，一路静默走到出图。
        """
        with pytest.raises(TypeError, match="必须是元组"):
            lexicon.RelicSpec(
                key="bad",
                label="坏样本",
                en="x",
                form_notes="x",
                scale_hint="x",
                extra_tokens="warm muted gold, not chrome-like gold",  # type: ignore[arg-type]
            )

    def test_aliases_must_also_be_a_tuple(self):
        with pytest.raises(TypeError, match="aliases"):
            lexicon.RelicSpec(
                key="bad",
                label="坏样本",
                en="x",
                form_notes="x",
                scale_hint="x",
                aliases="黄金面具、金面罩",  # type: ignore[arg-type]
            )

    def test_unknown_material_is_rejected(self):
        with pytest.raises(ValueError, match="MATERIALS"):
            lexicon.RelicSpec(
                key="bad",
                label="坏样本",
                en="x",
                form_notes="x",
                scale_hint="x",
                material="unobtainium",
            )

    def test_every_relic_declares_a_known_material(self):
        for relic in lexicon.RELICS:
            assert relic.material in lexicon.MATERIALS, relic.key
            assert relic.material_spec.surface_truth.strip()

    def test_no_relic_prompt_block_is_character_split(self):
        """逐字符拆分的直接特征：一堆单字母 token。"""
        for relic in lexicon.RELICS:
            block = relic.prompt_block()
            for part in block.split(","):
                token = part.strip()
                if len(token) == 1 and token.isalpha():
                    pytest.fail(f"{relic.key} 的 prompt_block 出现单字母 token：{block[:160]}")


class TestMaterialInPrompt:
    @pytest.mark.parametrize(
        ("message", "relic_key", "material_key"),
        [
            ("黄金面具在三星堆祭祀坑出土现场", "gold_mask", "gold_foil"),
            ("金杖特写，考古档案照片", "gold_scepter", "gold_foil"),
            ("玉璋的纹饰细节", "jade_zhang", "jade"),
            ("青铜大立人正面", "standing_figure", "oxidized_bronze"),
            ("象牙在祭祀坑中堆叠", "ivory", "ivory"),
        ],
    )
    def test_surface_truth_matches_the_material(self, message, relic_key, material_key):
        intent = rule_intent(message)
        relic = lexicon.resolve_relic(intent.get("subject"), message)
        assert relic is not None and relic.key == relic_key
        assert relic.material == material_key

        proposals = build_rule_proposals(intent, [])
        assert proposals
        expected = lexicon.MATERIALS[material_key].surface_truth.split(",")[0].lower()
        needle = f"surface truth: {expected}"
        for proposal in proposals:
            assert needle in proposal["prompt"].lower(), proposal["id"]

    @pytest.mark.parametrize("relic_key", ["gold_mask", "gold_scepter", "jade_zhang", "ivory"])
    def test_non_bronze_relics_never_claim_bronze_patina(self, relic_key):
        """非青铜文物不得出现氧化青铜的材质断言 —— 这正是那次事故的核心矛盾。"""
        relic = lexicon.RELIC_BY_KEY[relic_key]
        banned = (
            "matte oxidized bronze",
            "oxidized bronze surface",
            "granular patina",
            "malachite green",
        )
        for kind, group in STRATEGY_GROUPS:
            for strategy in group:
                profile = PROFILES.get(strategy.profile, PROFILES["photo_real"])
                prompt, _ = build_prompt(
                    kind=kind,
                    intent={"subject": relic.label, "identity": "大祭司", "scene": "博物馆展厅"},
                    strategy=strategy,
                    profile=profile,
                )
                lowered = prompt.lower()
                for term in banned:
                    assert term not in lowered, (
                        f"{relic_key} + {kind}:{strategy.key} 出现青铜断言 {term!r}"
                    )

    def test_material_negative_terms_reach_the_negative_prompt(self):
        """金器的负向词要明确排除「青铜锈层」与「镜面金」，否则回炉没有方向。"""
        intent = rule_intent("黄金面具在祭祀坑出土现场")
        proposals = build_rule_proposals(intent, [])
        assert proposals
        for proposal in proposals:
            negative = proposal["negative_prompt"].lower()
            assert "green corrosion crust" in negative, proposal["id"]
            assert "oxidised bronze body" in negative, proposal["id"]

    def test_non_visual_tags_do_not_leak_into_prompt(self):
        """「质检红线」曾因为含「质」字被当成视觉线索写进提示词。"""
        evidence = [
            {
                "doc_id": "d1",
                "title": "测试史料",
                "tags": ["金箔", "质检红线", "哑光质感", "学术争议", "评估维度", "青铜锈绿"],
            }
        ]
        intent = rule_intent("黄金面具在祭祀坑出土现场")
        proposals = build_rule_proposals(intent, evidence, cues=_cues_from_evidence(evidence))
        assert proposals
        for proposal in proposals:
            prompt = proposal["prompt"]
            for banned in ("质检红线", "学术争议", "评估维度"):
                assert banned not in prompt, proposal["id"]
            # 真正的视觉线索必须保留，别把孩子和洗澡水一起倒掉
            assert "金箔" in prompt and "哑光质感" in prompt, proposal["id"]


class TestMaterialAwareQualityGate:
    def test_material_overrides_style_color_families(self):
        """风格管环境色，材质管材质色；冲突时材质赢。

        回归背景：所有非创作型风格都要求 green>=0.10，
        黄金面具因此被判不合格，回炉时把「补足青铜锈绿」注入提示词 ——
        等于用质检把黄金主动改成青铜。
        """
        gold = lexicon.MATERIALS["gold_foil"]
        for key, profile in PROFILES.items():
            merged = objective.effective_family_min(profile, gold)
            assert "green" not in merged, f"{key} 仍要求黄金出现锈绿"
            assert merged.get("gold", 0) >= 0.12, key

    def test_material_minimums_are_enforced_not_just_permitted(self):
        jade = lexicon.MATERIALS["jade"]
        merged = objective.effective_family_min(PROFILES["surface_detail"], jade)
        assert merged.get("green", 0) >= 0.15

    def test_style_only_minimums_survive(self):
        """材质只否决与物理事实矛盾的色族，不能顺手把风格的环境期望也删掉。"""
        gold = lexicon.MATERIALS["gold_foil"]
        merged = objective.effective_family_min(PROFILES["museum_doc"], gold)
        assert merged.get("neutral", 0) >= 0.20

    def test_no_material_falls_back_to_profile(self):
        merged = objective.effective_family_min(PROFILES["photo_real"], None)
        assert merged == PROFILES["photo_real"].family_min

    def test_bronze_still_demands_patina(self):
        """青铜不能被误伤：它本来就应该出现锈绿。"""
        bronze = lexicon.MATERIALS["oxidized_bronze"]
        merged = objective.effective_family_min(PROFILES["photo_real"], bronze)
        assert merged.get("green", 0) >= 0.10


class TestSurfaceDetailProfile:
    def test_surface_detail_profile_is_material_neutral(self):
        """微距角度必须用材质中立的档案。

        `bronze_texture` 的名字与内容都是青铜专属（写死 malachite green、green>=0.18），
        却被「细节特写 / 病害特写 / 材质特写」三个角度复用。
        """
        profile = PROFILES["surface_detail"]
        joined = " ".join(profile.prompt_tokens).lower()
        for term in ("bronze", "patina", "malachite", "azurite"):
            assert term not in joined, term
        assert profile.family_min == {}, "色域必须交给材质决定"

    def test_bronze_texture_remains_available_as_an_explicit_style(self):
        """用户明确要「青铜纹饰」时，它仍然是一个可选项。"""
        assert resolve_profile("青铜纹饰").key == "bronze_texture"
        assert "bronze" in " ".join(PROFILES["bronze_texture"].prompt_tokens).lower()

    def test_macro_angles_use_the_neutral_profile(self):
        # 微距方案一律用中性档案：bronze_texture 是青铜专属，
        # 用在金/玉/象牙上会把「石绿锈层」写进提示词。
        micro_keys = {"ritual_detail", "damage_detail", "transfer_material"}
        for kind, group in STRATEGY_GROUPS:
            for strategy in group:
                if strategy.key in micro_keys:
                    assert strategy.profile == "surface_detail", f"{kind}:{strategy.key}"

    def test_surface_detail_is_listed_for_the_frontend(self):
        keys = {item["key"] for item in all_profiles()}
        assert "surface_detail" in keys


class TestEnvironmentIsSceneOwned:
    """环境（背景/光源氛围/地面）只能来自场景，策略与风格不得写死环境。

    事故：「黄金面具在三星堆祭祀坑出土现场，考古档案照片风格」生成的图
    没有「考古现场感」—— 面具像 CG 模型贴在土上。根因：策略的 Composition/Lighting
    与风格的 prompt_tokens 各自把「中性灰底 / no shadow / 展厅」写死，与真实场景
    （坑内现场）直接冲突。修复是结构性地把环境断言从策略/风格里剥离，只由 SCENES 提供。
    这样任意场景组合都不会再打架，也不需要为某个场景特判。
    """

    def _prompt(self, scene: str, profile: str = "archival") -> str:
        strategy = ARTIFACT_STRATEGIES[0]  # 现状记录，默认角度
        prompt, _ = build_prompt(
            kind="figure",
            intent={"subject": "黄金面具", "scene": scene},
            strategy=strategy,
            profile=PROFILES[profile],
        )
        return prompt

    def test_insitu_scene_gets_pit_environment_from_scene_only(self):
        """出土现场的环境来自场景词典，策略/风格里没有任何棚拍底色。"""
        prompt = self._prompt("三星堆祭祀坑")
        lowered = prompt.lower()
        # 坑内现场由 SCENES 提供
        assert "excavated sacrificial pit" in lowered
        # 策略/风格不得再写死这些环境假设
        for banned in (
            "neutral grey background",
            "neutral gray or black background",
            "specimen documentation",
            "no shadow",
            "dark low-illuminance hall",
            "glass case",
        ):
            assert banned not in lowered, banned

    def test_studio_scene_gets_backdrop_from_scene_not_strategy(self):
        """黑色背景展陈的「黑底」由场景提供，且全程不出现灰底。"""
        prompt = self._prompt("黑色背景展陈")
        lowered = prompt.lower()
        assert "matte black studio backdrop" in lowered
        assert "neutral grey background" not in lowered

    def test_no_strategy_or_style_leaks_studio_background(self):
        """每个器物策略 × 每个档案风格组合，都不应出现棚拍底色假设。"""
        for strategy in ARTIFACT_STRATEGIES:
            for profile in (PROFILES["archival"], PROFILES["museum_doc"]):
                prompt, _ = build_prompt(
                    kind="figure",
                    intent={"subject": "青铜面具", "scene": "三星堆祭祀坑"},
                    strategy=strategy,
                    profile=profile,
                )
                lowered = prompt.lower()
                for banned in (
                    "neutral grey background",
                    "neutral gray or black background",
                    "specimen documentation",
                ):
                    assert banned not in lowered, f"{strategy.key}/{profile.key}: {banned}"

    def test_spurious_method_block_is_gated_for_display_tasks(self):
        """展示请求的基线不许带修复手法。

        「出土现场展示」若被注入 method，出图提示词会多出
        Restoration approach 块，把展示图往修复图带（实测发生过）。
        规则层对无修复信号的请求不该给出 kind=artifact 或 method；
        LLM 幻觉出的 method 由 resolve_intent 的同名门控拦截。
        """
        resolved = rule_intent("黄金面具在三星堆祭祀坑出土现场，考古档案照片风格")
        assert resolved.get("kind") != "artifact"
        assert not resolved.get("method")
