"""对话接口端到端探针。

验证内容：
1. /api/health 是否暴露了出图 provider 链与网络诊断
2. /api/chat/stream 的 SSE 帧协议是否完整（session/intent/evidence/delta/proposals/message/followups）
3. 多轮对话是否复用同一 session
4. 提案能否直接转成 prompt_override 并锁定出图提示词

只依赖标准库 + httpx；对 127.0.0.1 强制 trust_env=False（避免系统代理劫持本机请求）。
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8123"
TURNS = [
    "做一个大祭司站在青铜神树祭坛前的场景",
    "改成博物馆纪实摄影的感觉，稍亮一点",
]


async def collect(client: httpx.AsyncClient, message: str, session_id: str | None):
    """消费一次 SSE，返回 (session_id, {frame_type: payload})。"""
    frames: dict[str, list] = {}
    sid = session_id
    body: dict = {"message": message}
    if session_id:
        body["session_id"] = session_id

    async with client.stream("POST", f"{BASE}/api/chat/stream", json=body) as resp:
        if resp.status_code != 200:
            text = (await resp.aread()).decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {resp.status_code}: {text[:400]}")
        event = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                raw = line[5:].strip()
                if event == ": ping":
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = raw
                frames.setdefault(event or "message", []).append(payload)
                if event == "session" and isinstance(payload, dict):
                    sid = payload.get("session_id", sid)
    return sid, frames


async def main() -> int:
    failures: list[str] = []
    async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
        health = (await client.get(f"{BASE}/api/health")).json()
        print("[health] status =", health.get("status"))
        print("[health] image providers =", json.dumps(health.get("providers"), ensure_ascii=False))
        print("[health] network =", json.dumps(health.get("network"), ensure_ascii=False)[:160])

        stats = (await client.get(f"{BASE}/api/chat/stats")).json()
        print("[chat/stats] =", json.dumps(stats, ensure_ascii=False)[:200])

        session_id = None
        last_frames: dict[str, list] = {}
        for index, turn in enumerate(TURNS):
            session_id, frames = await collect(client, turn, session_id)
            last_frames = frames
            print(f"\n[turn {index + 1}] {turn}")
            print("  frames =", {k: len(v) for k, v in frames.items()})
            print("  session =", session_id)
            intent = (frames.get("intent") or [{}])[0]
            print("  intent =", json.dumps(intent, ensure_ascii=False)[:200])
            print("  evidence =", len(frames.get("evidence") or []))
            print("  delta chars =", sum(len(str(d)) for d in frames.get("delta") or []))
            print("  message =", json.dumps((frames.get("message") or [""])[0], ensure_ascii=False)[:200])

        required = ["session", "intent", "delta", "proposals", "message", "followups"]
        for key in required:
            if not last_frames.get(key):
                failures.append(f"缺少 SSE 帧: {key}")

        # proposals 帧的载荷形如 {"type": "proposals", "items": [...], "count": n}
        proposal_frame = (last_frames.get("proposals") or [{}])[0]
        proposals = proposal_frame.get("items") if isinstance(proposal_frame, dict) else proposal_frame
        if not isinstance(proposals, list) or len(proposals) < 3:
            failures.append(f"提案数量不足 3: {proposals!r}")
        else:
            print(f"\n[proposals] {len(proposals)} 个")
            for item in proposals:
                print(
                    f"  - {item.get('id')} | {item.get('title')} "
                    f"| profile={item.get('style_profile')} ({item.get('style_label')})"
                )
                if not item.get("prompt"):
                    failures.append(f"提案 {item.get('id')} 缺少 prompt")
                if not item.get("rationale"):
                    failures.append(f"提案 {item.get('id')} 缺少 rationale")
                # 风格迁移若拿到空档案，prompt 里就没有目标风格 token、质检也没有数值基准
                if item.get("style_profile") in (None, "", "default"):
                    failures.append(f"提案 {item.get('id')} 退化到空风格档案")

        # 用第一个提案锁定出图，验证 prompt_override 真的生效
        override = {
            "prompt": proposals[0]["prompt"],
            "profile_key": proposals[0].get("style_profile"),
            "width": proposals[0]["params"]["width"],
            "height": proposals[0]["params"]["height"],
            # steps/cfg/lora_strength 是扩散采样器的旋钮，API 出图不接受；
            # 现在能调的只有「风格强度」（写进提示词措辞）与尺寸。
            "style_strength": proposals[0]["params"].get("style_strength"),
            "prompt_extend": proposals[0]["params"].get("prompt_extend", False),
            "proposal_id": proposals[0]["id"],
            "proposal_title": proposals[0]["title"],
        }
        if not override["proposal_id"] or not override["proposal_title"]:
            failures.append("提案缺少 id/title，无法回填成锁定覆盖")
        if override["prompt_extend"]:
            failures.append("锁定提示词时 prompt_extend 必须为 False，否则用户选定的提示词会被模型改写")
        if override["width"] <= 0 or override["height"] <= 0:
            failures.append(f"提案尺寸非法: {override['width']}x{override['height']}")
        restore = await client.post(
            f"{BASE}/api/restore",
            json={
                "kind": intent.get("kind", "scene"),
                "prompt": TURNS[0],
                "session_id": session_id,
                "prompt_override": override,
            },
            timeout=300.0,
        )
        if restore.status_code != 200:
            failures.append(f"restore HTTP {restore.status_code}: {restore.text[:300]}")
        else:
            data = restore.json()
            issued = (data.get("image") or {}).get("prompt") or ""
            history = data.get("revision_history") or []
            print("\n[restore] provider =", (data.get("image") or {}).get("provider"))
            print("[restore] profile =", data["plan"].get("profile_key"))
            print("[restore] 首轮 prompt head =", (history[0]["prompt"] if history else "")[:140])
            print("[restore] 最终 prompt head =", issued[:140].replace("\n", " "))
            print("[restore] qa passed =", (data.get("qa") or {}).get("passed"))

            # 首轮必须逐字等于用户选定的提示词（回炉轮次才允许追加修正指令）
            if not history:
                failures.append("revision_history 为空，无法验证提示词锁定")
            elif history[0]["prompt"].strip() != override["prompt"].strip():
                failures.append("首轮出图 prompt 与用户选定提示词不一致 —— 锁定失效")
            if data["plan"].get("profile_key") != override["profile_key"]:
                failures.append("出图使用的风格档案与提案不一致")

        final_stats = (await client.get(f"{BASE}/api/chat/stats")).json()
        print("\n[chat/stats after] =", json.dumps(final_stats, ensure_ascii=False)[:200])

    if failures:
        print("\nCHAT PROBE FAIL")
        for item in failures:
            print("  -", item)
        return 1
    print("\nCHAT PROBE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
