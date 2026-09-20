"""风格质检门（VLM 裁判 + 客观指标融合）。

融合公式
--------
    score = w_obj * objective + w_judge * judge
    w_judge = 0.60（VLM 可用时） / 0.00（降级时，w_obj 提到 1.0）

为什么不是 0.5/0.5：客观指标虽然稳定，但**无法识别时代错配与形制走样**——
一张饱和度完美、质感完美但给青铜大立人穿上明代官服的图，客观分可能很高。
因此语义判断（VLM）权重更高；而客观指标的作用是**防止 VLM 幻觉放水**：
VLM 给高分但客观指标塌方（纯色、镜面高光）时，用 `min()` 兜住。

一票否决项
----------
1. VLM 报告 anachronism（时代错配）→ 分数直接封顶 0.40，无论其他维度多高；
2. 客观硬失败（空图/无法解码）→ 0 分；
3. 创作型风格（赛博朋克等）只检查器物轮廓一致性，不检查色域与质感。

输出的 `feedback` 是**可直接转化为 prompt 修改指令**的自然语言，
这正是 Self-Correction 能闭环的关键 —— 不是「不合格，重做」，
而是「把镜面高光降下来、补足锈绿占比、去掉铁器元素」。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.core.tracing import TraceRecorder
from app.models.llm import image_part, text_part
from app.models.registry import registry
from app.quality import objective
from app.quality.style_profiles import StyleProfile

if TYPE_CHECKING:  # pragma: no cover
    from app.agents.lexicon import MaterialSpec

logger = get_logger("app.quality.guard")

JUDGE_DIMENSIONS = (
    "material_fidelity",   # 材质质感：是否为该材质应有的质感 / 边缘圆钝
    "color_fidelity",      # 色彩色域：在该材质的合理区间内，饱和度克制
    "form_fidelity",       # 形制忠实度：器物比例与结构与实物一致
    "era_compliance",      # 时代合规：无时代错配元素
    "source_fidelity",     # 来源忠实：残缺未被臆造美化
)

DIMENSION_WEIGHTS = {
    "material_fidelity": 0.20,
    "color_fidelity": 0.22,
    "form_fidelity": 0.26,
    "era_compliance": 0.20,
    "source_fidelity": 0.12,
}

ANACHRONISM_CEILING = 0.40
JUDGE_WEIGHT = 0.60


@dataclass(slots=True)
class Verdict:
    passed: bool
    score: float
    objective_score: float
    judge_score: float | None
    threshold: float
    round_index: int
    dimensions: dict[str, float] = field(default_factory=dict)
    violations: list[dict[str, Any]] = field(default_factory=list)
    anachronisms: list[str] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    reasoning: str = ""
    degraded: bool = False
    # 被一票否决「压下来之前」的融合分，以及压制原因（目前只有 anachronism）。
    #
    # 没有这两个字段时，「总分 0.40」与「客观分 0.81」会并排出现而无人能解释：
    # 实测有一轮融合分是 0.745 —— **已经越过 0.72 阈值** —— 只因 VLM 报了
    # 时代错配被封顶到 0.40。用户看到 0.40 会以为模型画得很差，
    # 从而去调提示词或换模型，而真正该做的是修那一处时代错配。
    # 「被封顶」和「分数不够」是两种完全不同的处境，必须能分开看。
    raw_score: float | None = None
    capped_by: str = ""
    # 这次判定用的「标尺」是什么，必须能对外说清楚：
    # 否则用户看到「补足锈绿」这种意见，无法判断是模型错了还是标尺配错了。
    material_label: str | None = None
    material_key: str | None = None
    expected_family_min: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 4),
            "objective_score": round(self.objective_score, 4),
            "judge_score": round(self.judge_score, 4) if self.judge_score is not None else None,
            "threshold": self.threshold,
            "round_index": self.round_index,
            "dimensions": {k: round(v, 3) for k, v in self.dimensions.items()},
            "violations": self.violations,
            "anachronisms": self.anachronisms,
            "feedback": self.feedback,
            "reasoning": self.reasoning,
            "degraded": self.degraded,
            "raw_score": round(self.raw_score, 4) if self.raw_score is not None else None,
            "capped_by": self.capped_by or None,
        }


JUDGE_SYSTEM = """你是三星堆文物数字复原项目的**质检专家**，同时具备考古学与文物摄影的双重背景。
你的任务是判断一张 AI 生成/复原的影像是否达到「可用」标准。

评判必须严格、具体，不要给「还不错」这类模糊结论。你的每一条扣分都必须指向画面中的
具体位置或现象，并且给出可执行的修改指令（供出图模型下一轮参考）。

特别注意：
- 三星堆属青铜时代，**铁器、瓷器、汉字铭文、佛教元素、明清服饰**等一律视为严重时代错配；
- 材质必须以【材质真相】为准：青铜应为哑光锈层，金应为哑金箔，玉应为温润玉质；
  **不得因为「三星堆等于青铜」的刻板印象，把金器/玉器/象牙判为材质错误**；
- 器物残缺部位被臆造补齐、或残缺信息被美化掩盖，属于「来源忠实度」失分；
- 学术争议内容（如大立人手中持物）不得以确定方式呈现。

只输出 JSON，不要任何解释性文字或 markdown 代码块。"""


class StyleGuard:
    def __init__(self) -> None:
        self.threshold = settings.style_threshold

    async def evaluate(
        self,
        *,
        image_bytes: bytes | None,
        profile: StyleProfile,
        artifact_name: str,
        user_intent: str,
        round_index: int = 0,
        material: "MaterialSpec | None" = None,
        tracer: TraceRecorder | None = None,
        skip_judge: bool = False,
    ) -> Verdict:
        # 材质参与客观判定：色域要求不能按青铜一刀切，否则黄金/玉/象牙会被误判并要求回炉
        # 客观指标是 CPU 密集的（PIL 解码 + resize + numpy 全图统计）。
        # 在 async 函数里直接跑会阻塞事件循环 —— 期间所有 SSE 流与其它请求
        # 全部停顿。丢进线程池，让事件循环继续服务其它连接。
        if image_bytes:
            obj = await asyncio.to_thread(objective.evaluate, image_bytes, profile, material)
        else:
            obj = objective.ObjectiveScore(
                score=0.0,
                violations=[],
                metrics=objective._empty_metrics(),
                hard_fail="缺少图像数据",
            )

        judge_score: float | None = None
        dimensions: dict[str, float] = {}
        anachronisms: list[str] = []
        judge_feedback: list[str] = []
        reasoning = ""
        degraded = True

        if image_bytes and obj.hard_fail is None and registry.enabled and not skip_judge:
            try:
                judge_score, dimensions, anachronisms, judge_feedback, reasoning = await self._judge(
                    image_bytes=image_bytes,
                    profile=profile,
                    artifact_name=artifact_name,
                    user_intent=user_intent,
                    objective_summary=obj.to_dict(),
                    material=material,
                    tracer=tracer,
                )
                degraded = False
            except ProviderUnavailable as exc:
                reasoning = f"VLM 裁判不可用，降级为客观指标判定：{exc}"
                metrics.record_degradation(
                    component="judge", reason=str(exc), fallback="objective_only"
                )
                if tracer is not None:
                    tracer.degraded("quality", reason=str(exc), fallback="objective_only")
        else:
            reasoning = "跳过 VLM 裁判（快速模式或未配置 VLM），仅使用客观指标判定"

        # ── 融合 ────────────────────────────────────────────────────────────
        if judge_score is None:
            score = obj.score
        else:
            score = JUDGE_WEIGHT * judge_score + (1.0 - JUDGE_WEIGHT) * obj.score
            # 防止 VLM 幻觉放水：客观指标塌方时用 min 兜底
            if obj.score < 0.5:
                score = min(score, (obj.score + judge_score) / 2.0)

        # 封顶前先记下原始融合分：只在封顶**真的把分数压低了**时才声称「被封顶」，
        # 否则本来是 0.30 的图会被说成「只因时代错配」，那是另一种误导。
        raw_score = float(score)
        capped_by = ""
        if anachronisms:
            score = min(score, ANACHRONISM_CEILING)
            if raw_score > ANACHRONISM_CEILING:
                capped_by = "anachronism"

        feedback = [item.directive for item in obj.violations if item.severity >= 0.25]
        feedback.extend(judge_feedback)

        if anachronisms:
            feedback.insert(
                0,
                "【一票否决·时代错配】移除画面中的以下元素，它们在三星堆所属年代并不存在："
                + "、".join(anachronisms[:6]),
            )

        passed = score >= self.threshold and obj.hard_fail is None

        verdict = Verdict(
            passed=passed,
            score=float(score),
            objective_score=obj.score,
            judge_score=judge_score,
            threshold=self.threshold,
            round_index=round_index,
            dimensions=dimensions,
            violations=[item.to_dict() for item in obj.violations],
            anachronisms=anachronisms,
            feedback=_dedupe(feedback)[:6],
            reasoning=reasoning,
            degraded=degraded,
            raw_score=raw_score,
            capped_by=capped_by,
            material_label=material.label if material is not None else None,
            material_key=material.key if material is not None else None,
            expected_family_min=objective.effective_family_min(profile, material),
        )

        logger.info(
            "质检完成",
            extra={
                "round": round_index,
                "score": round(verdict.score, 3),
                "objective": round(obj.score, 3),
                "judge": round(judge_score, 3) if judge_score is not None else None,
                "passed": passed,
                "material": verdict.material_key,
            },
        )
        return verdict

    async def _judge(
        self,
        *,
        image_bytes: bytes,
        profile: StyleProfile,
        artifact_name: str,
        user_intent: str,
        objective_summary: dict[str, Any],
        material: "MaterialSpec | None" = None,
        tracer: TraceRecorder | None,
    ) -> tuple[float, dict[str, float], list[str], list[str], str]:
        import base64

        data_uri = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")

        # 材质期望必须显式告诉裁判。
        # 否则它会按「三星堆 = 青铜」的先验去评金/玉/象牙，把正确的材质判成错误。
        if material is not None:
            material_block = (
                f"【目标材质】{material.label}\n"
                f"【材质真相（以它为准，不要按青铜先验臆断）】{material.surface_truth}\n"
                f"【该材质不应出现的色域】{list(material.forbidden_families) or '无'}"
            )
            material_fidelity_desc = f"材质质感：是否为{material.label}的正确质感（{material.surface_truth}）"
        else:
            material_block = "【目标材质】未指定（按画面上下文判断，不要强行套用青铜先验）"
            material_fidelity_desc = "材质质感：画面材质是否自洽且符合所声称的器类"

        prompt = f"""请对下面这张「三星堆文物复原/生成图」做质量判定。

【目标文物】{artifact_name}
【用户意图】{user_intent}
【目标风格】{profile.label}
{material_block}
【风格评分细则】
{profile.rubric}

【允许的色彩家族占比下限】{json.dumps(objective.effective_family_min(profile, material), ensure_ascii=False)}
【允许的饱和度区间】{list(profile.saturation_range)}
【允许的近白高光占比上限】{profile.specular_max}
（创作型风格：{"是，色域与质感阈值放宽" if profile.creative else "否，需严格符合上述区间"}）

【系统预先计算出的客观指标】（供你参考，但请以你自己的观察为准，不要被它带偏）
{json.dumps(objective_summary.get("metrics", {}), ensure_ascii=False)}

请输出如下 JSON：
{{
  "material_fidelity": <0-10 {material_fidelity_desc}>,
  "color_fidelity": <0-10 色彩色域：是否落在该材质的合理色彩区间内（不要按青铜锈绿去要求金/玉/象牙），饱和度是否克制>,
  "form_fidelity": <0-10 形制忠实度：器物比例、结构、纹饰是否与三星堆实物一致>,
  "era_compliance": <0-10 时代合规：画面中是否存在不属于该年代的器物/服饰/文字>,
  "source_fidelity": <0-10 来源忠实：残缺部位是否保留、是否被臆造补齐或美化>,
  "anachronisms": ["列举画面中出现的时代错配元素，没有则给空数组"],
  "issues": ["列举具体视觉问题，每条描述画面中的位置与现象"],
  "directives": ["给出可直接写进下一轮出图 prompt 的英文修改指令，每条 <= 20 词"],
  "reasoning": "<80 字以内的总体判断理由>"
}}"""

        payload, result = await registry.call_json(
            "judge",
            [
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": [text_part(prompt), image_part(data_uri)]},
            ],
            required_keys=("material_fidelity", "color_fidelity", "form_fidelity", "era_compliance"),
            temperature=0.0,
            max_tokens=1200,
            tag="style_judge",
        )

        if tracer is not None:
            tracer.llm(
                "quality",
                model=result.model,
                latency_ms=result.latency_ms,
                usage=result.usage,
                tag="style_judge",
            )

        dimensions: dict[str, float] = {}
        detail: dict[str, Any] = dict(payload)
        for key in JUDGE_DIMENSIONS:
            try:
                dimensions[key] = max(0.0, min(10.0, float(detail.get(key, 0.0))))
            except (TypeError, ValueError):
                dimensions[key] = 0.0

        weighted = sum(dimensions[key] * DIMENSION_WEIGHTS[key] for key in JUDGE_DIMENSIONS)
        judge_score = weighted / 10.0

        anachronisms = [str(item) for item in (detail.get("anachronisms") or []) if str(item).strip()]
        directives = [str(item) for item in (detail.get("directives") or []) if str(item).strip()]
        issues = [str(item) for item in (detail.get("issues") or []) if str(item).strip()]

        if issues:
            directives = [*directives, "需修正的视觉问题：" + "；".join(issues[:3])]

        return judge_score, dimensions, anachronisms, directives, str(detail.get("reasoning") or "")


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


guard = StyleGuard()
