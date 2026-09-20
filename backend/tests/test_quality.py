"""客观风格指标与质检门的可判定性测试。

这一层用合成图像做断言，而不是用真实生成图：合成图的期望属性是**可控**的
（我造一张大面积纯白高光的图，它就必然触发 specular 违规），
因此断言稳定、不依赖任何模型与网络。

真实生成图的端到端表现由 `app/eval/harness.py` 负责，二者分工不同：
这里是「尺子准不准」，那里是「东西好不好」。
"""

from __future__ import annotations

import asyncio
import io

import numpy as np
import pytest
from PIL import Image

from app.quality import objective
from app.quality.style_profiles import PROFILES, resolve_profile


def render(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array.astype("uint8")).save(buffer, format="PNG")
    return buffer.getvalue()


def solid(color: tuple[int, int, int], size: int = 320) -> bytes:
    return render(np.full((size, size, 3), color, dtype=np.float32))


def matte_bronze(size: int = 320, seed: int = 7) -> bytes:
    """构造「哑光青铜」：低饱和绿褐色调 + 细颗粒噪声 + 适度起伏。"""
    rng = np.random.default_rng(seed)
    base = np.zeros((size, size, 3), dtype=np.float32)
    base[..., 0] = 96   # R
    base[..., 1] = 108  # G（略偏绿，模拟孔雀石锈）
    base[..., 2] = 88   # B
    grain = rng.normal(0, 9, size=(size, size, 1))
    base += grain
    # 低频起伏，模拟器物表面的铸造起伏
    ys, xs = np.mgrid[0:size, 0:size] / size
    relief = 16 * np.sin(6 * np.pi * xs) * np.cos(5 * np.pi * ys)
    base += relief[..., None]
    return render(np.clip(base, 0, 255))


def glossy_saturated(size: int = 320) -> bytes:
    """构造「镜面高光 + 过饱和」的典型失败样例：亮青绿底 + 大面积纯白高光斑。"""
    array = np.zeros((size, size, 3), dtype=np.float32)
    array[..., 0] = 20
    array[..., 1] = 235
    array[..., 2] = 200
    array[90:230, 90:230] = 255.0  # 大面积近白高光
    return render(array)


class TestObjectiveMetrics:
    def test_blank_image_is_hard_failure(self):
        score = objective.evaluate(solid((24, 26, 30)), PROFILES["museum_doc"])
        assert score.hard_fail is not None
        assert score.score == 0.0

    def test_undecodable_bytes_are_handled(self):
        score = objective.evaluate(b"not an image", PROFILES["museum_doc"])
        assert score.hard_fail is not None

    def test_glossy_image_triggers_specular_violation(self):
        score = objective.evaluate(glossy_saturated(), PROFILES["museum_doc"])
        metrics = [item.metric for item in score.violations]
        assert "specular_ratio" in metrics or "saturation_mean" in metrics
        assert score.metrics.specular_ratio > PROFILES["museum_doc"].specular_max

    def test_matte_bronze_has_low_specular_ratio(self):
        score = objective.evaluate(matte_bronze(), PROFILES["museum_doc"])
        assert score.metrics.specular_ratio < PROFILES["museum_doc"].specular_max

    def test_matte_bronze_beats_glossy(self):
        bronze = objective.evaluate(matte_bronze(), PROFILES["museum_doc"])
        glossy = objective.evaluate(glossy_saturated(), PROFILES["museum_doc"])
        assert bronze.score > glossy.score

    def test_scoring_is_deterministic(self):
        """同一张图两次评分必须完全一致 —— 评估报告要站得住，这一点是前提。"""
        payload = matte_bronze(seed=11)
        first = objective.evaluate(payload, PROFILES["photo_real"])
        second = objective.evaluate(payload, PROFILES["photo_real"])
        assert first.score == second.score
        assert first.metrics.to_dict() == second.metrics.to_dict()

    def test_directives_are_actionable_strings(self):
        score = objective.evaluate(glossy_saturated(), PROFILES["museum_doc"])
        assert score.violations, "高光/饱和超标的图必须产出违规项"
        for violation in score.violations:
            # 指令要能直接喂给出图模型：非空、有长度、不是纯指标名
            assert isinstance(violation.directive, str)
            assert len(violation.directive) >= 12


class TestStyleProfiles:
    def test_no_duplicate_aliases(self):
        seen: dict[str, str] = {}
        for profile in PROFILES.values():
            for alias in profile.aliases:
                key = alias.lower()
                assert key not in seen, f"别名冲突: {alias} 同时属于 {seen.get(key)} 与 {profile.key}"
                seen[key] = profile.key

    def test_creative_profile_has_relaxed_specular_limit(self):
        assert PROFILES["cyberpunk"].creative is True
        assert PROFILES["cyberpunk"].specular_max > PROFILES["museum_doc"].specular_max

    @pytest.mark.parametrize(
        ("needle", "expected"),
        [
            ("博物馆纪实摄影", "museum_doc"),
            ("考古档案照片", "archival"),
            ("赛博古蜀", "cyberpunk"),
            ("最小干预修复", "restoration"),
            ("青铜纹饰", "bronze_texture"),
            ("黄金雕刻", "gold_relief"),
            ("玉石质感", "jade_texture"),
            ("考古素描", "sketch"),
            ("神庙壁画", "temple_mural"),
            ("史诗油画风", "epic_oil"),
            ("工笔重彩", "gongbi"),
        ],
    )
    def test_frontend_labels_resolve(self, needle: str, expected: str):
        # 前端下拉框里的每一个选项都必须能解析到档案，否则会静默退化成 default
        assert resolve_profile(needle).key == expected

    def test_unknown_label_falls_back_to_default(self):
        assert resolve_profile("某种不存在的风格").key == "default"
        assert resolve_profile(None).key == "default"

    @pytest.mark.parametrize(
        ("sentence", "expected"),
        [
            # 回归：photo_real 声明在 archival 之前且带泛别名「照片」，
            # 原先的「先声明谁赢」双向子串匹配会把整句判给 photo_real
            ("把青铜面具改成考古档案照片的风格", "archival"),
            ("请给我一版博物馆纪实摄影的效果", "museum_doc"),
            ("用赛博朋克的感觉重做青铜神树", "cyberpunk"),
            ("改成神庙壁画的质感", "temple_mural"),
            ("想要玉石质感的青铜面具", "jade_texture"),
        ],
    )
    def test_profile_resolution_prefers_the_longest_alias(self, sentence: str, expected: str):
        """整句输入必须命中「最精确」的别名，而不是 PROFILES 里最先声明的那个。

        这直接决定出图 prompt 的 Style 块与质检的数值区间取哪一套，
        所以其结果不允许依赖字典声明顺序。
        """
        assert resolve_profile(sentence).key == expected

    def test_profile_resolution_is_declaration_order_independent(self):
        """把字典顺序反过来，解析结果必须一致 —— 否则就是顺序耦合。"""
        import app.quality.style_profiles as module

        sentences = [
            "把青铜面具改成考古档案照片的风格",
            "请给我一版博物馆纪实摄影的效果",
            "想要玉石质感的青铜面具",
        ]
        baseline = [resolve_profile(text).key for text in sentences]
        original = dict(module.PROFILES)
        try:
            module.PROFILES.clear()
            module.PROFILES.update(reversed(list(original.items())))
            reordered = [resolve_profile(text).key for text in sentences]
        finally:
            module.PROFILES.clear()
            module.PROFILES.update(original)
        assert reordered == baseline

    def test_anachronism_keywords_in_negative_prompt(self):
        """时代错配红线必须写进负向词，否则出图侧毫无约束。"""
        for key in ("museum_doc", "photo_real", "restoration", "archival"):
            negative = PROFILES[key].negative_prompt().lower()
            assert "iron" in negative
            assert "porcelain" in negative
            assert "chinese characters inscription" in negative


class TestAnachronismCeilingIsExplainable:
    """「被封顶」与「分数不够」必须能分开看。

    实测有一次：融合分算出来是 **0.745 —— 已经越过 0.72 阈值** ——
    只因 VLM 报了一处时代错配（明清斗拱），被 `ANACHRONISM_CEILING` 压到 0.40。

    界面上只显示「0.40 / 阈值 0.72」，读起来就像「模型画得很差」，
    于是处置方向变成调提示词、换模型 —— 而真正该做的是修那一处错配。
    `Verdict` 因此必须同时给出「压制前的分」与「压制原因」。
    """

    @staticmethod
    def _evaluate(monkeypatch, *, anachronisms: list[str], judge: float):
        from app.quality import style_guard

        async def fake_judge(self, **kwargs):  # noqa: ANN001, ARG001
            return (
                judge,
                {key: judge * 10 for key in style_guard.JUDGE_DIMENSIONS},
                anachronisms,
                ["remove the dougong brackets"] if anachronisms else [],
                "测试判定",
            )

        monkeypatch.setattr(style_guard.StyleGuard, "_judge", fake_judge)
        # `enabled` 是只读属性（`settings.has_dashscope_key`）。测试环境没有 Key，
        # 不打开的话 `evaluate` 会整段跳过裁判：`judge_score` 为 None、
        # `anachronisms` 永远为空 —— 于是这些用例会在「什么都没测到」的情况下通过。
        monkeypatch.setattr(type(style_guard.registry), "enabled", property(lambda self: True))
        return asyncio.run(
            style_guard.guard.evaluate(
                image_bytes=matte_bronze(),
                profile=PROFILES["photo_real"],
                artifact_name="青铜纵目面具",
                user_intent="复原大祭司在祭祀台前主持祭祀",
                round_index=0,
                material=None,
                tracer=None,
            )
        )

    def test_capped_verdict_records_raw_score_and_reason(self, monkeypatch):
        from app.quality.style_guard import ANACHRONISM_CEILING

        verdict = self._evaluate(
            monkeypatch, anachronisms=["明清斗拱（现代官式建筑构件）"], judge=0.95
        )

        assert verdict.score == pytest.approx(ANACHRONISM_CEILING)
        assert verdict.capped_by == "anachronism"
        assert verdict.raw_score is not None and verdict.raw_score > verdict.score
        # 必须透出到接口层，否则界面无从解释
        assert verdict.to_dict()["capped_by"] == "anachronism"
        assert verdict.to_dict()["raw_score"] == pytest.approx(verdict.raw_score, abs=1e-4)

    def test_clean_verdict_is_not_labelled_as_capped(self, monkeypatch):
        """没有时代错配时不得声称「被封顶」——那是另一种误导。"""
        verdict = self._evaluate(monkeypatch, anachronisms=[], judge=0.9)

        assert verdict.capped_by == ""
        assert verdict.to_dict()["capped_by"] is None
        assert verdict.score > 0.40

    def test_ceiling_flag_never_lies_about_the_direction(self, monkeypatch):
        """声称被封顶 ⟺ 原始分确实被压低了。

        反向也必须成立：本来就低于封顶线的图不能被说成「只差一处错配」——
        那是把「画得不够好」粉饰成「几乎合格」，同样会把人带偏。
        """
        for judge in (0.0, 0.3, 0.95):
            verdict = self._evaluate(monkeypatch, anachronisms=["现代人物"], judge=judge)
            assert verdict.raw_score is not None
            if verdict.capped_by:
                assert verdict.raw_score > verdict.score
                assert verdict.score == pytest.approx(0.40)
            else:
                assert verdict.raw_score == pytest.approx(verdict.score, abs=1e-4)


class TestJudgedDenominatorExcludesUnfinished:
    """通过率的分母只能包含**真的判过**的任务。

    原实现是 `tasks - skipped`，它同时错在两处：

    1. 落占位图时质检是 skipped —— 原注释已经指出，不该进分母；
    2. 被用户**中止**与中途**失败**的任务不在 skipped 里，却会被算进分母。

    第 2 点在中止功能上线后立刻显形：越爱中止，通过率越差，
    而系统本身并没有变坏。一个会随用户操作习惯漂移的指标，比没有指标更糟。
    """

    def test_unfinished_tasks_are_excluded(self):
        from app.storage.repository import _ratio_view

        # 10 个任务：6 个判过（4 通过）、2 个占位图跳过、2 个被中止（没有 decision）
        view = _ratio_view(
            {
                "tasks": 10,
                "passed_count": 4,
                "skipped_count": 2,
                "judged_count": 6,
                "avg_score": 0.8,
                "avg_revisions": 1.0,
                "avg_duration_ms": 1000.0,
            }
        )

        assert view["judged"] == 6
        assert view["pass_rate"] == pytest.approx(4 / 6, abs=1e-4)

    def test_falls_back_when_the_count_is_absent(self):
        """拿不到 `judged_count` 时退回旧算法 —— 不把调用方的桩写死。"""
        from app.storage.repository import _ratio_view

        view = _ratio_view({"tasks": 10, "passed_count": 4, "skipped_count": 2})

        assert view["judged"] == 8

    def test_nothing_judged_reports_none_not_zero(self):
        """一个都没判过时通过率是「无」，不是 0 —— 0 会被读成「全都没过」。"""
        from app.storage.repository import _ratio_view

        view = _ratio_view({"tasks": 3, "passed_count": 0, "skipped_count": 0, "judged_count": 0})

        assert view["passed"] == 0
        assert view["pass_rate"] is None
