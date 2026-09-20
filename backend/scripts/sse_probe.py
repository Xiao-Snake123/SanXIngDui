"""SSE 端点冒烟验证：真实按帧消费 /api/restore/stream，检查帧格式与事件序列。

用法（先启动后端）：
    python backend/scripts/sse_probe.py --base http://127.0.0.1:8123
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter

import httpx


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8123")
    parser.add_argument("--kind", default="scene")
    args = parser.parse_args()

    payload = {
        "kind": args.kind,
        "identity": "大祭司",
        "scene": "博物馆展厅",
        "item": "青铜纵目面具",
        "style": "博物馆纪实摄影",
        "seed": 20260916,
    }

    print(f"POST {args.base}/api/restore/stream")
    counts: Counter[str] = Counter()
    nodes: list[str] = []
    trace_kinds: list[str] = []
    event_names: list[str] = []
    result: dict | None = None
    error: str | None = None

    timeout = httpx.Timeout(180.0, connect=10.0)
    # trust_env=False：本机服务不能被系统代理（Clash 等）接管，否则会拿到 502
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        async with client.stream(
            "POST",
            f"{args.base}/api/restore/stream",
            json=payload,
            headers={"Accept": "text/event-stream", "Accept-Encoding": "identity"},
        ) as response:
            print("HTTP", response.status_code, response.headers.get("content-type"))
            if response.status_code != 200:
                print(await response.aread())
                return 1

            event_name = "message"
            data_lines: list[str] = []

            async for line in response.aiter_lines():
                if line == "":
                    if data_lines:
                        raw = "\n".join(data_lines)
                        counts[event_name] += 1
                        event_names.append(event_name)
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            data = {}
                        if event_name == "node":
                            nodes.append(f"{data.get('node')}@{data.get('progress')}%")
                        elif event_name == "trace":
                            trace_kinds.append(str(data.get("kind")))
                        elif event_name == "done":
                            result = data.get("result")
                        elif event_name == "error":
                            error = data.get("message")
                    event_name = "message"
                    data_lines = []
                    continue
                if line.startswith(":"):
                    counts["ping"] += 1
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())

    print("-" * 78)
    print("帧类型统计 :", dict(counts))
    print("节点序列   :", " -> ".join(nodes))
    print("trace 类型 :", dict(Counter(trace_kinds)))
    print("-" * 78)

    if error:
        print("ERROR:", error)
        return 1
    if not result:
        print("未收到 done 帧")
        return 1

    qa = result.get("qa") or {}
    print("goal        :", result.get("goal"))
    print("engine      :", result.get("engine"))
    print("duration_ms :", result.get("duration_ms"))
    print("image_url   :", result.get("image_url"))
    print("provider    :", result.get("image_provider"), "degraded:", result.get("image_degraded"))
    print("evidence    :", len(result.get("evidence") or []))
    print("qa          : passed=%s score=%s obj=%s judge=%s decision=%s" % (
        qa.get("passed"), qa.get("score"), qa.get("objective_score"), qa.get("judge_score"), qa.get("decision")))
    print("revisions   :", result.get("revisions"))
    print("degraded    :", result.get("degraded_components"))
    print("copy len    :", len((result.get("copy") or {}).get("text") or ""))
    print("usage       :", result.get("usage"))

    expected = {"start", "node", "trace", "done"}
    missing = expected - set(event_names)
    ok = not missing and bool(result.get("image_url"))
    print("-" * 78)
    print("缺失帧类型 :", missing or "无")
    print("SSE PROBE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
