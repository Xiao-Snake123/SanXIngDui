interface ChatHistoryMessage {
  role: "user" | "assistant";
  content: string;
}

interface ChatMessagesResponse {
  answer?: string;
  conversation_id?: string;
  message_id?: string;
}

interface ChatMessagesErrorResponse {
  code?: string;
  message?: string;
  status?: number;
}

const CHAT_APP_API_KEY = import.meta.env.VITE_CHAT_APP_API_KEY || "";
const CHAT_MESSAGES_URL = CHAT_APP_API_KEY
  ? "https://sxdapi.aitrais.cn/v1/chat-messages"
  : "/dify/v1/chat-messages";
const CHAT_USER_ID = "user_001";

let conversationId = "";

const SANXINGDUI_SYSTEM_PROMPT = `你是一位专业的三星堆考古知识科普讲解员，名叫“三星堆小助手”。
你的核心任务是：
1. 回答用户关于三星堆的所有问题，包括但不限于：遗址概况、出土文物、考古发现过程、历史背景、文化解读、相关争议等。
2. 回答风格要兼顾专业性和易懂性：用通俗的语言解释专业术语，避免过于晦涩；同时保证信息准确，不编造没有考古依据的内容。
3. 当内容尚无定论时，请明确说明“目前学界尚无定论，主流观点认为……”。
4. 可以主动补充必要背景，但不要偏离用户问题本身。
5. 语气友好、耐心，像一位靠谱的科普老师。`;

function formatQuery(userQuestion: string, history: ChatHistoryMessage[]): string {
  const recentHistory = history
    .slice(-6)
    .map((message) => `${message.role === "user" ? "用户" : "助手"}：${message.content}`)
    .join("\n");

  if (!recentHistory) {
    return `${SANXINGDUI_SYSTEM_PROMPT}\n\n用户问题：${userQuestion}`;
  }

  return `${SANXINGDUI_SYSTEM_PROMPT}\n\n以下是最近对话，可用于理解上下文：\n${recentHistory}\n\n用户最新问题：${userQuestion}`;
}

export async function generateAssistantReply(userQuestion: string, history: ChatHistoryMessage[] = []): Promise<string> {
  const response = await fetch(CHAT_MESSAGES_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(CHAT_APP_API_KEY ? { Authorization: `Bearer ${CHAT_APP_API_KEY}` } : {}),
    },
    body: JSON.stringify({
      inputs: {},
      query: formatQuery(userQuestion, history),
      response_mode: "blocking",
      conversation_id: conversationId,
      user: CHAT_USER_ID,
    }),
  });

  const rawText = await response.text();
  let data: ChatMessagesResponse & ChatMessagesErrorResponse;

  try {
    data = JSON.parse(rawText) as ChatMessagesResponse & ChatMessagesErrorResponse;
  } catch {
    throw new Error(`小助手接口返回了无法解析的响应：${rawText || response.status}`);
  }

  if (!response.ok) {
    const detail = data.message || data.code || rawText || `请求失败：${response.status}`;

    if (response.status === 401) {
      throw new Error(`401 Unauthorized：请检查 CHAT_APP_API_KEY 是否有效。${detail ? ` 详情：${detail}` : ""}`);
    }

    throw new Error(detail);
  }

  if (data.conversation_id) {
    conversationId = data.conversation_id;
  }

  const answer = data.answer?.trim();
  if (!answer) {
    throw new Error("小助手接口未返回有效回复");
  }

  return answer;
}
