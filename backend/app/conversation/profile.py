"""用户画像：从对话里抽取长期事实，并格式化成可注入的提示词。

为什么抽取交给 LLM 而不是关键词表
----------------------------------
「我叫肖晟」「记住，我喜欢写实风格」「我是做考古的」——这类表达没有穷举边界，
用关键词去匹配就是在堆一个永远补不完的列表（本项目已经删掉这类闸门，
理由见 app/conversation/router.py 的 docstring）。

判断「这句话里有没有值得记住的个人信息」正是 LLM 擅长的事，
所以用一次轻量调用（temperature=0、约 200 token）做抽取即可。

为什么只在对话流里抽取
----------------------
`/api/ask` 是无状态单轮问答端点（契约如此），不该有「记住用户」的副作用；
长期记忆只发生在多轮对话（`/api/chat/stream`）里。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.models.registry import registry

logger = get_logger("app.conversation.profile")

EXTRACT_SYSTEM = """你是用户信息抽取器。判断用户这句话里有没有**主动陈述的、值得长期记住**的个人事实。

只输出 JSON：
{"facts":[{"key":"name","value":"肖晟"}]}

规则：
1. 只抽用户**主动陈述自己**的稳定事实：名字 / 称呼、职业身份、稳定的偏好。
   例：「我叫肖晟」「记住，我喜欢写实的风格」「我是做考古的」。
2. 不抽：一次性的请求内容（「帮我复原青铜面具」里的文物不是用户事实）、
   助手说过的话、疑问句、猜测、他人的信息、临时上下文。
3. key 用简短英文（name / occupation / preference_style / preference_topic），
   value 只写具体值，不要把整句话抄进来。
4. 没有就返回空数组 []。

只输出 JSON，不要解释。"""


async def extract_facts(user_message: str) -> list[dict[str, str]]:
    """从用户这句话里抽取长期事实。

    无 Key / 调用失败时返回空列表——记不住只是降级，不该让本轮对话失败。
    """
    text = (user_message or "").strip()
    if not text or not registry.enabled:
        return []
    try:
        payload, _ = await registry.call_json(
            "qa",
            [
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": text},
            ],
            required_keys=("facts",),
            temperature=0.0,
            max_tokens=200,
            tag="fact_extract",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("用户事实抽取失败（不影响本轮对话）: %s", exc)
        return []

    raw = payload.get("facts")
    if not isinstance(raw, list):
        return []
    facts: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        value = str(item.get("value") or "").strip()
        if key and value:
            facts.append({"key": key[:64], "value": value})
    return facts


def format_profile(facts: list[dict[str, Any]] | None) -> str:
    """把画像事实格式化成一段可注入提示词的文本；没有事实返回空串。"""
    if not facts:
        return ""
    lines = [
        f"- {item.get('key')}：{item.get('value')}"
        for item in facts
        if item.get("value")
    ]
    if not lines:
        return ""
    return "【关于这位用户的已知信息（跨会话长期记忆）】\n" + "\n".join(lines)
