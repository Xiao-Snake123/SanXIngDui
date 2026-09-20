"""指标注册表。

不引入 prometheus_client，避免多一个依赖；指标以「打点 → 进程内聚合 → JSON 快照」
的形式暴露在 `GET /api/metrics`，同时周期性落盘，便于跨重启做趋势对比。

关键业务指标：
- sxd_task_total / sxd_task_failed_total
- sxd_style_mismatch_total / sxd_style_score（风格错配率的分母与分子）
- sxd_revision_total（Self-Correction 回炉次数分布）
- sxd_degraded_total{component}（降级次数 —— 判断「是不是真在用大模型」的关键探针）
- sxd_llm_latency_ms / sxd_node_latency_ms
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import defaultdict
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.metrics")


def _key(name: str, labels: dict[str, Any] | None) -> str:
    if not labels:
        return name
    parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{parts}}}"


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._gauges: dict[str, float] = {}
        self._started = time.time()

    # ── 打点 ────────────────────────────────────────────────────────────────
    def inc(self, name: str, value: float = 1.0, **labels: Any) -> None:
        with self._lock:
            self._counters[_key(name, labels or None)] += value

    def set_gauge(self, name: str, value: float, **labels: Any) -> None:
        with self._lock:
            self._gauges[_key(name, labels or None)] = value

    def observe(self, name: str, value: float, *, max_samples: int = 5000, **labels: Any) -> None:
        with self._lock:
            bucket = self._histograms[_key(name, labels or None)]
            if len(bucket) < max_samples:
                bucket.append(value)
            else:  # 采样上限后转为指数衰减，避免长跑内存增长
                bucket[int(time.time() * 1000) % max_samples] = value

    # ── 领域事件 ────────────────────────────────────────────────────────────
    def record_task_started(self, *, kind: str, task_id: str) -> None:
        self.inc("sxd_task_total", kind=kind)
        self.set_gauge("sxd_task_last_start_ts", time.time())

    def record_task_finished(self, *, kind: str, duration_ms: float, failed: bool) -> None:
        self.observe("sxd_task_duration_ms", duration_ms, kind=kind)
        if failed:
            self.inc("sxd_task_failed_total", kind=kind)

    def record_quality(
        self,
        *,
        style_score: float,
        passed: bool,
        revisions: int,
        kind: str,
    ) -> None:
        self.observe("sxd_style_score", style_score, kind=kind)
        self.observe("sxd_revision_count", float(revisions), kind=kind)
        if revisions > 0:
            self.inc("sxd_revision_total", kind=kind)
        if not passed:
            self.inc("sxd_style_mismatch_total", kind=kind)

    def record_degradation(self, *, component: str, reason: str, fallback: str) -> None:
        self.inc("sxd_degraded_total", component=component, fallback=fallback)
        logger.info("degraded", extra={"component": component, "reason": reason, "fallback": fallback})

    # ── 快照 ────────────────────────────────────────────────────────────────
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = {
                name: _summarize(values) for name, values in self._histograms.items() if values
            }
        total = counters.get("sxd_task_total", 0.0)
        mismatched = counters.get("sxd_style_mismatch_total", 0.0)
        return {
            "uptime_seconds": round(time.time() - self._started, 1),
            "counters": {k: round(v, 3) for k, v in sorted(counters.items())},
            "gauges": {k: round(v, 3) for k, v in sorted(gauges.items())},
            "histograms": histograms,
            "derived": {
                # 首轮风格错配率：质检未通过次数 / 总任务数
                "style_mismatch_rate": round(mismatched / total, 4) if total else None,
                "avg_revisions_per_task": (
                    round(counters.get("sxd_revision_total", 0.0) / total, 3) if total else None
                ),
                "degradation_ratio": (
                    round(
                        counters.get("sxd_degraded_total", 0.0)
                        / max(counters.get("sxd_llm_calls", counters.get("sxd_llm_call_total", 1.0)), 1.0),
                        4,
                    )
                    if total
                    else None
                ),
            },
        }

    def persist(self) -> None:
        try:
            settings.metrics_path.mkdir(parents=True, exist_ok=True)
            target = settings.metrics_path / "metrics_snapshot.json"
            target.write_text(
                json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("metrics persist failed: %s", exc)

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
            self._gauges.clear()


def _summarize(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    count = len(ordered)
    return {
        "count": count,
        "avg": round(sum(ordered) / count, 3),
        "p50": round(_quantile(ordered, 0.50), 3),
        "p90": round(_quantile(ordered, 0.90), 3),
        "p95": round(_quantile(ordered, 0.95), 3),
        "max": round(ordered[-1], 3),
    }


def _quantile(ordered: list[float], q: float) -> float:
    if not ordered:
        return math.nan
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


metrics = MetricsRegistry()
