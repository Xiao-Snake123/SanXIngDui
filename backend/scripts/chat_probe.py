"""对 /api/chat/stream 做分类压测：100 个问题，5 大类，逐题判定。

用法（backend 目录下）:
    python scripts/chat_probe.py            # 跑全部 100 题
    python scripts/chat_probe.py --fast     # 只跑闲聊+问答（不调生图链路）

输出:
    - 控制台：分类统计 + 失败清单（含回复摘要）
    - backend/var/chat_probe_report.json：逐题明细
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8123"
STREAM = f"{BASE}/api/chat/stream"
CONCURRENCY = 6
TIMEOUT = 180.0

# ────────────────────────────── 用例定义 ──────────────────────────────
# expect: smalltalk / qa / creation / revision / robust
# answerable 仅对 qa 有意义：True 要求有引用；False 要求诚实拒答。

CASES: list[dict] = []


def add(category: str, message: str, expect: str, *, answerable: bool | None = None,
        followup: str | None = None, note: str = "") -> None:
    CASES.append({
        "id": len(CASES) + 1,
        "category": category,
        "message": message,
        "expect": expect,
        "answerable": answerable,
        "followup": followup,
        "note": note,
    })


# ── 1) 闲聊 / 身份 / 问候 / 致谢 / 聊天请求（22）─────────────────────
for m in ["你好", "您好呀", "在吗", "你是谁", "你是谁呀", "你叫什么名字", "自我介绍一下",
          "你是AI吗", "你是机器人吗", "你能做什么", "你会什么", "你能帮我做什么",
          "怎么使用你", "谢谢", "多谢啦", "辛苦了", "晚上好", "早上好", "hello"]:
    add("闲聊", m, "smalltalk")
add("闲聊", "跟我来聊天可以吗", "smalltalk", note="截图中的实际案例")
add("闲聊", "陪我聊聊天", "smalltalk")
add("闲聊", "你喜欢聊天吗", "smalltalk")

# ── 2) 领域问答（28：语料可答 24 + 超纲应拒答 4... 共 28）────────────
_QA = [
    ("纵目面具的眼睛为什么外凸", True),
    ("三星堆是什么时候发现的", True),
    ("青铜神树有多高", True),
    ("金杖上刻的是什么图案", True),
    ("三星堆遗址在哪个省", True),
    ("祭祀坑是哪一年发现的", True),
    ("青铜大立人手里原本拿的什么", True),
    ("三星堆的名字是怎么来的", True),
    ("为什么三星堆的青铜器造型这么奇特", True),
    ("三星堆和中原青铜文明有什么关系", True),
    ("黄金面具是怎么被发现的", True),
    ("三星堆出土了多少件文物", True),
    ("什么是纵目面具", True),
    ("介绍一下青铜神树", True),
    ("谁主持发掘了三星堆", True),
    ("三星堆是外星人建造的吗", False),
    ("三星堆文字被破译了吗", True),
    ("遗址里有没有发现城墙", True),
    ("三星堆的碳十四测年结果是什么", True),
    ("金沙遗址和三星堆什么关系", True),
    ("纵目面具出土于几号坑", True),
    ("青铜神树上有几只鸟", True),
    ("太阳轮是什么形状", True),
    ("三星堆为什么被称为20世纪伟大考古发现之一", True),
]
for q, a in _QA:
    add("问答", q, "qa", answerable=a)
add("问答", "今天成都天气怎么样", "qa", answerable=False, note="超纲")
add("问答", "你会做红烧肉吗", "qa", answerable=False, note="超纲")
add("问答", "三星堆博物馆门票多少钱", "qa", answerable=False, note="超纲")
add("问答", "北京有多少人口", "qa", answerable=False, note="超纲")

# ── 3) 创作请求（28）─────────────────────────────────────────────────
for m in [
    "黄金面具在三星堆祭祀坑出土现场，考古档案照片风格",
    "帮我修复一件破损青铜面具",
    "复原祭祀坑发掘现场",
    "画一张青铜神树",
    "大祭司在神树祭坛前的场景，博物馆纪实摄影",
    "把纵目面具放到博物馆展柜里，射灯打光",
    "青铜大立人特写，低角度仰拍",
    "金杖纹样海报，深色背景",
    "太阳轮做成金属徽章的样子",
    "陶三足炊具在古蜀先民灶台上的场景",
    "复原一尊完整纵目面具的正面像",
    "祭祀坑剖面图，考古线描风格",
    "青铜人头像并排陈列在库房里",
    "帮我把这张面具还原成崭新的样子",
    "出一张三星堆主题文创海报",
    "古蜀国王举行祭祀的宏大场面",
    "神树上栖息的九只鸟，夜色",
    "风格迁移：把面具变成水墨画风格",
    "象牙制品在祭祀坑里堆叠的样子",
    "复原青铜尊的原始形制",
    "用赛博朋克风格表现青铜面具",
    "大立人与神树同框，仰视构图",
    "玉璋的纹饰细节特写",
    "复原古蜀先民的聚落生活场景",
    "海报：三星堆考古百年",
    "修复黄金面具的细节过程",
    "面具与新出土的青铜器合影，档案照",
    "一个古蜀祭司戴着面具起舞的画面",
]:
    add("创作", m, "creation")

# ── 4) 修改指令（8，两轮）────────────────────────────────────────────
for first, second in [
    ("大祭司在神树祭坛前的场景", "能不能再暗一点？"),
    ("黄金面具特写", "背景换成纯黑"),
    ("复原祭祀坑现场", "视角改成俯拍"),
    ("青铜神树海报", "标题改成沉睡三千年"),
    ("画一张纵目面具", "放大面具占比"),
    ("祭祀坑场景", "加上飘散的尘土"),
    ("金杖纹样", "线条再锐利一点"),
    ("博物馆展柜里的面具", "灯光冷一点"),
]:
    add("修改", first, "revision", followup=second)

# ── 5) 边界 / 混合（14）─────────────────────────────────────────────
add("边界", "你好，帮我修复一件破损青铜面具", "creation", note="问候+创作")
add("边界", "restore the golden mask at the sacrifice pit, archival photo", "creation", note="英文创作")
add("边界", "复原", "creation", note="单动词")
add("边界", "在吗？帮我复原青铜神树", "creation", note="问候+创作")
add("边界", "三星堆是什么？顺便帮我画一张金面具", "creation", note="问答+创作混合")
add("边界", "谢谢，那把神树画成剪影吧", "creation", note="致谢+创作")
add("边界", "修复面具", "creation", note="简短创作")
add("边界", "我是谁", "smalltalk", note="身份闲聊变体")
add("边界", "？？?", "robust", note="纯标点")
add("边界", "asdfjkl", "robust", note="乱码")
add("边界", "🙂🙂🙂", "robust", note="emoji")
add("边界", "<script>alert(1)</script>", "robust", note="注入尝试")
add("边界", "'; DROP TABLE users; --", "robust", note="注入尝试")
add("边界", "我想复原一个场景就是那种很多青铜器堆积在一起然后有祭司在旁边站着天空是黄昏的光线照下来,"
            "整体氛围神秘庄重,最好有点烟雾缭绕的感觉,镜头从低角度往上看,面具的细节要清晰,风格类似国家地理的纪实摄影",
    "creation", note="长句创作")

# ────────────────────────────── SSE 客户端 ──────────────────────────────


async def chat_turn(client: httpx.AsyncClient, message: str,
                    session_id: str | None) -> tuple[list[tuple[str, dict]], str | None, str | None]:
    """发一轮消息，返回 (帧列表, session_id, 错误)。"""
    frames: list[tuple[str, dict]] = []
    payload: dict = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    sid = session_id
    try:
        async with client.stream("POST", STREAM, json=payload,
                                 headers={"Accept": "text/event-stream"},
                                 timeout=TIMEOUT) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")[:300]
                return frames, sid, f"HTTP {resp.status_code}: {body}"
            event = "message"
            data_lines: list[str] = []
            async for line in resp.aiter_lines():
                if line == "":
                    if data_lines:
                        try:
                            data = json.loads("\n".join(data_lines))
                        except json.JSONDecodeError:
                            data = {"_raw": "\n".join(data_lines)}
                        frames.append((event, data))
                        if event == "session":
                            sid = data.get("session_id", sid)
                    event, data_lines = "message", []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            if data_lines:
                try:
                    frames.append((event, json.loads("\n".join(data_lines))))
                except json.JSONDecodeError:
                    pass
        return frames, sid, None
    except Exception as exc:  # noqa: BLE001
        return frames, sid, f"{type(exc).__name__}: {exc}"


def _frame(frames, name):
    return next((p for n, p in frames if n == name), None)


def judge(case: dict, frames: list, err: str | None) -> tuple[bool, str]:
    """返回 (是否通过, 原因)。"""
    if err:
        return False, f"请求失败: {err}"
    names = {n for n, _ in frames}
    msg = _frame(frames, "message")
    reply = str((msg or {}).get("text") or "")
    decision = _frame(frames, "decision") or {}
    mode = decision.get("mode")
    proposals = (_frame(frames, "proposals") or {}).get("items") or []
    evidence = (_frame(frames, "evidence") or {}).get("items") or []

    if not reply and "message" not in names:
        return False, f"无回复帧（frames={sorted(names)}）"

    exp = case["expect"]
    if exp == "smalltalk":
        if mode != "smalltalk":
            return False, f"期望闲聊回复，实际 mode={mode}，回复:「{reply[:60]}」"
        if proposals:
            return False, "闲聊不应出方案"
        if "intent" in names:
            return False, "闲聊不应进入意图解析"
        return True, f"mode={mode}"

    if exp == "qa":
        if mode != "qa":
            return False, f"期望走问答，实际 mode={mode}，回复:「{reply[:60]}」"
        if proposals:
            return False, "问答不应出方案"
        if case.get("answerable"):
            if not evidence:
                return False, f"语料应可答但无引用，回复:「{reply[:80]}」"
            return True, f"引用 {len(evidence)} 条"
        if "没有找到可靠记载" in reply or "换个说法" in reply:
            return True, "诚实拒答"
        return False, f"超纲问题未明确拒答，回复:「{reply[:80]}」"

    if exp == "creation":
        if mode in ("smalltalk", "qa"):
            return False, f"创作请求被误判为 mode={mode}，回复:「{reply[:60]}」"
        if not proposals:
            return False, f"创作请求未出方案，回复:「{reply[:60]}」"
        return True, f"{len(proposals)} 个方案"

    if exp == "revision":
        # 第二轮：绝不能被送去问答
        if mode == "qa":
            return False, f"修改指令被误送问答:「{reply[:60]}」"
        if not proposals:
            return False, f"修改指令未产出新方案，回复:「{reply[:60]}」"
        return True, "修改进入创作管线"

    # robust：不 5xx、有回复即可
    return True, f"mode={mode}，回复 {len(reply)} 字"


# ────────────────────────────── 主流程 ──────────────────────────────


async def run_case(case: dict, client: httpx.AsyncClient, sem: asyncio.Semaphore,
                   fast: bool) -> dict:
    if fast and case["expect"] in ("creation", "revision"):
        return {**case, "skip": True}
    async with sem:
        t0 = time.perf_counter()
        result: dict = {**case}
        if case["expect"] == "revision":
            f1, sid, e1 = await chat_turn(client, case["message"], None)
            if e1 or not sid:
                result.update(passed=False, reason=f"第一轮失败: {e1 or '无 session_id'}",
                              latency=round(time.perf_counter() - t0, 1))
                return result
            f2, _, e2 = await chat_turn(client, case["followup"], sid)
            ok, why = judge(case, f2, e2)
            result.update(turns=2, first_round_proposals=len((_frame(f1, 'proposals') or {}).get('items') or []))
            frames, err = f2, e2
        else:
            frames, _, err = await chat_turn(client, case["message"], None)
            ok, why = judge(case, frames, err)

        names = {n for n, _ in frames}
        msg = _frame(frames, "message") or {}
        result.update(
            passed=ok,
            reason=why,
            mode=(_frame(frames, "decision") or {}).get("mode"),
            reply=str(msg.get("text") or "")[:200],
            has_intent="intent" in names,
            proposals=len((_frame(frames, "proposals") or {}).get("items") or []),
            citations=len(((_frame(frames, "evidence") or {}).get("items") or [])),
            latency=round(time.perf_counter() - t0, 1),
        )
        return result


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="跳过创作/修改（不触发生图链路）")
    args = parser.parse_args()

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient() as client:
        # 健康预检
        try:
            r = await client.get(f"{BASE}/api/health", timeout=5)
            print(f"[probe] backend health: {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            print(f"[probe] backend unreachable: {exc}")
            return 2

        t0 = time.perf_counter()
        results = await asyncio.gather(*[run_case(c, client, sem, args.fast) for c in CASES])
    elapsed = time.perf_counter() - t0

    # ── 汇总 ──
    categories: dict[str, list[dict]] = {}
    for r in results:
        categories.setdefault(r["category"], []).append(r)

    print(f"\n{'=' * 64}\n  100 题压测报告  ({elapsed:.0f}s)\n{'=' * 64}")
    total_fail = 0
    for cat, items in categories.items():
        ran = [i for i in items if not i.get("skip")]
        passed = [i for i in ran if i["passed"]]
        failed = [i for i in ran if not i["passed"]]
        total_fail += len(failed)
        lat = [i["latency"] for i in ran if i.get("latency") is not None]
        avg = sum(lat) / len(lat) if lat else 0
        print(f"\n[{cat}]  {len(passed)}/{len(ran)} 通过   平均 {avg:.1f}s")
        for f in failed:
            print(f"  ✗ #{f['id']:>3} 「{f['message'][:28]}」{('('+f['note']+')') if f.get('note') else ''}")
            print(f"      {f['reason']}")

    skips = [r for r in results if r.get("skip")]
    print(f"\n{'-' * 64}")
    print(f"总计: {len(results) - len(skips) - total_fail}/{len(results) - len(skips)} 通过"
          + (f"（跳过 {len(skips)} 题 --fast）" if skips else ""))
    if total_fail:
        print("全部失败明细已列出，请逐条排查。")

    out = Path(__file__).parent.parent / "var" / "chat_probe_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"明细: {out}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
