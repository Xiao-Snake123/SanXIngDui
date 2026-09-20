"""评估基准（Style Mismatch Benchmark）。

存在的意义：**让「风格错配率下降 X%」这句话可复现、可证伪。**

做法是把「改造前」与「改造后」放到同一把尺子下量：

| 配置 | 流程 | 对应本项目的历史阶段 |
|---|---|---|
| `baseline` | 单次文生图调用，prompt 由前端模板字符串拼接 | 重构前（`aiApi.ts` 的 `createPrompt`） |
| `agent`    | LangGraph 多智能体：规划 → 检索 → 出图 → 质检 → 回炉 | 当前实现 |

关键点：**两种配置的产物由同一个质检器（`StyleGuard`）评分**。
如果 baseline 用一套标准、agent 用另一套，得出的降幅毫无意义。

指标定义
--------
- `mismatch_rate`：风格一致性总分低于阈值（默认 0.72）的样本占比。
  对 baseline 就是「首轮即不达标」的比例；对 agent 是**最终交付**的不达标比例。
- `first_pass_mismatch_rate`（仅 agent）：第一次出图即不达标的比例，
  与 baseline 的 mismatch_rate 对比才能看出「检索 + 规划」本身带来了多少提升，
  与最终 mismatch_rate 对比则能看出「Self-Correction 回炉」额外补了多少。
- `avg_revisions`：平均回炉次数 —— 降幅的代价，必须一起报，否则就是选择性呈现。

用法
----
    python -m app.eval.harness --limit 10
    python -m app.eval.harness --limit 10 --modes baseline agent --engine langgraph
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.blobs import blobs
from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics
from app.graph.runner import RestorationRunner
from app.imagegen.base import ImageRequest, fetch_image_bytes
from app.imagegen.service import get_image_service
from app.quality.style_guard import guard

logger = get_logger("app.eval")


@dataclass(slots=True)
class EvalCase:
    case_id: str
    label: str
    request: dict[str, Any]
    # 该 case 期望被识别为「风格一致」时最关键的文物（用于日志与人工复核）
    focus: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "label": self.label, "request": self.request, "focus": self.focus}


# ── 内置基准集 ──────────────────────────────────────────────────────────────
# 覆盖四个 Tab × 典型风格，且刻意包含「最难」的两类：
# 1) 赛伯朋克等创作型风格（客观指标放宽，主要看 VLM 判断空间一致性）
# 2) 有明确时代错配风险的人物还原（服饰最容易踩红线）
BENCHMARK: list[EvalCase] = [
    EvalCase(
        "scene-standing-museum",
        "青铜大立人 · 博物馆纪实摄影",
        {
            "kind": "scene",
            "identity": "大祭司",
            "scene": "博物馆展厅",
            "item": "青铜大立人",
            "style": "博物馆纪实摄影",
        },
        focus="青铜大立人",
    ),
    EvalCase(
        "scene-mask-archival",
        "纵目面具 · 考古档案照片",
        {
            "kind": "scene",
            "identity": "纵目面具祭司",
            "scene": "黑色背景展陈",
            "item": "青铜纵目面具",
            "style": "考古档案照片",
        },
        focus="青铜纵目面具",
    ),
    EvalCase(
        "scene-tree-epic",
        "青铜神树 · 电影级写实",
        {
            "kind": "scene",
            "identity": "神树守护者",
            "scene": "青铜神树祭坛",
            "item": "青铜神树",
            "style": "电影级写实",
        },
        focus="青铜神树",
    ),
    EvalCase(
        "scene-gold-photo",
        "黄金面具 · 真实照片风格",
        {
            "kind": "scene",
            "identity": "古蜀王者",
            "scene": "三星堆祭祀坑",
            "item": "黄金面具",
            "style": "真实照片风格",
        },
        focus="黄金面具",
    ),
    EvalCase(
        "scene-scepter-photo",
        "金杖 · 真实照片风格",
        {
            "kind": "scene",
            "identity": "古蜀王者",
            "scene": "遗址考古现场",
            "item": "金杖",
            "style": "真实照片风格",
        },
        focus="金杖",
    ),
    EvalCase(
        "scene-cyberpunk",
        "纵目面具 · 赛伯朋克（创作型，阈值放宽）",
        {
            "kind": "scene",
            "identity": "大祭司",
            "scene": "黑色背景展陈",
            "item": "青铜纵目面具",
            "style": "赛伯朋克风格",
        },
        focus="青铜纵目面具",
    ),
    EvalCase(
        "figure-priest",
        "大祭司全身像 · 古蜀人物还原",
        {
            "kind": "figure",
            "gender": "男性",
            "rank": "大祭司",
            "era": "鱼凫王朝",
            "expression": "庄严肃穆",
            "detail": "全身像",
            "style": "真实照片风格",
        },
        focus="人物服饰时代合规",
    ),
    EvalCase(
        "figure-divine",
        "神灵半身像 · 古蜀人物还原",
        {
            "kind": "figure",
            "gender": "神灵（无性别）",
            "rank": "大祭司",
            "era": "蚕丛王朝",
            "expression": "神秘莫测",
            "detail": "半身像",
            "style": "博物馆纪实摄影",
        },
        focus="人物服饰时代合规",
    ),
    EvalCase(
        "artifact-mask-restore",
        "破损青铜面具 · 智能填补",
        {
            "kind": "artifact",
            "artifact": "破损青铜面具",
            "method": "最小干预修复",
            "mode": "博物馆展陈",
            "damage": "35%",
        },
        focus="补配可识别性",
    ),
    EvalCase(
        "artifact-jade-restore",
        "残缺玉璋 · 历史参照",
        {
            "kind": "artifact",
            "artifact": "残缺玉璋",
            "method": "写实复原",
            "mode": "学术报告",
            "damage": "42%",
        },
        focus="玉璋形制",
    ),
    EvalCase(
        "style-bronze",
        "青铜纹饰风格迁移",
        {"kind": "style", "style_preset": "青铜纹饰", "strength": 75},
        focus="轮廓保持",
    ),
    EvalCase(
        "style-jade",
        "玉石质感风格迁移",
        {"kind": "style", "style_preset": "玉石质感", "strength": 60},
        focus="轮廓保持",
    ),
]


# ── baseline：复刻重构前的单次调用 ──────────────────────────────────────────
LEGACY_TEMPLATE = (
    "我希望复原一个三星堆文明的场景，其中主要的文物是——{item}，身份为{identity}，"
    "站在{scene}前，以{style}风格呈现。三星堆文物自然千年氧化斑驳痕迹，肌理清晰，"
    "器物雕刻纹路精致。8K超高清，RAW摄影画质，超高细节，微距质感，青铜器凹凸纹理清晰。"
    "写实纪实摄影，自然光影，镜头无畸变，色彩真实，文物原样忠实还原。"
)
LEGACY_DEFAULT_ITEM = "青铜大立人"


def legacy_prompt(request: dict[str, Any]) -> str:
    """与重构前 `aiApi.ts::createPrompt` 等价的模板拼接。"""
    return LEGACY_TEMPLATE.format(
        item=request.get("item") or request.get("artifact") or LEGACY_DEFAULT_ITEM,
        identity=request.get("identity") or request.get("rank") or "古蜀人物",
        scene=request.get("scene") or request.get("mode") or "三星堆遗址",
        style=request.get("style") or request.get("style_preset") or "写实",
    )


async def run_baseline_case(case: EvalCase) -> dict[str, Any]:
    """单次调用：不检索、不规划增强、不质检、不回炉。"""
    from app.quality.style_profiles import resolve_profile

    started = time.perf_counter()
    profile = resolve_profile(
        case.request.get("style"), case.request.get("style_preset"), case.request.get("method")
    )
    image_request = ImageRequest(
        prompt=legacy_prompt(case.request),
        negative_prompt="lowres, blurry, watermark, text, oversaturated, cartoon, anime, cgi",
        width=1024,
        height=1280,
        kind=str(case.request.get("kind") or "scene"),
    )
    result = await get_image_service().generate(image_request)

    image_bytes: bytes | None = None
    try:
        image_bytes, _ = await fetch_image_bytes(result.image_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("baseline 取图失败: %s", exc)

    verdict = await guard.evaluate(
        image_bytes=image_bytes,
        profile=profile,
        artifact_name=str(case.request.get("item") or case.request.get("artifact") or ""),
        user_intent=legacy_prompt(case.request),
        round_index=0,
    )

    return {
        "case_id": case.case_id,
        "label": case.label,
        "mode": "baseline",
        "passed": verdict.passed,
        "score": verdict.score,
        "objective_score": verdict.objective_score,
        "judge_score": verdict.judge_score,
        "provider": result.provider,
        "degraded": result.degraded,
        "revisions": 0,
        "duration_ms": (time.perf_counter() - started) * 1000,
        "image_url": result.image_url,
        "anachronisms": verdict.anachronisms,
        "top_violations": [item["metric"] for item in verdict.violations[:3]],
    }


async def run_agent_case(case: EvalCase) -> dict[str, Any]:
    started = time.perf_counter()
    runner = RestorationRunner(dict(case.request))
    result = await runner.arun()
    qa = result.get("qa") or {}
    first_round = next(
        (
            item
            for item in (result.get("revision_history") or [])
            if isinstance(item, dict) and item.get("round_index") == 0
        ),
        None,
    )
    return {
        "case_id": case.case_id,
        "label": case.label,
        "mode": "agent",
        "passed": bool(qa.get("passed")),
        "score": float(qa.get("score") or 0.0),
        "objective_score": float(qa.get("objective_score") or 0.0),
        "judge_score": qa.get("judge_score"),
        "provider": result.get("image_provider"),
        "degraded": bool(result.get("image_degraded")),
        "revisions": int(result.get("revisions") or 0),
        "duration_ms": (time.perf_counter() - started) * 1000,
        "image_url": result.get("image_url"),
        "anachronisms": qa.get("anachronisms") or [],
        "top_violations": [item.get("metric") for item in (qa.get("violations") or [])[:3]],
        "first_round_pass": first_round is not None,
        "goal": result.get("goal"),
        "evidence_count": len(result.get("evidence") or []),
        "degraded_components": result.get("degraded_components") or [],
        "errors": result.get("errors") or [],
    }


# ── 汇总 ────────────────────────────────────────────────────────────────────
def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_mode: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        by_mode.setdefault(str(case["mode"]), []).append(case)

    report: dict[str, Any] = {}
    for mode, items in by_mode.items():
        scores = [float(item["score"]) for item in items]
        mismatches = [item for item in items if not item["passed"]]
        entry: dict[str, Any] = {
            "cases": len(items),
            "mismatch_count": len(mismatches),
            "mismatch_rate": round(len(mismatches) / len(items), 4) if items else None,
            "avg_score": round(statistics.fmean(scores), 4) if scores else None,
            "min_score": round(min(scores), 4) if scores else None,
            "avg_duration_ms": round(
                statistics.fmean([float(item["duration_ms"]) for item in items]), 1
            )
            if items
            else None,
            "avg_revisions": round(
                statistics.fmean([float(item.get("revisions") or 0) for item in items]), 3
            )
            if items
            else None,
            "anachronism_cases": [item["case_id"] for item in items if item.get("anachronisms")],
            "degraded_cases": [item["case_id"] for item in items if item.get("degraded")],
            "mismatched_cases": [item["case_id"] for item in mismatches],
        }
        report[mode] = entry

    baseline = report.get("baseline")
    agent = report.get("agent")
    if baseline and agent and baseline["mismatch_rate"] is not None and agent["mismatch_rate"] is not None:
        base_rate = float(baseline["mismatch_rate"])
        agent_rate = float(agent["mismatch_rate"])
        report["delta"] = {
            "mismatch_rate_baseline": base_rate,
            "mismatch_rate_agent": agent_rate,
            "mismatch_rate_absolute_drop": round(base_rate - agent_rate, 4),
            # 相对降幅：仅在 baseline 不为 0 时有定义
            "mismatch_rate_relative_drop": (
                round((base_rate - agent_rate) / base_rate, 4) if base_rate > 0 else None
            ),
            "avg_score_gain": round(
                float(agent["avg_score"]) - float(baseline["avg_score"]), 4
            )
            if agent["avg_score"] is not None and baseline["avg_score"] is not None
            else None,
            "cost_of_improvement": {
                "extra_latency_ms": round(
                    float(agent["avg_duration_ms"]) - float(baseline["avg_duration_ms"]), 1
                ),
                "avg_revisions": agent["avg_revisions"],
            },
            "note": (
                "两种配置的产物由同一个 StyleGuard 评分；"
                "若 baseline 错配率为 0，相对降幅无定义（分母为 0），此时请只看绝对降幅。"
            ),
        }
    return report


# ── 入口 ────────────────────────────────────────────────────────────────────
async def run_benchmark(
    *,
    limit: int | None = None,
    modes: tuple[str, ...] = ("baseline", "agent"),
    engine_override: str | None = None,
    write_report: bool = True,
) -> dict[str, Any]:
    if engine_override:
        settings.engine_backend = engine_override  # type: ignore[assignment]
        from app.graph import builder as builder_module

        builder_module._orchestrator = None  # 强制重新装配引擎

    cases = BENCHMARK[: limit or len(BENCHMARK)]
    results: list[dict[str, Any]] = []

    for case in cases:
        logger.info("评估样本: %s", case.case_id)
        if "baseline" in modes:
            try:
                results.append(await run_baseline_case(case))
            except Exception as exc:  # noqa: BLE001
                logger.exception("baseline 样本失败: %s", case.case_id)
                results.append(
                    {
                        "case_id": case.case_id,
                        "label": case.label,
                        "mode": "baseline",
                        "passed": False,
                        "score": 0.0,
                        "error": str(exc),
                        "revisions": 0,
                        "duration_ms": 0.0,
                        "degraded": True,
                    }
                )
        if "agent" in modes:
            try:
                results.append(await run_agent_case(case))
            except Exception as exc:  # noqa: BLE001
                logger.exception("agent 样本失败: %s", case.case_id)
                results.append(
                    {
                        "case_id": case.case_id,
                        "label": case.label,
                        "mode": "agent",
                        "passed": False,
                        "score": 0.0,
                        "error": str(exc),
                        "revisions": 0,
                        "duration_ms": 0.0,
                        "degraded": True,
                    }
                )
            finally:
                blobs.pop(case.case_id)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "engine": settings.engine_backend,
            "style_threshold": settings.style_threshold,
            "max_revisions": settings.max_revisions,
            "corpus": settings.corpus_dir,
            "models_configured": settings.has_dashscope_key,
            "image_model": settings.model_image,
            "judge_model": settings.model_judge,
            "image_model": settings.model_image,
        },
        "summary": summarize(results),
        "cases": results,
    }

    if write_report:
        settings.metrics_path.mkdir(parents=True, exist_ok=True)
        target = settings.metrics_path / "eval_report.json"
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(target)
    return report


def _print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("=" * 78)
    print("三星堆复原系统 · 风格错配率基准")
    print("=" * 78)
    print(f"配置: {json.dumps(report['config'], ensure_ascii=False)}")
    print("-" * 78)
    header = f"{'配置':<10}{'样本':>5}{'错配':>6}{'错配率':>9}{'均分':>8}{'均耗时(ms)':>12}{'均回炉':>8}"
    print(header)
    for mode in ("baseline", "agent"):
        entry = summary.get(mode)
        if not entry:
            continue
        print(
            f"{mode:<10}{entry['cases']:>5}{entry['mismatch_count']:>6}"
            f"{(entry['mismatch_rate'] or 0):>9.2%}{(entry['avg_score'] or 0):>8.3f}"
            f"{(entry['avg_duration_ms'] or 0):>12.0f}{(entry['avg_revisions'] or 0):>8.2f}"
        )
    delta = summary.get("delta")
    if delta:
        print("-" * 78)
        rel = delta["mismatch_rate_relative_drop"]
        print(
            f"错配率: {delta['mismatch_rate_baseline']:.2%} → {delta['mismatch_rate_agent']:.2%}"
            f"（绝对下降 {delta['mismatch_rate_absolute_drop']:.2%}"
            + (f"，相对下降 {rel:.2%}" if rel is not None else "，相对降幅无定义")
            + "）"
        )
        print(f"平均分提升: {delta['avg_score_gain']:+.3f}")
        print(f"代价: 平均多耗时 {delta['cost_of_improvement']['extra_latency_ms']:.0f} ms，"
              f"平均回炉 {delta['cost_of_improvement']['avg_revisions']:.2f} 次")
    if report.get("report_path"):
        print("-" * 78)
        print(f"完整报告: {report['report_path']}")
    print("=" * 78)


def main() -> None:
    parser = argparse.ArgumentParser(description="三星堆复原系统风格错配率基准")
    parser.add_argument("--limit", type=int, default=len(BENCHMARK), help="样本数量上限")
    parser.add_argument(
        "--modes", nargs="+", default=["baseline", "agent"], choices=["baseline", "agent"]
    )
    parser.add_argument("--engine", default=None, choices=["langgraph", "builtin"])
    parser.add_argument("--no-report", action="store_true", help="不写报告文件")
    args = parser.parse_args()

    report = asyncio.run(
        run_benchmark(
            limit=args.limit,
            modes=tuple(args.modes),
            engine_override=args.engine,
            write_report=not args.no_report,
        )
    )
    _print_report(report)
    metrics.persist()


if __name__ == "__main__":
    main()
