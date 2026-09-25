"""意图路由：用一次轻量 LLM 调用判定消息意图，取代关键词匹配。

为什么要干掉关键词表（classify_smalltalk / _looks_like_question 那套）：
- 关键词是无限维护的屎山——今天补「你在干什么」，明天「你干啥呢」又漏；
- 意图判断正是 LLM 最擅长的事，而我们每轮本来就要调模型，没必要用规则抢跑；
- 只在无 Key / 模型失败时才退化为极简启发式（降级路径，不是常态）。

三态：chat（闲聊/问候/身份/致谢）| question（史实提问）| creation（复原/出图/改方案）。
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.models.registry import registry

logger = get_logger("app.conversation.router")

SYSTEM = """你是三星堆对话系统的意图路由器。判断用户这条消息属于哪一类，只输出 JSON：

{"intent": "chat" | "question" | "creation"}

- "chat"：问候、闲聊、问助手是谁/能做什么、致谢、无实质内容的寒暄，
  **以及要求改变助手说话方式/语言风格的话**。
  例：「你好」「你是谁」「谢谢」「用普通话说」「改成现代话」「说人话」「别文绉绉」。
- "question"：关于三星堆文物/历史/考古的事实性提问，或任何想从语料找答案的问题。
  例：「纵目面具为什么眼睛外凸」「青铜神树有多高」。
- "creation"：想复原/修复文物、生成图像、做风格迁移、改方案、描述一个要出图的场景。
  例：「帮我修复青铜面具」「金杖在博物馆展厅里」「换个角度」（在已有方案时）。

判定要点（按序）：
1. **先读【最近对话】再判**。同一句话换上下文意思就变了：
   「改成现代话」——前文在聊助手怎么说话，就是 chat；前文已经出了方案/图，才是 creation。
2. 「改 / 换成 / 加上」这类词**本身不等于 creation**，要看有没有可改的对象。
   【可改对象】为「无」时，孤零零一句「改成X」多半是在改助手的说法，归 chat
   （若听着像在追问事实则归 question）。
3. 出现明确的创作/出图线索（复原、修复、生成、风格、场景、角度、海报、视频、
   光位、构图、迁移、出图、画、绘）归 creation；
4. 纯问候/身份/致谢/闲聊归 chat；其余涉及事实提问的归 question。
只输出 JSON，不要解释。"""

# 模型不可用时的极简降级（不是常态路径）。
_CREATION_HINTS = ("复原", "修复", "生成", "风格", "场景", "角度", "海报", "视频",
                   "改", "换成", "加上", "光位", "构图", "迁移", "出图", "画", "绘")
# 「改 / 换成 / 加上」单独出现时**不足以**判定为创作：它们可能是在改助手的说法。
# 只有在【可改对象】存在（会话里已有方案/图）时才算创作线索。
_MODIFY_ONLY = ("改", "换成", "加上")
_QUESTION_WORDS = ("吗", "？", "?", "为什么", "怎么", "如何", "什么", "多少", "几", "哪", "谁", "介绍", "讲讲")


def creation_anchor_present(text: str) -> bool:
    """只看字面：有没有**不含**「改/换成/加上」的创作线索。

    给上层做兜底闸门用——「改成现代话」里有「改」，但它改的不是画面。
    """
    return any(hint in text for hint in _CREATION_HINTS if hint not in _MODIFY_ONLY)


def _heuristic(text: str, *, has_proposals: bool = False) -> str:
    hits = [hint for hint in _CREATION_HINTS if hint in text]
    # 没有任何可改对象时，光一个「改成X」不该判成创作——
    # 降级路径也要守住这条，否则误判会一路连锁到「凭空编个主体出来」。
    if hits and not (not has_proposals and set(hits) <= set(_MODIFY_ONLY)):
        return "creation"
    if text.endswith(("？", "?")) or any(w in text for w in _QUESTION_WORDS):
        return "question"
    return "chat"


def _context(text: str, history: list[dict[str, str]] | None, has_proposals: bool) -> str:
    """把「上下文」和「有没有可改的东西」一起交给路由器。

    这两个信息是判对的关键：路由器只看当前这一句时，「改成现代话」和
    「换个角度」长得一模一样，只能靠上下文区分。
    """
    parts: list[str] = []
    if history:
        tail = "\n".join(
            f"{turn.get('role')}: {str(turn.get('content') or '')[:120]}"
            for turn in history[-4:]
        )
        parts.append(f"【最近对话】\n{tail}")
    parts.append(
        "【可改对象】"
        + ("有（会话里已有方案/图，可以改）" if has_proposals else "无（会话里还没有任何方案或图）")
    )
    parts.append(f"【用户本轮】{text}")
    return "\n".join(parts)


async def classify_intent(
    message: str,
    *,
    history: list[dict[str, str]] | None = None,
    has_proposals: bool = False,
) -> str:
    """返回 'chat' | 'question' | 'creation'。

    history 为会话历史（不含当前轮），has_proposals 表示会话里是否已有方案/图。
    两者都只影响判定，不改变返回值语义。
    """
    text = (message or "").strip()
    if not text:
        return "chat"
    if not registry.enabled:
        return _heuristic(text, has_proposals=has_proposals)
    try:
        payload, _ = await registry.call_json(
            "qa",
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": _context(text, history, has_proposals)},
            ],
            required_keys=("intent",),
            temperature=0.0,
            max_tokens=32,
            tag="intent",
        )
        intent = str(payload.get("intent") or "").strip().lower()
        if intent in {"chat", "question", "creation"}:
            return intent
        return _heuristic(text, has_proposals=has_proposals)
    except Exception as exc:  # noqa: BLE001
        logger.warning("意图路由失败，退化为启发式: %s", exc)
        return _heuristic(text, has_proposals=has_proposals)
