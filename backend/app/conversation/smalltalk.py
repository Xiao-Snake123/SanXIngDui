"""对话流里给用户的「下一步建议」示例（前端渲染成可点击建议）。

原来的关键词闲聊分类器（classify_smalltalk / _PATTERNS / _REPLIES）已删除 ——
意图判断统一交给 `app.conversation.router.classify_intent`（一次轻量 LLM 调用），
不再维护会无限膨胀的关键词表。
"""

# 闲聊 / 问答后给用户的引导建议（前端渲染成可点击的建议）
SMALLTALK_EXAMPLES: tuple[str, ...] = (
    "黄金面具在三星堆祭祀坑出土现场，考古档案照片风格",
    "帮我修复这件破损青铜面具",
    "纵目面具的眼睛为什么外凸",
)
