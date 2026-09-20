"""对「黄金面具 + 出土语境」这条历史失败路径做定点复验。

背景（来自旧实例日志）：
    profile=photo_real goal=按选定方案「出土语境」生成：... 「黄金面具」...
    qa passed=False score=0.594 -> revise -> give_up score=0.594

原因：所有非创作型风格档案都硬要求 green>=0.10。黄金面具的画面里当然没有锈绿，
于是被判不合格，回炉时还把「补足青铜锈绿与土褐色调」注入下一轮提示词 ——
用质检把黄金主动改成了青铜。

本脚本断言修复后：
  1. 提示词里没有逐字符拆分、没有青铜材质断言
  2. 出图一次通过（不再因色域口径错误触发回炉）
  3. 质检意见与材质一致
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8123"
MESSAGE = "黄金面具在三星堆祭祀坑出土现场，考古档案照片风格"


def intent_to_request(intent: dict) -> dict:
    """对齐前端 `intentToRequest()` 的字段映射，保证测的是用户真实路径。"""
    request = {
        "kind": intent.get("kind"),
        "style": intent.get("style"),
        "item": intent.get("subject"),
        "identity": intent.get("identity"),
        "scene": intent.get("scene"),
        "artifact": intent.get("subject"),
        "method": intent.get("method"),
        "style_preset": intent.get("style_preset"),
    }
    if intent.get("strength"):
        request["strength"] = intent["strength"]
    return {key: value for key, value in request.items() if value}


async def chat_proposals(client: httpx.AsyncClient, message: str) -> tuple[str, dict, list]:
    """走一次对话，拿到 proposal「出土语境」。"""
    frames: dict[str, list] = {}
    session_id = ""
    async with client.stream("POST", f"{BASE}/api/chat/stream", json={"message": message}) as resp:
        resp.raise_for_status()
        event = None
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                frames.setdefault(event or "", []).append(payload)
                if event == "session":
                    session_id = payload.get("session_id", "")

    intent = (frames.get("intent") or [{}])[0].get("intent") or {}
    proposal_frame = (frames.get("proposals") or [{}])[0]
    proposals = proposal_frame.get("items") or []
    return session_id, intent, proposals


async def main() -> int:
    failures: list[str] = []
    async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
        session_id, intent, proposals = await chat_proposals(client, MESSAGE)
        print("[intent]", json.dumps(intent, ensure_ascii=False))
        print(f"[proposals] {len(proposals)} 个")

        target = next((item for item in proposals if item["id"] == "scene:context"), None)
        if target is None:
            failures.append("找不到 scene:context（出土语境）方案")
            print("\nHISTORICAL PATH CHECK FAIL")
            for item in failures:
                print("  -", item)
            return 1

        prompt = target["prompt"]
        print(f"\n[目标方案] {target['title']} profile={target['style_profile']}")

        # 1) 逐字符拆分
        letters = [
            part.strip() for part in prompt.split(",") if len(part.strip()) == 1 and part.strip().isalpha()
        ]
        print(f"[逐字符 token] {len(letters)} 个 {'-> ' + str(letters[:8]) if letters else '(无)'}")
        if len(letters) >= 5:
            failures.append(f"提示词出现逐字符拆分：{letters[:10]}")

        # 2) 青铜材质断言
        lowered = prompt.lower()
        for banned in ("oxidized bronze", "granular patina", "malachite", "azurite"):
            if banned in lowered:
                failures.append(f"金器提示词出现青铜断言 {banned!r}")
        if "oxidized bronze" not in lowered and "granular patina" not in lowered:
            print("[材质断言] 未出现氧化青铜/锈层 ✓")

        # 3) Surface truth 必须是金
        st = prompt.split("Surface truth:")[-1].split(". ")[0] if "Surface truth:" in prompt else "(缺失)"
        print(f"[Surface truth] {st.strip()}")
        if "gold" not in st.lower():
            failures.append(f"Surface truth 未描述黄金材质：{st!r}")

        # 4) 非视觉标签
        for banned in ("质检红线", "学术争议"):
            if banned in prompt:
                failures.append(f"提示词混入非视觉标签 {banned!r}")

        # 5) 真实出图 + 质检
        params = target["params"]
        override = {
            "prompt": prompt,
            "negative_prompt": target["negative_prompt"],
            "profile_key": target["style_profile"],
            "width": params["width"],
            "height": params["height"],
            "steps": params["steps"],
            "cfg": params["cfg"],
            "lora_strength": params["lora_strength"],
            "proposal_id": target["id"],
            "proposal_title": target["title"],
        }
        response = await client.post(
            f"{BASE}/api/restore",
            json={
                # 与前端 intentToRequest() 保持一致：真实调用会把这些槽位都带上，
                # 少传会走到「猜不出文物」的降级路径，那就不是在测用户真实路径了。
                **intent_to_request(intent),
                "session_id": session_id,
                "prompt_override": override,
            },
            timeout=300.0,
        )
        response.raise_for_status()
        data = response.json()
        qa = data.get("qa") or {}
        print(
            f"\n[出图] provider={(data.get('image') or {}).get('provider')} "
            f"profile={data['plan'].get('profile_key')}"
        )
        print(
            f"[质检] passed={qa.get('passed')} score={qa.get('score')} "
            f"revisions={data.get('revisions')}"
        )
        print(f"[色域要求] {json.dumps(qa.get('expected_families') or {}, ensure_ascii=False)}")

        feedback = qa.get("feedback") or []
        for item in feedback[:4]:
            print(f"  · {item}")
            if "锈绿" in item and "gold" in prompt.lower():
                failures.append(f"质检仍要求金器补锈绿：{item}")

        if not qa.get("passed"):
            failures.append(f"黄金面具出图未通过质检（score={qa.get('score')}）")

    if failures:
        print("\nHISTORICAL PATH CHECK FAIL")
        for item in failures:
            print("  -", item)
        return 1
    print("\nHISTORICAL PATH CHECK PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
