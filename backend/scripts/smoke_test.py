"""端到端冒烟脚本：不依赖任何 API Key 也能验证整条链路是否跑通。

用法：
    cd backend
    python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import setup_logging  # noqa: E402
from app.graph.builder import build_orchestrator, engine_report  # noqa: E402
from app.graph.runner import RestorationRunner  # noqa: E402
from app.rag.store import get_retriever  # noqa: E402


async def main() -> int:
    setup_logging()
    print("=" * 70)
    print("1) 引擎装配")
    orchestrator = build_orchestrator()
    print("   engine =", orchestrator.name, "| report =", json.dumps(engine_report(), ensure_ascii=False)[:160])

    print("2) 语料装载与索引")
    retriever = await get_retriever()
    stats = retriever.stats()
    print("   corpus =", stats["corpus_size"], "| files =", stats["corpus_files"], "| embedder =", stats["embedder"])

    print("3) 单查询检索")
    chunks, diag = await retriever.search("青铜纵目面具 形制 柱状凸目", top_n=3)
    print("   hits =", len(chunks), "| diag =", json.dumps(diag.to_dict(), ensure_ascii=False))
    for chunk in chunks:
        print(f"   - {chunk.title}  final={chunk.final_score:.3f}  channels={chunk.channels}")

    print("4) 多查询检索（Planner 视角）")
    multi, diag_multi = await retriever.search_multi(
        ["青铜大立人 形制", "大祭司 服饰", "三星堆 时代错配 禁忌"], top_n=4
    )
    print("   merged =", len(multi))
    for chunk in multi:
        print(f"   - {chunk.title}  final={chunk.final_score:.3f}")

    print("5) 端到端跑一次复原任务（无 Key 时走降级链）")
    runner = RestorationRunner(
        {
            "kind": "scene",
            "identity": "大祭司",
            "scene": "博物馆展厅",
            "item": "青铜纵目面具",
            "style": "博物馆纪实摄影",
            "seed": 20260916,
        }
    )
    events: list[str] = []
    async for event in runner.astream():
        kind = event.get("type")
        if kind == "node":
            events.append(f"{event['node']}({event.get('progress')}%)")
            print(f"   [{event.get('progress'):>3}%] {event.get('label')}  next={event.get('next')}")
        elif kind == "error":
            print("   ERROR:", event.get("message"))
    print("   节点序列:", " → ".join(events))

    result = runner._final or {}
    print("6) 结果摘要")
    print("   task_id      =", result.get("task_id"))
    print("   duration_ms  =", result.get("duration_ms"))
    print("   image_url    =", (result.get("image_url") or "")[:80])
    print("   provider     =", result.get("image_provider"), "| degraded =", result.get("image_degraded"))
    print("   evidence     =", result.get("evidence_count"))
    qa = result.get("qa") or {}
    print(
        "   qa           = passed:%s score:%s objective:%s judge:%s decision:%s"
        % (qa.get("passed"), qa.get("score"), qa.get("objective_score"), qa.get("judge_score"), qa.get("decision"))
    )
    print("   revisions    =", result.get("revisions"))
    print("   degraded     =", result.get("degraded_components"))
    copy_payload = result.get("copy") or {}
    print("   copy mode    =", copy_payload.get("mode"), "| length =", copy_payload.get("length"))
    print("   copy preview =", (copy_payload.get("text") or "")[:100].replace("\n", " "))
    print("   errors       =", result.get("errors"))
    print("=" * 70)

    ok = bool(result.get("image_url")) and not (result.get("errors") or [])
    print("SMOKE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
