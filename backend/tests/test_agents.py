"""Agent 层测试：规划骨架、词典解析、调度护栏、质检决策。

全部在「无 Key」路径下断言 —— 这恰好覆盖了系统最容易退化的场景。
带 Key 的 LLM 路径由 `app/eval/harness.py` 做端到端质量评估，不在这里重复。
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents import lexicon, planner
from app.agents import supervisor as supervisor_agent
from app.agents.retrieval_worker import build_evidence_prompt_block
from app.core.config import settings


class TestEvidencePromptBlock:
    def test_cautions_do_not_leak_into_the_image_prompt(self):
        """史料争议 / 摘要局限是**给文案层的认知提示**，不是画面内容。

        回归背景：它曾被写成 `Must not be presented as settled fact: <中文元信息>`
        注进英文出图 prompt —— 出图模型既无法表现"不确定性"，只能产出混语噪音。
        需要它的地方（科普文案）直接读 `retrieval["cautions"]`，不受影响。
        """
        block = build_evidence_prompt_block(
            {
                "image_cues": ["金箔", "哑光质感"],
                "key_facts": ["金面具以捶揲工艺成形"],
                "cautions": ["本条摘要由抽取式策略生成（未使用语言模型），语义连贯性有限"],
            }
        )
        assert "金箔" in block
        assert "Documented facts" in block
        assert "Must not be presented" not in block
        assert "抽取式策略" not in block


class TestLexicon:
    def test_relic_resolution_by_alias(self):
        for alias, expected_key in (
            ("青铜大立人", "standing_figure"),
            ("大立人", "standing_figure"),
            ("青铜纵目面具", "zongmu_mask"),
            ("纵目", "zongmu_mask"),
            ("青铜神树", "bronze_tree"),
            ("金杖", "gold_scepter"),
            ("玉璋", "jade_zhang"),
        ):
            spec = lexicon.resolve_relic(alias)
            assert spec is not None, f"{alias} 未解析到词典条目"
            assert spec.key == expected_key

    def test_unknown_relic_returns_none(self):
        assert lexicon.resolve_relic("不存在的文物") is None

    def test_form_notes_are_english_visual_constraints(self):
        """形制要点要能直接进 prompt，因此必须是英文视觉描述而不是中文笔记。"""
        spec = lexicon.resolve_relic("青铜大立人")
        notes = spec.form_notes
        assert "crown" in notes
        assert "robe" in notes
        assert not any("\u4e00" <= char <= "\u9fff" for char in notes)

    def test_identity_and_scene_resolution(self):
        assert "priest" in lexicon.resolve_identity("大祭司")
        assert "museum" in lexicon.resolve_scene("博物馆展厅").lower()

    def test_frontend_compound_label_degrades_gracefully(self):
        # 旧的「中文｜english tokens」格式必须仍可解析
        assert "high priest" in lexicon.resolve_identity("大祭司｜sxd_standing_figure, high priest")


class TestRulePlanner:
    @pytest.mark.parametrize(
        ("request_payload", "expected_profile"),
        [
            ({"kind": "scene", "item": "青铜大立人", "style": "博物馆纪实摄影"}, "museum_doc"),
            (
                {"kind": "figure", "rank": "大祭司", "style": "真实照片风格"},
                "photo_real",
            ),
            ({"kind": "artifact", "artifact": "残缺玉璋", "method": "写实复原"}, "restoration"),
            ({"kind": "style", "style_preset": "赛博古蜀"}, "cyberpunk"),
        ],
    )
    def test_all_kinds_produce_complete_plan(self, request_payload: dict, expected_profile: str):
        plan = planner.build_rule_plan(request_payload)
        assert plan["profile_key"] == expected_profile
        assert plan["goal"]
        assert len(plan["subtasks"]) >= 4
        assert plan["retrieval_queries"], "必须给出检索 query，否则检索 Worker 无事可做"

        image_spec = plan["image_spec"]
        assert image_spec["prompt"]
        assert image_spec["negative_prompt"]
        assert image_spec["width"] > 0 and image_spec["height"] > 0
        assert plan["acceptance_criteria"]

    def test_scene_plan_locks_relic_form_notes(self):
        plan = planner.build_rule_plan({"kind": "scene", "item": "青铜纵目面具"})
        prompt = plan["image_spec"]["prompt"]
        # 形制要点必须落到 prompt 里，否则通用模型会把纵目面具画成普通人脸
        assert "protruding" in prompt.lower()
        assert plan["locked_form_notes"] == lexicon.resolve_relic("青铜纵目面具").form_notes

    def test_negative_prompt_blocks_anachronism(self):
        plan = planner.build_rule_plan({"kind": "scene", "item": "金杖"})
        negative = plan["image_spec"]["negative_prompt"].lower()
        for token in ("iron tools", "porcelain", "chinese characters inscription"):
            assert token in negative, f"负向词缺少时代错配拦截: {token}"

    def test_artifact_plan_mentions_minimal_intervention(self):
        plan = planner.build_rule_plan(
            {"kind": "artifact", "artifact": "破损青铜面具", "method": "最小干预修复"}
        )
        prompt = plan["image_spec"]["prompt"].lower()
        assert "distinguishable" in prompt
        assert any("补配" in item for item in plan["style_constraints"])

    def test_style_plan_keeps_silhouette_constraint(self):
        plan = planner.build_rule_plan({"kind": "style", "style_preset": "黄金雕刻", "strength": 60})
        assert "silhouette" in plan["image_spec"]["prompt"].lower()

    def test_unknown_item_still_produces_usable_plan(self):
        plan = planner.build_rule_plan({"kind": "scene", "item": "某种未登记器物"})
        assert plan["image_spec"]["prompt"]
        assert plan["profile_key"] == "default"


class TestPlannerDegradation:
    def test_refine_without_key_returns_rule_plan(self):
        base = planner.build_rule_plan({"kind": "scene", "item": "青铜神树"})
        original_key = settings.dashscope_api_key
        settings.dashscope_api_key = ""
        try:
            merged, mode = asyncio.run(planner.refine_with_llm(base, {"kind": "scene"}))
        finally:
            settings.dashscope_api_key = original_key
        assert mode == "rule"
        assert merged == base, "无 Key 时不能篡改规则骨架"


class TestSupervisorGuardrails:
    def test_first_step_is_retrieval(self):
        state = {"plan": {"retrieval_queries": ["青铜神树"]}, "completed": []}
        assert supervisor_agent._rule_route(state, [], 0)[0] == "retrieval"

    def test_skips_retrieval_when_already_done(self):
        state = {"plan": {"retrieval_queries": ["x"]}, "completed": ["planner", "retrieval"]}
        assert supervisor_agent._rule_route(state, ["planner", "retrieval"], 2)[0] == "restoration"

    def test_finishes_when_all_artifacts_present(self):
        state = {"plan": {}, "completed": ["retrieval", "restoration", "copywriting"]}
        assert supervisor_agent._rule_route(state, state["completed"], 6)[0] == "finalize"

    def test_step_budget_forces_convergence(self):
        state = {"plan": {"retrieval_queries": ["x"]}, "completed": []}
        route, reason = supervisor_agent._rule_route(
            state, [], settings.max_graph_steps + 1
        )
        assert route == "finalize"
        assert "上限" in reason

    def test_qa_revision_routes_back_to_restoration(self):
        state = {
            "plan": {"retrieval_queries": ["x"]},
            "completed": ["retrieval", "restoration", "quality"],
            "qa": {"decision": "revise", "feedback": ["饱和度偏高，降低饱和度"]},
            "revisions": 1,
            "max_revisions": 2,
        }
        assert supervisor_agent._rule_route(state, state["completed"], 5)[0] == "restoration"

    def test_qa_feedback_about_evidence_triggers_second_retrieval(self):
        """质检反馈指向「依据不足」时应该补检索，而不是盲目重出图 —— 这是状态路由的价值所在。"""
        state = {
            "plan": {"retrieval_queries": ["x"]},
            "completed": ["retrieval", "restoration", "quality"],
            "qa": {"decision": "revise", "feedback": ["器物形制缺少史料依据，与实物不符"]},
            "revisions": 1,
            "max_revisions": 2,
        }
        assert supervisor_agent._rule_route(state, state["completed"], 5)[0] == "retrieval"

    def test_clamp_rejects_extra_retrieval_rounds(self):
        state = {"completed": ["planner", "retrieval", "retrieval"]}
        assert supervisor_agent._clamp(state, "retrieval", state["completed"], 5) is None

    def test_clamp_rejects_regeneration_after_pass(self):
        state = {"qa": {"passed": True}, "revisions": 0, "max_revisions": 2}
        assert supervisor_agent._clamp(state, "restoration", [], 3) is None

    def test_clamp_rejects_copywriting_before_quality_resolved(self):
        state = {"qa": {"passed": False}, "revisions": 0, "max_revisions": 2}
        assert supervisor_agent._clamp(state, "copywriting", [], 3) is None

    def test_clamp_allows_copywriting_after_giving_up(self):
        state = {"qa": {"passed": False}, "revisions": 2, "max_revisions": 2}
        assert supervisor_agent._clamp(state, "copywriting", [], 8) == "copywriting"

    def test_clamp_forces_finalize_at_budget(self):
        state = {}
        assert (
            supervisor_agent._clamp(state, "restoration", [], settings.max_graph_steps)
            == "finalize"
        )


class TestQualityDecisionLogic:
    """质检决策的四个分支：通过 / 回炉 / 上限放行 / 收益递减早停。"""

    @staticmethod
    def decide(**overrides):
        from app.agents.qa_worker import decide_quality

        params = {
            "passed": False,
            "score": 0.5,
            "previous_score": None,
            "revision": 0,
            "max_revisions": 2,
        }
        params.update(overrides)
        return decide_quality(**params)

    def test_pass_accepts(self):
        decision, reason = self.decide(passed=True, score=0.9)
        assert decision == "accept"
        assert "通过" in reason

    def test_first_failure_requests_revision(self):
        decision, reason = self.decide(score=0.5, previous_score=None, revision=0)
        assert decision == "revise"
        assert "第 1 次" in reason

    def test_keeps_revising_while_improving_within_budget(self):
        decision, _ = self.decide(score=0.60, previous_score=0.45, revision=1, max_revisions=2)
        assert decision == "revise"

    def test_hits_budget_ceiling(self):
        decision, reason = self.decide(score=0.70, previous_score=0.55, revision=2, max_revisions=2)
        assert decision == "give_up"
        assert "上限" in reason

    def test_early_stop_on_diminishing_returns(self):
        # 只提升 0.01，低于 MIN_GAIN → 即使还有预算也停止回炉
        decision, reason = self.decide(score=0.61, previous_score=0.60, revision=0, max_revisions=3)
        assert decision == "give_up"
        assert "收益递减" in reason

    def test_zero_budget_gives_up_immediately(self):
        # max_revisions=0 表示「不允许回炉」，首轮失败即放行
        decision, reason = self.decide(score=0.4, previous_score=None, revision=0, max_revisions=0)
        assert decision == "give_up"
        assert "上限" in reason

    def test_first_round_never_triggers_early_stop(self):
        # 首轮没有历史分可比，因此不应被「收益递减」提前终止
        decision, reason = self.decide(score=0.4, previous_score=None, revision=0, max_revisions=2)
        assert decision == "revise"
        assert "收益递减" not in reason

    def test_min_gain_is_small_but_positive(self):
        from app.agents.qa_worker import MIN_GAIN

        assert 0 < MIN_GAIN < 0.1, "早停阈值过高会浪费预算，过低则无法终止无效回炉"
