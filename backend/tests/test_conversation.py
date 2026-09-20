"""对话式交互层测试：意图解析、方案生成、会话存储、SSE 协议、prompt_override 贯通。

这些用例全部在「无 API Key」下运行，因此覆盖的是**规则路径** ——
而规则路径正是最容易退化、也最容易被忽略的那条（有 Key 时它不会被走到）。
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.conversation.agent import build_rule_proposals, missing_slots, rule_intent
from app.conversation.smalltalk import classify_smalltalk
from app.conversation.store import (
    MAX_SESSIONS,
    MAX_TURNS_PER_SESSION,
    SESSION_TTL_SECONDS,
    ChatSession,
    ChatTurn,
)
from app.conversation.strategies import strategies_for, to_image_spec
from app.main import app
from app.storage.sessions import MemorySessionBackend, SessionRepository


# ════════════════════════════════════════════════════════════════════════════
#  意图解析
# ════════════════════════════════════════════════════════════════════════════
class TestRuleIntent:
    @pytest.mark.parametrize(
        ("text", "expected_kind"),
        [
            ("我想做个大祭司在青铜神树祭坛前的场景，博物馆纪实摄影风格", "scene"),
            ("还原一个古蜀武士的人物形象，全身像", "figure"),
            ("帮我修复这件破损青铜面具，用最小干预修复", "artifact"),
            ("把这张图转成赛博古蜀风格，强度 80%", "style"),
            ("残缺玉璋的病害情况，我要做诊断", "artifact"),
            ("青铜纵目面具在展厅里的照片", "scene"),
        ],
    )
    def test_kind_classification(self, text: str, expected_kind: str):
        assert rule_intent(text)["kind"] == expected_kind

    def test_scene_name_does_not_pollute_relic(self):
        """回归测试：'青铜神树祭坛' 里的 '青铜神树' 曾被误判成核心文物。"""
        intent = rule_intent("我想做一个大祭司在青铜神树祭坛前的场景")
        assert intent["scene"] == "青铜神树祭坛"
        # 场景名被摘除后，文物应由「场景 → 默认文物」映射得出，而不是从场景名里断章取义
        assert intent["subject"] == "青铜神树"

    def test_explicit_relic_wins_over_scene_default(self):
        intent = rule_intent("金杖在博物馆展厅里的特写")
        assert intent["subject"] == "金杖"
        assert intent["scene"] == "博物馆展厅"

    def test_identity_and_style_extraction(self):
        intent = rule_intent("古蜀王者站在三星堆祭祀坑前，电影级写实风格")
        assert intent["identity"] == "古蜀王者"
        assert intent["scene"] == "三星堆祭祀坑"
        assert intent["style"] == "电影级写实"

    def test_always_returns_complete_structure(self):
        """意图结构必须永远完整 —— 下游（方案生成、复原请求）不允许出现空字段。"""
        for text in ("随便来一张", "", "???", "修复"):
            intent = rule_intent(text)
            assert intent["kind"] in {"scene", "figure", "artifact", "style"}
            assert intent["subject"]
            assert intent["identity"]
            assert intent["scene"]
            assert intent["style"]

    def test_strength_parsing(self):
        assert rule_intent("风格强度 85%")["strength"] == 85
        assert rule_intent("强度 200%")["strength"] == 100
        assert rule_intent("强度 5%")["strength"] == 10

    def test_multi_turn_inherits_previous_intent(self):
        first = rule_intent("做一个大祭司在青铜神树祭坛前的场景")
        second = rule_intent("再暗一点", first)
        assert second["subject"] == first["subject"]
        assert second["scene"] == first["scene"]
        assert second["kind"] == "scene"

    def test_missing_slots(self):
        assert "修复方式" in missing_slots(rule_intent("修复这件破损青铜面具"))
        # 已完成解析的意图不应该再追问
        complete = rule_intent("修复这件破损青铜面具，用最小干预修复")
        assert missing_slots(complete) == []

    def test_defaults_are_assumptions_not_user_facts(self):
        """缺省补的槽位不进来源账本 —— 下游才分得清「用户说的」与「我们猜的」。

        这正是「祭司被塞进器物特写」「博物馆被塞进祭祀坑」的共同根因：
        默认值一旦冒充用户事实，任何忘记查标志位的下游都会中招。
        """
        intent = rule_intent("随便来一张")
        # 下游仍然拿到可用的值（产品行为不变）
        assert intent["identity"] and intent["scene"] and intent["style"]
        # 但它们不是用户说的
        for slot in ("identity", "scene", "style", "subject"):
            assert slot not in intent["stated"], f"{slot} 是缺省补的，不该记账"

    def test_stated_records_what_the_user_actually_said(self):
        intent = rule_intent("大祭司在祭祀台前主持祭祀")
        assert "identity" in intent["stated"]
        assert "scene" in intent["stated"]

    def test_scene_derived_relic_is_grounded_only_from_a_stated_scene(self):
        """由用户给的场景推出的文物算用户事实；从缺省场景推的不算。"""
        grounded = rule_intent("我想做一个大祭司在青铜神树祭坛前的场景")
        assert grounded["subject"] == "青铜神树"
        assert "subject" in grounded["stated"]

        assumed = rule_intent("随便来一张")
        assert "subject" not in assumed["stated"]

    def test_missing_slots_asks_about_unstated_scene(self):
        """场景没提就该追问 —— 判据必须是来源账本。

        历史 bug：`missing_slots` 用 `not intent.get("scene")` 判断，
        而 scene 被缺省补满、恒为真，导致 scene/figure 的追问是死代码。
        """
        intent = rule_intent("还原一个古蜀武士的人物形象")
        assert "场景地点" in missing_slots(intent)


class TestAIDecidesInsteadOfOfferingMenus:
    """产品取向：AI 自己拍板视觉角度，只在连画什么都无从谈起时才反问。

    与「表单式界面」相反：不要求用户先知道所有选项，也不每次摆四个方案让他挑。
    """

    def test_pick_strategy_reads_intent_signals(self):
        from app.conversation.strategies import pick_strategy

        repair = rule_intent("用最小干预修复这件破损青铜面具")
        assert pick_strategy(repair["kind"], repair).key == "minimal_intervention"

        top = rule_intent("来一张祭祀场面的俯瞰布局")
        assert pick_strategy(top["kind"], top).key == "overhead"

        # 展陈角度要求主体被判为器物。规则层对「面具在展厅里」只会判成 scene，
        # 器物主体这一步由 LLM 裁决 —— 这里直接模拟那个裁决结果。
        exhibit = rule_intent("青铜纵目面具在博物馆展厅里")
        exhibit["subject_role"] = "artefact"
        assert pick_strategy(exhibit["kind"], exhibit).key == "exhibition"

    def test_pick_strategy_has_a_safe_default(self):
        from app.conversation.strategies import pick_strategy

        # 没有任何信号时也要给出一个可用的角度，而不是抛错或返回 None
        assert pick_strategy("scene", {"brief": ""}).key == "panorama"

    def test_named_artefact_is_not_written_as_a_ceremony(self):
        """点名器物、又没有场面诉求时，主体是器物而不是「一场祭祀」。

        这是「面具在展厅里」那次事故的规则层版本。单方案决策下判错主体的代价
        从「挑错一个」变成「没有别的可选」，所以规则层必须能独立判准。
        """
        from app.conversation.strategies import resolve_role

        artefact_intent = rule_intent("青铜纵目面具在博物馆展厅里")
        assert resolve_role(artefact_intent["kind"], artefact_intent) == "artefact"

        # 明确要「场面」时不能被反噬
        scene_intent = rule_intent("青铜神树祭坛的场景")
        assert resolve_role(scene_intent["kind"], scene_intent) == "scene"

    def test_clarifying_question_only_when_there_is_no_anchor(self):
        from app.conversation.agent import clarifying_question

        # 没有器物 / 场景 / 人物 → 值得问
        assert clarifying_question(rule_intent("随便来一张"))
        # 有任何锚点 → AI 自己定，不问。
        # 注意场景要写成**能命中词典 key** 的说法（key 是「三星堆祭祀坑」）；
        # 用户说「祭祀坑出土现场」这种别名由 LLM 层映射，规则层不保证命中。
        assert clarifying_question(rule_intent("青铜纵目面具")) == ""
        assert clarifying_question(rule_intent("三星堆祭祀坑")) == ""
        assert clarifying_question(rule_intent("大祭司")) == ""


# ════════════════════════════════════════════════════════════════════════════
#  方案生成
# ════════════════════════════════════════════════════════════════════════════
class TestProposalGeneration:
    def test_every_kind_yields_multiple_distinct_proposals(self):
        for text in (
            "做一个大祭司在青铜神树祭坛前的场景",
            "还原一个古蜀大祭司的人物形象",
            "修复这件破损青铜面具",
            "把这张图转成赛博古蜀风格",
        ):
            intent = rule_intent(text)
            proposals = build_rule_proposals(intent, [])

            assert len(proposals) >= 3, f"{intent['kind']} 至少应给出 3 个方案"
            prompts = {item["prompt"] for item in proposals}
            assert len(prompts) == len(proposals), "各方案的提示词必须彼此不同，否则选择毫无意义"

            titles = [item["title"] for item in proposals]
            assert len(set(titles)) == len(titles)

    def test_proposals_differ_in_visual_strategy_not_just_wording(self):
        """四个方案必须真的不一样：风格档案、画幅、采样参数至少各有一处不同。"""
        proposals = build_rule_proposals(rule_intent("大祭司在神树祭坛前的场景"), [])
        profiles = {item["style_profile"] for item in proposals}
        sizes = {(item["params"]["width"], item["params"]["height"]) for item in proposals}

        assert len(profiles) >= 2, "不能四个方案都用同一个风格档案"
        assert len(sizes) >= 2, "不能四个方案都是同一个画幅"

    def test_proposals_carry_rationale_and_risk(self):
        proposals = build_rule_proposals(rule_intent("修复这件破损青铜面具"), [])
        for item in proposals:
            assert len(item["rationale"]) >= 10, "每个方案都要说明为什么推荐它"
            assert len(item["risk"]) >= 10, "每个方案都要如实给出已知风险"
            assert item["angle"]
            assert item["tags"]

    def test_prompts_contain_locked_form_notes(self):
        """形制要点必须落到提示词里，否则通用模型会把纵目面具画成普通人脸。"""
        intent = rule_intent("青铜纵目面具在展厅里的照片")
        proposals = build_rule_proposals(intent, [])
        for item in proposals:
            assert "protruding" in item["prompt"].lower() or "zongmu" in item["prompt"].lower()

    def test_negative_prompt_blocks_anachronism(self):
        proposals = build_rule_proposals(rule_intent("大祭司站在祭祀坑前"), [])
        for item in proposals:
            negative = item["negative_prompt"].lower()
            assert "iron tools" in negative
            assert "porcelain" in negative

    def test_evidence_is_attached(self):
        evidence = [
            {"doc_id": "sxd-tree-01", "title": "一号青铜神树的形制", "text": "…", "tags": ["九枝"]},
        ]
        proposals = build_rule_proposals(
            rule_intent("青铜神树"), evidence, cues=["哑光", "颗粒感"]
        )
        assert all("sxd-tree-01" in item["evidence_ids"] for item in proposals)
        assert all("哑光" in item["prompt"] for item in proposals)

    def test_refined_patch_can_override_rationale(self):
        # 补丁按 strategy.key 索引，所以这里必须用一个**真实存在**的 key。
        # 「大祭司」现在裁决为 person（人为主体），走的是人物策略集，
        # 原先的 "documentary" 已不在其中 —— 这是有意的行为变更，不是回归。
        intent = rule_intent("大祭司")
        assert intent["subject_role"] == "person"
        key = build_rule_proposals(intent, [])[0]["strategy"]
        patch = {
            key: {
                "title": "铁证纪实",
                "rationale": "这是模型润色过的说明文字。",
                "extra_directives": ["golden hour haze"],
            }
        }
        proposals = build_rule_proposals(intent, [], refined=patch)
        target = next(item for item in proposals if item["strategy"] == key)
        assert target["title"] == "铁证纪实"
        assert target["rationale"] == "这是模型润色过的说明文字。"
        assert "golden hour haze" in target["prompt"]
        assert target["generated_by"] == "rule+llm"

    def test_strategy_sets_cover_all_kinds(self):
        for kind in ("scene", "figure", "artifact", "style"):
            assert len(strategies_for(kind)) >= 3
        # 未知 kind 回落到场景策略，而不是抛异常
        assert strategies_for("unknown") == strategies_for("scene")

    def test_to_image_spec_shape(self):
        proposal = build_rule_proposals(rule_intent("大祭司"), [])[0]
        spec = to_image_spec(proposal)
        assert spec["prompt"] == proposal["prompt"]
        assert spec["source"] == "user_selected_proposal"
        assert spec["proposal_id"] == proposal["id"]
        assert spec["width"] > 0 and spec["height"] > 0


class TestSubjectRole:
    """主体裁决（person / artefact / scene）—— 回归一次真实失败。

    用户给的诉求是「大祭司戴着青铜纵目面具在祭祀台前主持祭祀」，
    交付的却是一张**只有面具**的考古纪实照：没有祭司、没有祭台、没有仪式，
    还多了卷尺、比例尺和红底金字横幅。

    两个叠加的根因，下面每条测试钉住其中一个：
    1. kind 落在 scene 且解析到文物时，人物描述被整段跳过 ——
       提示词里没有一个字说祭司长什么样，模型只能退回「现代人／动漫女性」的先验；
    2. 场景/人物复用的是文物视角的构图（`scale reference`、`reportage`、
       `documentation markers`），卷尺与横幅是提示词自己请来的。
    """

    FAILING_INPUT = (
        "大祭司是三星堆文物的大祭司在三星堆文化的祭祀台面前"
        "大祭司带着青铜纵目面具主持祭祀的场景"
    )

    def test_person_becomes_the_subject_when_the_text_names_one(self):
        intent = rule_intent(self.FAILING_INPUT)
        assert intent["kind"] == "scene"            # 分类仍是场景
        assert intent["subject_role"] == "person"   # 但主体是人

    def test_person_block_comes_first_and_describes_the_figure(self):
        """块序就是权重。人物排在最后一句等于没写 —— 这正是那次失败。"""
        prompt = build_rule_proposals(rule_intent(self.FAILING_INPUT), [])[0]["prompt"]
        assert "ancient Shu high priest" in prompt
        assert "The figure is the visual anchor" in prompt
        assert prompt.index("ancient Shu high priest") < prompt.index("Bronze Zongmu")

    def test_mask_is_written_as_worn_not_displayed(self):
        """面具被写成「展柜标本」正是那次失败：画面里只剩一件面具。"""
        prompt = build_rule_proposals(rule_intent(self.FAILING_INPUT), [])[0]["prompt"]
        assert "worn on the figure's face" in prompt

    def test_ritual_prompts_do_not_invite_documentation_props(self):
        """`scale reference` / `reportage` 就是卷尺与比例尺的来源。"""
        for proposal in build_rule_proposals(rule_intent(self.FAILING_INPUT), []):
            lowered = proposal["prompt"].lower()
            for term in ("scale reference", "reportage", "documentation markers"):
                assert term not in lowered, f"{proposal['id']} 含 {term!r}"

    def test_ritual_negative_bans_modern_props(self):
        """只禁「现代服装」不够 —— 卷尺、比例尺、展签也要显式排除。"""
        negative = build_rule_proposals(rule_intent(self.FAILING_INPUT), [])[0][
            "negative_prompt"
        ].lower()
        for term in ("tape measure", "ruler", "exhibit label", "modern person"):
            assert term in negative

    def test_ritual_scene_is_not_a_modern_museum(self):
        """「祭祀台」曾不在场景词典里，于是回落到「现代博物馆展厅」。

        一个现代展厅配一个三千年前的祭司，时代错配是必然的 ——
        这类错误不该靠回炉去补，要在词典层就不发生。
        """
        intent = rule_intent(self.FAILING_INPUT)
        assert intent["scene"] == "祭祀台"
        for proposal in build_rule_proposals(intent, []):
            assert "modern museum" not in proposal["prompt"].lower(), proposal["id"]

    def test_artifact_task_does_not_import_a_priest(self):
        """没人提到人时，器物任务不该被塞进一个祭司。

        `identity` 对**每个**意图都 setdefault 成「大祭司」，
        所以判据必须是「原文提没提到人」，不能是「identity 有没有值」。
        """
        intent = rule_intent("给青铜纵目面具做最小干预修复，要能看清残缺")
        assert intent["subject_role"] == "artefact"
        assert intent["identity"] == "大祭司"           # 默认值仍在
        for proposal in build_rule_proposals(intent, []):
            assert "high priest" not in proposal["prompt"], proposal["id"]

    def test_explicit_panorama_request_overrides_sticky_person(self):
        """多轮里「人在场」会粘住，用户明确要全景时得能推翻它。"""
        prior = rule_intent("大祭司在祭祀台前主持祭祀")
        assert prior["subject_role"] == "person"
        nxt = rule_intent("来一张祭祀场面的全景", prior)
        assert nxt["subject_role"] == "scene"

    def test_each_role_gets_its_own_strategy_set(self):
        """三种主体拿到的构图必须真的不同，否则「选主体」是装饰。"""
        person = {p["strategy"] for p in build_rule_proposals(rule_intent(self.FAILING_INPUT), [])}
        artefact = {
            p["strategy"]
            for p in build_rule_proposals(rule_intent("给青铜纵目面具做最小干预修复"), [])
        }
        scene = {
            p["strategy"] for p in build_rule_proposals(rule_intent("来一张祭祀场面的全景"), [])
        }
        assert person and artefact and scene
        assert not (person & artefact) and not (person & scene) and not (artefact & scene)


class TestArtefactSubjectDoesNotGrowAPriest:
    """第二次真实事故：**用户一个字没提人，图上却站着一个祭司。**

    输入是「青铜纵目面具在博物馆展厅里，电影级写实风格」。

    LLM 层判得没错 —— `subject_role=artefact`，主体确实是器物，
    方案层因此拿到器物策略集（现状存档／可逆修复／病害特写／展陈实况）。

    错的是 `build_prompt`：它用 `kind` 选分支、用 `role` 选子分支。
    这次 `kind` 是 `scene`，于是进了 `if kind in {"scene","figure"}`；
    而 `role` 既不等于 person，`else` 又默认当成「场面」——
    于是一张「拍面具」的图被写成一场祭祀，还凭空多出一个祭司：

        Central figure (middle distance): an ancient Shu high priest, ...

    同时「在展厅里」这个环境信息被整个丢掉（因为走了场面分支）。

    根因是分支的判据不统一 + `else` 隐含假设「只有两种 role」。
    """

    INPUT = "青铜纵目面具在博物馆展厅里，电影级写实风格"

    @staticmethod
    def _intent_with_llm_role() -> dict:
        intent = rule_intent(TestArtefactSubjectDoesNotGrowAPriest.INPUT)
        # 模拟 LLM 层合并的结果：主体判为器物，而 kind 仍在 scene
        intent["subject_role"] = "artefact"
        return intent

    def test_kind_stays_scene_while_the_subject_is_the_artefact(self):
        """先把这次的矛盾状态本身钉住，否则下面的断言会失去意义。"""
        intent = self._intent_with_llm_role()
        assert intent["kind"] == "scene", "这条回归的前提就是 kind 与 role 不一致"
        assert intent["subject_role"] == "artefact"

    def test_no_figure_is_invented(self):
        for proposal in build_rule_proposals(self._intent_with_llm_role(), []):
            prompt = proposal["prompt"]
            assert "Central figure" not in prompt, f"{proposal['id']} 凭空多出一个人"
            assert "high priest" not in prompt, f"{proposal['id']} 凭空多出一个人"

    def test_the_artefact_is_the_subject_and_keeps_its_setting(self):
        """主体得是器物，且「在展厅里」这个环境不能被丢掉。"""
        proposals = build_rule_proposals(self._intent_with_llm_role(), [])
        assert proposals
        for proposal in proposals:
            prompt = proposal["prompt"]
            assert "Subject artefact" in prompt, proposal["id"]
            assert "Setting (background context)" in prompt, proposal["id"]

    def test_a_display_request_is_not_turned_into_a_restoration(self):
        """「在展厅里」不是「修复它」—— 不该冒出「修复方式：最小干预」这种话。"""
        for proposal in build_rule_proposals(self._intent_with_llm_role(), []):
            assert "Restoration approach" not in proposal["prompt"], proposal["id"]

    def test_pure_scene_request_also_has_no_figure(self):
        """纯场面请求（没提人）同样不得被塞进一个祭司。

        `identity` 被 `rule_intent` 用 `setdefault` 填成「大祭司」，永远非空 ——
        拿它当「人在场」的判据，每个场面提示词都会长出一个祭司。
        """
        intent = rule_intent("来一张祭祀场面的全景")
        assert intent["subject_role"] == "scene"
        assert intent["identity"] == "大祭司", "默认值仍然在，但这不代表人在场"
        for proposal in build_rule_proposals(intent, []):
            assert "high priest" not in proposal["prompt"], proposal["id"]

    def test_artefact_prompts_ban_bystanders(self):
        """展陈画面里的现代观众不在承诺范围内，显式排除。

        实测 VLM 抓到过「背景展柜玻璃反光中可见现代游客穿牛仔裤」。
        """
        for proposal in build_rule_proposals(self._intent_with_llm_role(), []):
            negative = proposal["negative_prompt"].lower()
            assert "modern visitors" in negative, proposal["id"]

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("把青铜大立人改成博物馆纪实摄影的风格", "museum_doc"),
            ("把青铜神树迁移成赛博朋克风格", "cyberpunk"),
            ("把青铜面具改成考古档案照片的风格", "archival"),
            ("把青铜面具改成神庙壁画的风格", "temple_mural"),
        ],
    )
    def test_style_transfer_uses_the_requested_target_profile(self, message, expected):
        """风格迁移必须拿「用户指名的风格」当质检标尺。

        回归背景：这里原本四个策略全部写死 profile="default"，
        而 default 是个空档案（无数值区间、无负向词）→ prompt 里没有目标风格 token，
        质检也没有可比基准，整条风格迁移链的自检是空转的。
        """
        intent = rule_intent(message)
        assert intent["kind"] == "style"
        proposals = build_rule_proposals(intent, [])
        assert proposals, "风格迁移必须产出方案"
        for proposal in proposals:
            assert proposal["style_profile"] == expected, proposal["id"]

    def test_style_transfer_never_falls_back_to_empty_profile(self):
        """识别不出预设时回退到真实档案，而不是让质检失去标尺。"""
        intent = {"kind": "style", "subject": "青铜大立人", "style_preset": "某种不存在的风格"}
        proposals = build_rule_proposals(intent, [])
        assert proposals
        for proposal in proposals:
            assert proposal["style_profile"] != "default"
            assert proposal["style_label"]

    def test_style_transfer_prompt_carries_target_style_tokens(self):
        """目标风格的 token 必须真的落到 prompt 里，否则风格迁移只是嘴上说说。"""
        from app.quality.style_profiles import PROFILES

        intent = rule_intent("把青铜大立人改成博物馆纪实摄影的风格")
        proposals = build_rule_proposals(intent, [])
        tokens = PROFILES["museum_doc"].prompt_tokens
        assert tokens, "档案本身应带风格 token"
        for proposal in proposals:
            assert any(token.lower() in proposal["prompt"].lower() for token in tokens[:4]), (
                proposal["id"]
            )
            assert PROFILES["museum_doc"].negative_prompt()[:20] in proposal["negative_prompt"]


# ════════════════════════════════════════════════════════════════════════════
#  会话存储
# ════════════════════════════════════════════════════════════════════════════
def _repository() -> SessionRepository:
    """拿一个**确定走进程内实现**的会话仓储。

    conftest 已经把 REDIS_URL 清空，所以 connect() 必然返回 False 并保持
    进程内后端 —— 这里不 mock，而是走真实的降级分支（降级路径本身也要被测）。
    """
    repository = SessionRepository()
    assert asyncio.run(repository.connect()) is False
    assert repository.backend_name == "memory"
    return repository


class TestSessionStore:
    """会话存储的行为契约。

    跑在进程内实现上，但断言的是**两种实现都必须满足**的语义；
    Redis 通道另有真实读写往返验证（`scripts/check_storage.py`），
    因为「本地内存版对、Redis 版忘存」这类问题只有真连上去才测得出来。
    """

    def test_create_and_reuse(self):
        store = _repository()
        first = asyncio.run(store.get_or_create(None))
        second = asyncio.run(store.get_or_create(first.session_id))
        assert first is second

    def test_reset(self):
        store = _repository()
        session = asyncio.run(store.get_or_create(None))
        assert asyncio.run(store.reset(session.session_id)) is True
        assert asyncio.run(store.get(session.session_id)) is None

    def test_history_window(self):
        session = ChatSession(session_id="t")
        for index in range(12):
            session.append(
                ChatTurn(role="user" if index % 2 == 0 else "assistant", content=f"m{index}")
            )
        history = session.history(limit=4)
        assert len(history) == 4
        assert history[-1]["content"] == "m11"
        assert history[0]["role"] == "user"

    def test_turn_trimming_keeps_first_user_turn(self):
        session = ChatSession(session_id="t")
        session.append(ChatTurn(role="user", content="最初的诉求"))
        for index in range(MAX_TURNS_PER_SESSION + 20):
            session.append(ChatTurn(role="assistant", content=f"r{index}"))
        assert len(session.turns) <= MAX_TURNS_PER_SESSION
        assert session.turns[0].content == "最初的诉求", "长会话不应把最初的诉求冲掉"

    def test_max_sessions_eviction(self):
        store = _repository()
        for index in range(MAX_SESSIONS + 15):
            asyncio.run(store.get_or_create(f"chat-{index:04d}"))
        stats = asyncio.run(store.stats())
        assert stats["sessions"] <= MAX_SESSIONS
        assert stats["evicted_total"] >= 15

    def test_ttl_expiry(self):
        store = _repository()
        session = asyncio.run(store.get_or_create("chat-stale"))
        session.updated_at = time.time() - SESSION_TTL_SECONDS - 10
        assert asyncio.run(store.get("chat-stale")) is None
        assert asyncio.run(store.stats())["evicted_total"] >= 1

    def test_append_assistant_updates_last_intent(self):
        store = _repository()
        session = asyncio.run(store.get_or_create(None))
        asyncio.run(store.append_assistant(session, "回复", intent={"kind": "artifact"}))
        assert session.last_intent["kind"] == "artifact"

    def test_session_survives_dict_round_trip(self):
        """`to_dict` / `from_dict` 必须无损。

        这是 Redis 通道的地基：load() 拿到的对象完全由 from_dict 重建，
        任何字段漏掉都会表现为「会话在第二次请求后行为变了」——
        而进程内实现不会暴露这个问题（它根本没走序列化）。
        """
        session = ChatSession(session_id="s-1", last_intent={"kind": "scene"})
        session.append(ChatTurn(role="user", content="想看青铜大立人"))
        session.append(
            ChatTurn(
                role="assistant",
                content="好的",
                proposals=[{"id": "p1", "title": "展陈纪实"}],
                intent={"kind": "scene"},
                evidence_titles=["发掘简报"],
            )
        )
        session.selected_proposal = {"id": "p1"}

        restored = ChatSession.from_dict(json.loads(json.dumps(session.to_dict())))
        assert restored.to_dict() == session.to_dict()
        assert restored.turns[-1].proposals == [{"id": "p1", "title": "展陈纪实"}]
        assert restored.transcript(2).endswith("好的")

    def test_memory_backend_requires_explicit_save(self):
        """把两种实现的语义差异写死在测试里。

        进程内实现的 load() 返回的是**活对象**，改完不 save 也能读到；
        Redis 实现必须 save 才写回。这里断言「backend 层面的 save 会把内容写回」，
        这样任何一次「忘了 save」的重构都会在这里露出来。
        """
        backend = MemorySessionBackend()
        session = ChatSession(session_id="s-2")
        asyncio.run(backend.save(session))
        session.append(ChatTurn(role="user", content="hi"))
        asyncio.run(backend.save(session))
        loaded = asyncio.run(backend.load("s-2"))
        assert loaded is not None and loaded.turns[-1].content == "hi"
        assert asyncio.run(backend.delete("s-2")) is True
        assert asyncio.run(backend.load("s-2")) is None


# ════════════════════════════════════════════════════════════════════════════
#  接口
# ════════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as instance:
        yield instance


def collect_sse(client: TestClient, url: str, payload: dict) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    with client.stream(
        "POST", url, json=payload, headers={"Accept": "text/event-stream"}
    ) as response:
        assert response.status_code == 200
        event_name = "message"
        data_lines: list[str] = []
        for line in response.iter_lines():
            if line == "":
                if data_lines:
                    frames.append((event_name, json.loads("\n".join(data_lines))))
                event_name = "message"
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
    return frames


class TestChatEndpoint:
    def test_frame_protocol(self, client: TestClient):
        frames = collect_sse(
            client,
            "/api/chat/stream",
            {"message": "我想做一个大祭司在青铜神树祭坛前的场景，博物馆纪实摄影风格"},
        )
        names = [name for name, _ in frames]

        for required in ("session", "intent", "evidence", "delta", "proposals", "message", "followups", "trace"):
            assert required in names, f"缺少 {required} 帧（实际: {set(names)}）"

    def test_proposals_are_actionable(self, client: TestClient):
        """现在只给**一个**由 AI 选定、可直接出图的方案，而不是四个候选。"""
        frames = collect_sse(
            client, "/api/chat/stream", {"message": "帮我修复这件破损青铜面具"}
        )
        proposals_frame = next(payload for name, payload in frames if name == "proposals")
        items = proposals_frame["items"]

        assert len(items) == 1, "AI 应当自己拍板一个角度，而不是摆出候选清单"
        for item in items:
            assert item["prompt"] and len(item["prompt"]) > 80
            assert item["rationale"] and item["risk"]
            assert item["params"]["width"] > 0
            assert item["style_profile"]

    def test_decision_event_reports_the_chosen_angle(self, client: TestClient):
        frames = collect_sse(client, "/api/chat/stream", {"message": "金杖在博物馆展厅里"})
        decision = next(payload for name, payload in frames if name == "decision")
        assert decision["mode"] == "decide"
        assert decision["strategy"] and decision["title"]

    def test_vague_request_asks_instead_of_dumping_options(self, client: TestClient):
        """信息不足以决策时才追问 —— 而且一个方案都不给（不做菜单）。"""
        frames = collect_sse(client, "/api/chat/stream", {"message": "随便来一张"})

        decision = next(payload for name, payload in frames if name == "decision")
        assert decision["mode"] == "ask"
        assert decision["question"]

        proposals = next(payload for name, payload in frames if name == "proposals")
        assert proposals["items"] == []

        message = next(payload for name, payload in frames if name == "message")
        assert "？" in message["text"]

    def test_reply_is_not_empty_without_api_key(self, client: TestClient):
        """无 Key 时回复由模板兜底生成 —— 功能可用，而不是显示一句「请配置 Key」。"""
        frames = collect_sse(client, "/api/chat/stream", {"message": "金杖特写"})
        message = next(payload for name, payload in frames if name == "message")
        assert len(message["text"]) >= 40
        assert "史料" in message["text"] or "没有检索到" in message["text"]

    def test_session_id_is_reused_across_turns(self, client: TestClient):
        first = collect_sse(client, "/api/chat/stream", {"message": "大祭司在神树祭坛前的场景"})
        session_id = next(payload for name, payload in first if name == "session")["session_id"]

        second = collect_sse(
            client,
            "/api/chat/stream",
            {"message": "再暗一点，做成电影感", "session_id": session_id},
        )
        assert next(payload for name, payload in second if name == "session")["session_id"] == session_id

        detail = client.get(f"/api/chat/session/{session_id}").json()
        assert len(detail["turns"]) == 4  # 两轮 user + assistant

    def test_empty_message_rejected(self, client: TestClient):
        assert client.post("/api/chat/stream", json={"message": ""}).status_code == 422

    def test_session_404(self, client: TestClient):
        assert client.get("/api/chat/session/chat-nonexistent").status_code == 404

    def test_reset(self, client: TestClient):
        frames = collect_sse(client, "/api/chat/stream", {"message": "青铜神树"})
        session_id = next(payload for name, payload in frames if name == "session")["session_id"]
        assert client.post("/api/chat/reset", json={"session_id": session_id}).json()["ok"] is True
        assert client.get(f"/api/chat/session/{session_id}").status_code == 404

    def test_stats(self, client: TestClient):
        stats = client.get("/api/chat/stats").json()
        assert stats["strategy_count"]["scene"] >= 3
        assert stats["strategy_count"]["artifact"] >= 3


# ════════════════════════════════════════════════════════════════════════════
#  非创作消息闸门：闲聊不进生图管线
# ════════════════════════════════════════════════════════════════════════════
class TestSmalltalkClassification:
    """闸门规则层：整句匹配，创作线索一票否决。"""

    def test_identity(self):
        for message in ("你是谁", "你是谁呀", "你叫什么名字", "自我介绍一下", "你是ai吗",
                        "我是谁"):
            assert classify_smalltalk(message) is not None, message
        category, _ = classify_smalltalk("你是谁")
        assert category == "identity"

    def test_greeting_and_thanks(self):
        for message in ("你好", "你好呀", "在吗", "hello", "谢谢", "辛苦了", "多谢啦"):
            category, _ = classify_smalltalk(message)
            assert category in {"greeting", "thanks"}, message

    def test_chat_request(self):
        """「跟我来聊天可以吗」是闲聊请求，不能被送进问答或生图管线。"""
        for message in ("跟我来聊天可以吗", "陪我聊聊天", "你喜欢聊天吗", "讲个笑话"):
            category, _ = classify_smalltalk(message)
            assert category == "chat", message

    def test_creation_request_is_never_smalltalk(self):
        for message in (
            "你好，帮我修复一件破损青铜面具",
            "黄金面具在祭祀坑出土现场，考古档案照片风格",
            "画一张青铜神树",
        ):
            assert classify_smalltalk(message) is None, message

    def test_plain_statement_is_not_smalltalk(self):
        assert classify_smalltalk("金杖在博物馆展厅里") is None
        assert classify_smalltalk("随便来一张") is None


class TestSmalltalkGate:
    """「你是谁」必须得到一句身份回答，而不是被硬答成「你想复原……」。"""

    def test_identity_question_is_not_turned_into_generation(self, client: TestClient):
        frames = collect_sse(client, "/api/chat/stream", {"message": "你是谁"})
        names = {name for name, _ in frames}

        # 没进创作管线：没有意图帧、没有史料帧
        assert "intent" not in names
        assert "evidence" not in names

        decision = next(payload for name, payload in frames if name == "decision")
        assert decision["mode"] == "smalltalk"

        proposals = next(payload for name, payload in frames if name == "proposals")
        assert proposals["items"] == []

        message = next(payload for name, payload in frames if name == "message")
        assert "古蜀智脑" in message["text"]
        # 回复在引导用户发起创作，而不是替用户拍板一个画面
        assert "复原" not in message["text"] or "想复原" in message["text"]

    def test_greeting_gets_greeting(self, client: TestClient):
        frames = collect_sse(client, "/api/chat/stream", {"message": "你好"})
        decision = next(payload for name, payload in frames if name == "decision")
        assert decision["mode"] == "smalltalk"
        proposals = next(payload for name, payload in frames if name == "proposals")
        assert proposals["items"] == []

    def test_greeting_with_creation_request_still_creates(self, client: TestClient):
        """「你好，帮我修复……」是创作请求 —— 闲聊闸门不能误杀。"""
        frames = collect_sse(
            client, "/api/chat/stream", {"message": "你好，帮我修复一件破损青铜面具"}
        )
        decision = next(payload for name, payload in frames if name == "decision")
        assert decision["mode"] != "smalltalk"
        proposals = next(payload for name, payload in frames if name == "proposals")
        assert len(proposals["items"]) == 1


class TestQuestionGate:
    """领域提问转交 QA；已有方案后的修改指令绝不能被送去问答。"""

    def test_domain_question_routes_to_qa(self, client: TestClient, monkeypatch):
        citations = [
            {"doc_id": "d1", "title": "《古蜀·建木》", "quote": "有蜀侯蠶叢，其目縱。",
             "source_type": "ancient_text", "url": None},
        ]
        captured: dict = {}

        async def fake_qa(message: str):
            captured["message"] = message
            return "据《古蜀·建木》记载，蜀侯蚕丛纵目。", citations

        monkeypatch.setattr("app.conversation.agent._qa_reply", fake_qa)

        frames = collect_sse(
            client, "/api/chat/stream", {"message": "纵目面具的眼睛为什么外凸"}
        )
        assert captured["message"] == "纵目面具的眼睛为什么外凸"

        decision = next(payload for name, payload in frames if name == "decision")
        assert decision["mode"] == "qa"

        evidence = next(payload for name, payload in frames if name == "evidence")
        assert evidence["items"][0]["title"] == "《古蜀·建木》"

        proposals = next(payload for name, payload in frames if name == "proposals")
        assert proposals["items"] == []

    def test_revision_request_in_active_session_skips_qa(self, client: TestClient, monkeypatch):
        """会话里已有方案时，「再暗一点」是修改指令 —— 即使它带问号。"""
        called = {"qa": False}

        async def fail_qa(message: str):
            called["qa"] = True
            return "不应被调用", []

        monkeypatch.setattr("app.conversation.agent._qa_reply", fail_qa)

        first = collect_sse(client, "/api/chat/stream", {"message": "大祭司在神树祭坛前的场景"})
        session_id = next(payload for name, payload in first if name == "session")["session_id"]
        assert any(name == "proposals" for name, _ in first)

        second = collect_sse(
            client,
            "/api/chat/stream",
            {"message": "能不能再暗一点？", "session_id": session_id},
        )
        assert called["qa"] is False
        decision = next(payload for name, payload in second if name == "decision")
        assert decision["mode"] != "qa"

    def test_unanswerable_question_says_so(self, client: TestClient, monkeypatch):
        """语料无据时明说「答不了」，绝不回退成生图。"""

        async def refused_qa(message: str):
            return (
                "这个问题我在语料里没有找到可靠记载，不能编一个有出处的假答案。"
                "你可以换个说法再问，或者直接描述想复原的场景，我来出方案。",
                [],
            )

        monkeypatch.setattr("app.conversation.agent._qa_reply", refused_qa)

        frames = collect_sse(client, "/api/chat/stream", {"message": "今天成都的天气怎么样"})
        names = {name for name, _ in frames}
        assert "intent" not in names
        message = next(payload for name, payload in frames if name == "message")
        assert "没有找到可靠记载" in message["text"]


class TestMultiProposalSelection:
    """方案数量由 AI 决定：该多就多（带推荐项），该少就少。"""

    def test_ai_can_present_multiple_plans_with_one_recommended(self, client, monkeypatch):
        async def multi(message, intent, candidates, cues, tracer):
            keys = [c["strategy"] for c in candidates][:2]
            return keys, keys[0]

        monkeypatch.setattr("app.conversation.agent._select_proposals", multi)

        frames = collect_sse(client, "/api/chat/stream", {"message": "金杖在博物馆展厅里"})
        proposals = next(payload for name, payload in frames if name == "proposals")
        items = proposals["items"]
        assert len(items) == 2
        recommended = [p for p in items if p.get("recommended")]
        assert len(recommended) == 1
        # 推荐项排在第一个，作为默认选中
        assert recommended[0]["strategy"] == items[0]["strategy"]

        decision = next(payload for name, payload in frames if name == "decision")
        assert decision["count"] == 2
        assert decision["recommended_key"] == items[0]["strategy"]

    def test_single_plan_without_api_key_is_recommended(self, client):
        """无 Key 时回落单方案，但仍标记为 AI 推荐。"""
        frames = collect_sse(client, "/api/chat/stream", {"message": "帮我修复这件破损青铜面具"})
        proposals = next(payload for name, payload in frames if name == "proposals")
        items = proposals["items"]
        assert len(items) == 1
        assert items[0].get("recommended") is True


class TestPromptOverridePlumbing:
    """用户选定方案后，出图必须严格使用该提示词 —— 这是整个交互的核心承诺。"""

    CHOSEN_PROMPT = (
        "A museum documentary photograph of the Bronze Standing Figure of Sanxingdui, "
        "matte oxidized patina, focused soft spotlight, dark exhibition hall, 85mm lens."
    )

    def test_override_is_used_verbatim(self, client: TestClient):
        response = client.post(
            "/api/restore",
            json={
                "kind": "scene",
                "item": "青铜大立人",
                "identity": "大祭司",
                "scene": "博物馆展厅",
                "prompt_override": {
                    "prompt": self.CHOSEN_PROMPT,
                    "negative_prompt": "iron tools, porcelain",
                    "profile_key": "museum_doc",
                    "width": 768,
                    "height": 960,
                    "proposal_id": "scene:documentary",
                    "proposal_title": "展陈纪实",
                },
            },
            timeout=60,
        )
        assert response.status_code == 200
        body = response.json()

        spec = body["plan"]["image_spec"]
        assert spec["source"] == "user_selected_proposal"
        assert spec["proposal_title"] == "展陈纪实"
        assert spec["width"] == 768 and spec["height"] == 960
        # 选定方案必须关闭提示词增强：用户选的就是这份文字，不允许模型改写
        assert spec["prompt_extend"] is False
        # 扩散采样参数（steps/cfg/lora_strength）是 ComfyUI 链路的东西，
        # 本项目改用 API 出图后已移除。这条断言防止它们被无意中加回来 ——
        # 接口上留一个不生效的旋钮，比没有旋钮更坑调用方。
        for removed in ("steps", "cfg", "lora_strength"):
            assert removed not in spec, f"{removed} 已不属于出图参数"
        assert body["plan"]["profile_key"] == "museum_doc"

        # 首轮必须逐字下发。注意是 == 而不是 startswith：
        # 只要允许「以选定的提示词开头」，任何后缀追加都能悄悄改写用户的意图，
        # 而前端向用户承诺的却是「已锁定为你选定的提示词」。
        # 用 revision_history[0]（首轮实际下发内容）而不是 image["prompt"]，
        # 因为后者是「最后一轮」；若质检要求回炉，它理应带上修正指令。
        first_round = body["revision_history"][0]
        assert first_round["round_index"] == 0
        assert first_round["prompt"] == self.CHOSEN_PROMPT

        # 回炉时仍以选定提示词为基准，只追加不改写。
        # 注意是「包含」而不是「前缀」：回炉轮的修正指令被**前置**到了最开头
        # （见 restoration_worker._compose_prompt 里关于有效窗口的实测注释），
        # 但基准提示词本身一个字符都不会被改写。
        assert self.CHOSEN_PROMPT in body["image"]["prompt"]
        assert body["image"]["negative_prompt"].startswith("iron tools")

    def test_locked_prompt_still_accepts_revision_feedback(self):
        """锁定提示词只能锁「首轮基准」，不能锁死 Self-Correction。

        若回炉重做时也逐字下发，质检意见就永远传不到出图端，
        「不达标自动重做」会退化成「原地重画同一张图」。
        """
        from app.agents.restoration_worker import _compose_prompt
        from app.quality.style_profiles import resolve_profile

        plan = {
            "prompt_locked": True,
            "image_spec": {"prompt": "LOCKED BASE PROMPT"},
        }
        profile = resolve_profile("restoration")
        qa = {"score": 0.4, "threshold": 0.72, "feedback": ["降低饱和度", "增强铜锈质感"]}

        first = _compose_prompt(plan, {}, None, {}, 0, profile)
        assert first == "LOCKED BASE PROMPT"

        second = _compose_prompt(plan, {}, None, qa, 1, profile)
        # 修正指令必须排在最前面：它写在末尾时会被出图通道的有效窗口静默丢掉，
        # 于是两轮画面逐像素相同（实测数据见下一条测试）。
        assert second.startswith("REVISION 1")
        assert "LOCKED BASE PROMPT" in second
        assert "降低饱和度" in second and "增强铜锈质感" in second
        assert len(second) > len(first)

    def test_image_prompt_carries_no_evaluation_rubric(self):
        """中文评分细则不得进英文出图 prompt。

        回归背景：`_compose_prompt` 末尾曾追加 `Final style anchor: {label}. {rubric}`，
        而 rubric 是**中文的评价口径**（"1) 器表是否……"）。它同时踩三个坑：
        受众错位（评审口径去干扰生成决策）、与 Style 块重复、且位于结尾注定被有效窗口丢弃。
        """
        from app.agents.restoration_worker import _compose_prompt
        from app.quality.style_profiles import resolve_profile

        profile = resolve_profile("archival")
        plan = {
            "prompt_locked": True,
            "image_spec": {"prompt": "Key artefact: the gold mask of Sanxingdui."},
        }
        qa = {"score": 0.3, "threshold": 0.7, "feedback": ["lower the specular highlight"]}
        prompt = _compose_prompt(plan, {}, None, qa, 1, profile)

        assert "Final style anchor" not in prompt
        assert profile.rubric not in prompt

    def test_revision_directive_survives_the_truncation_window(self):
        """回炉指令不但要前置，还必须**在截断之后**仍留在模型视野内。

        实测（`scripts/probe_freeimage_tail.py`，2026-09）：出图通道对提示词有有效窗口 ——
        构造两个头部相同、只差结尾一句的提示词，288 字时尾部生效（81.7% 像素不同），
        436 字时尾部被静默丢弃（最大像素差 0）。

        所以有两件事会致命：
        1. 把修正指令写在末尾 —— 直接丢出窗口；
        2. 引导语写得太长，把真正要执行的修改挤到头部窗口之外 —— 模型只看到「apply:」，
           却看不到要 apply 什么。
        这条测试把「指令必须落在下发的头部窗口内」钉死，防止将来有人把顺序改回去。
        """
        from app.agents.restoration_worker import _compose_prompt
        from app.core.config import settings
        from app.imagegen.freeimage import FreeImageProvider
        from app.quality.style_profiles import resolve_profile

        profile = resolve_profile("restoration")
        # 用生产长度的基准提示词，确保一定会触发截断
        base = "Key artefact: the gold mask of Sanxingdui, hammered gold foil. " * 40
        plan = {"prompt_locked": True, "image_spec": {"prompt": base}}
        qa = {
            "score": 0.40,
            "threshold": 0.72,
            "feedback": ["降低镜面高光", "补足铜锈颗粒", "移除铁器元素"],
        }

        prompt = _compose_prompt(plan, {}, None, qa, 1, profile)
        limit = int(settings.freeimage_max_prompt_chars)
        assert len(prompt) > limit, "测试前提：提示词必须长到触发截断"

        fitted, truncated = FreeImageProvider.fit_prompt(prompt, limit)
        assert truncated is True

        head_len = max(1, int(limit * 0.6))  # 与 fit_prompt 的「保头」比例保持一致
        window = fitted[:head_len]
        assert fitted.startswith("REVISION 1"), "修正指令不在下发内容的最前面"
        assert "降低镜面高光" in window, (
            f"第一条修正指令落在头部窗口（{head_len} 字）之外，模型读不到：{window!r}"
        )

    def test_audit_record_keeps_the_whole_prompt(self, client: TestClient):
        """审计记录不允许截断。

        回归背景：revision_history 里原本按 600 字截断，而真实的风格迁移提示词有
        700~800 字 —— 被切掉的恰好是尾部的史料线索与 Final style anchor，
        即决定风格忠实度的两块；同时它还会让「逐字比对」对不上，
        把记录失真误读成「提示词锁定失效」。

        提示词必须长到能触发旧上限，否则这条测试没有意义。
        """
        long_prompt = (
            "A museum documentary photograph of the Bronze Standing Figure of Sanxingdui. "
            + "Preserve the matte oxidized patina and the layered bronze texture. " * 10
            + "Neutral white balance, low saturation, dark exhibition hall, 85mm lens."
        )
        assert len(long_prompt) > 600, "测试前提：提示词必须超过旧截断阈值"

        response = client.post(
            "/api/restore",
            json={"kind": "scene", "prompt_override": {"prompt": long_prompt}},
            timeout=60,
        )
        assert response.status_code == 200
        first_round = response.json()["revision_history"][0]
        assert first_round["prompt"] == long_prompt
        assert len(first_round["prompt"]) == len(long_prompt)

    def test_override_rejects_absurd_params(self, client: TestClient):
        response = client.post(
            "/api/restore",
            json={
                "kind": "scene",
                "prompt_override": {"prompt": self.CHOSEN_PROMPT, "width": 99},
            },
        )
        assert response.status_code == 422

    def test_override_rejects_short_prompt(self, client: TestClient):
        response = client.post(
            "/api/restore",
            json={"kind": "scene", "prompt_override": {"prompt": "hi"}},
        )
        assert response.status_code == 422


class TestRewriteBecomesTheSubjectBlock:
    """小模型写的 `rewrite` 必须落在**第一块**（块序就是权重）。

    分工是刻意的，两边都不能越界：

    - **外观**（冠饰、祭服、材质）来自词典。模型写这些会编 ——
      「青铜」写成「黄铜」、「三层祭服」写成「唐装」都实测出现过；
    - **动作与主次**（他在做什么、谁是画面主角）来自模型。
      规则写不好这个：`composition` 只能给机位景别，给不出「主持祭祀」。

    所以 `rewrite` 的定位是「外观之后的第二句」，不是「替代外观」。
    """

    REWRITE = (
        "He stands before the stone altar with both arms raised, "
        "and is the visual anchor of the image in full figure."
    )

    def _proposals(self, *, role: str):
        intent = rule_intent("大祭司在祭祀台前主持祭祀")
        intent["subject_role"] = role
        intent["rewrite"] = self.REWRITE
        return build_rule_proposals(intent, [])

    def _prompt(self, *, role: str) -> str:
        """取提示词，并断言 `rewrite` 排在所有后续块之前。

        用下标比较而不是 `split(". ")`：词典描述本身就是含逗号的长句，
        按句号切会切在**第一块内部**，断言会看错位置。
        """
        prompt = self._proposals(role=role)[0]["prompt"]
        at = prompt.index("both arms raised")
        for later in ("Composition:", "Camera:", "Lighting:"):
            assert at < prompt.index(later), f"{role}: rewrite 没排在第一块"
        return prompt

    def test_person_role_puts_rewrite_in_the_first_block(self):
        prompt = self._prompt(role="person")
        assert prompt.startswith("Main subject:")
        assert "ancient Shu high priest" in prompt   # 外观来自词典
        assert prompt.index("both arms raised") < prompt.index("Worn or held")

    def test_artefact_role_puts_rewrite_in_the_first_block(self):
        prompt = self._prompt(role="artefact")
        assert prompt.startswith("Subject artefact:")
        assert "Bronze Zongmu" in prompt

    def test_scene_role_puts_rewrite_in_the_first_block(self):
        prompt = self._prompt(role="scene")
        assert prompt.startswith("Main subject: the scene as a whole")

    def test_without_rewrite_the_old_anchor_sentence_still_applies(self):
        """LLM 降级时没有 `rewrite` —— 行为必须与之前完全一致，不能丢掉主次声明。"""
        intent = rule_intent("大祭司在祭祀台前主持祭祀")
        intent["subject_role"] = "person"
        prompt = build_rule_proposals(intent, [])[0]["prompt"]
        assert "The figure is the visual anchor" in prompt

    def test_user_brief_is_not_appended_when_rewrite_exists(self):
        """`rewrite` 一进第一块，末尾就不该再粘一遍用户原话。

        原先把用户原话整句粘在最后，等于把真正的诉求降级成脚注 ——
        整段模板都在讲器物，模型自然只画器物。重复还会稀释第一块的权重，
        并吃掉免费通道仅有的 256 字预算。
        """
        prompt = self._proposals(role="person")[0]["prompt"]
        assert "Additional requirement from the user" not in prompt

    def test_brief_is_still_used_as_fallback_without_rewrite(self):
        intent = rule_intent("大祭司在祭祀台前主持祭祀")
        intent["subject_role"] = "person"
        prompt = build_rule_proposals(intent, [])[0]["prompt"]
        assert "Additional requirement from the user" in prompt

    def test_prompt_has_no_double_period(self):
        """`prompt_block()` 自带句点，拼接处不能再补一个 —— 实测出过 `site..`。"""
        for proposal in self._proposals(role="artefact"):
            assert ".. " not in proposal["prompt"], proposal["id"]
            assert not proposal["prompt"].endswith(".."), proposal["id"]


class TestIntentPromptStaysCheap:
    """意图解析在**关键路径**上 —— 每轮对话用户都在等它。

    原先它借的是 planner 的 qwen3-max，同时喂进去 5 张全表 + 4 轮对话 +
    完整上一轮 JSON，再要求输出 500 token。三件事全在给首包延迟加码，
    而它做的其实是最轻的活（候选表都给了，只是选一下 + 写一句改写）。
    """

    def test_shortlist_prefers_entries_present_in_the_text(self):
        from app.conversation.agent import _shortlist

        picked = _shortlist(
            "祭祀台", ["博物馆展厅", "祭祀台", "祭坛", "遗址考古现场", "神庙祭坛"]
        )
        assert picked.startswith("祭祀台"), "命中的候选必须排在最前"
        assert len(picked.split("、")) <= 4

    def test_shortlist_still_offers_options_when_nothing_matches(self):
        """一个都没命中时也要给几个候选 —— 否则模型只能留空，槽位白丢。"""
        from app.conversation.agent import _shortlist

        assert _shortlist("随便说说", ["甲", "乙", "丙"]) == "甲、乙、丙"

    def test_relic_candidates_match_aliases(self):
        """用户说的是「纵目」，规范名却是「青铜纵目面具」——只匹配标签它就不在候选里。"""
        from app.conversation.agent import _relic_candidates

        assert _relic_candidates("青铜纵目面具在展厅里").startswith("青铜纵目面具")
        assert _relic_candidates("纵目").startswith("青铜纵目面具")

    def test_prior_slots_drops_everything_but_the_key_slots(self):
        from app.conversation.agent import _prior_slots

        slim = _prior_slots(
            {
                "kind": "scene",
                "subject_role": "person",
                "identity": "大祭司",
                "stated": ["identity"],
                "brief": "很长的一段原话复述，对判断这句在说什么没有帮助",
                "evidence_ids": ["doc-1", "doc-2"],
            }
        )
        assert "大祭司" in slim
        assert "brief" not in slim
        assert "evidence_ids" not in slim

    def test_prior_slots_does_not_leak_assumed_defaults(self):
        """缺省补的值不能冒充「用户上一轮说过的」喂给意图 LLM。

        `identity` / `scene` 被缺省补成「大祭司 / 黑色背景展陈」后永远非空，
        之前一定会随 `_prior_slots` 发出去，模型会以为自己上一轮真听用户说过。
        """
        from app.conversation.agent import _prior_slots

        slim = _prior_slots(
            {
                "kind": "scene",
                "subject_role": "scene",
                "identity": "大祭司",       # 缺省补的，用户没说过
                "scene": "黑色背景展陈",     # 缺省补的，用户没说过
                "stated": [],               # 账本为空 = 用户什么都没说
            }
        )
        assert "大祭司" not in slim
        assert "黑色背景展陈" not in slim

    def test_prior_slots_handles_an_empty_prior(self):
        from app.conversation.agent import _prior_slots

        assert _prior_slots({}) == "（无）"

    def test_prompts_stay_short(self):
        """给系统提示词设上限，是为了防止以后往里加规则。

        需要加规则时应该先问：这条规则能不能落到词典或规则层？
        落不下去才占这里的预算 —— 而每 100 字都摊在每一轮对话的首包上。
        """
        from app.conversation.agent import INTENT_SYSTEM, INTENT_TEMPLATE

        assert len(INTENT_SYSTEM) < 900, f"意图系统提示词 {len(INTENT_SYSTEM)} 字"
        assert len(INTENT_TEMPLATE) < 400, f"意图模板 {len(INTENT_TEMPLATE)} 字"

    def test_intent_timeout_is_bounded(self):
        """必须有延迟上限：超时退回规则解析，而不是让用户一直等。"""
        from app.conversation.agent import INTENT_TIMEOUT_SECONDS

        assert 0 < INTENT_TIMEOUT_SECONDS <= 10

    def test_rewrite_does_not_survive_into_the_next_turn(self):
        """`rewrite` 是「这一句」的，必须每轮重写。

        其余槽位可以继承（用户常常只补充一个维度），但这句话一旦继承下来，
        用户换了主体之后，提示词第一块还在描述上一个画面。
        """
        prior = rule_intent("大祭司在祭祀台前主持祭祀")
        prior["rewrite"] = "He stands before the stone altar."

        nxt = rule_intent("换成面具的材质特写", prior)

        assert "rewrite" not in nxt
