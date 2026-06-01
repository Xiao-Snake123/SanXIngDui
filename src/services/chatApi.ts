interface OpenWebUIMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

interface OpenWebUIResponse {
  choices?: Array<{
    message?: {
      content?: string;
    };
  }>;
}

interface OpenWebUIErrorResponse {
  detail?: string;
  message?: string;
}

const OPENWEBUI_BASE = "/openwebui";
const OPENWEBUI_MODEL = import.meta.env.VITE_OPENWEBUI_MODEL || "111";
const OPENWEBUI_TOKEN = import.meta.env.VITE_OPENWEBUI_API_KEY || "";

const SANXINGDUI_SYSTEM_PROMPT = `你是一位专业的三星堆考古知识科普讲解员，名叫“三星堆小助手”。
你的核心任务是：
1. 回答用户关于三星堆的所有问题，包括但不限于：遗址概况、出土文物、考古发现过程、历史背景、文化解读、相关争议等。
2. 回答风格要兼顾专业性和易懂性：用通俗的语言解释专业术语，避免过于晦涩；同时保证信息准确，不编造没有考古依据的内容。
3. 当内容尚无定论时，请明确说明“目前学界尚无定论，主流观点认为……”。
4. 可以主动补充必要背景，但不要偏离用户问题本身。
5. 语气友好、耐心，像一位靠谱的科普老师。`;

export async function generateAssistantReply(userQuestion: string, history: OpenWebUIMessage[] = []): Promise<string> {
  if (!OPENWEBUI_TOKEN) {
    throw new Error("未配置 Open WebUI API Key，请设置 VITE_OPENWEBUI_API_KEY");
  }

  const response = await fetch(`${OPENWEBUI_BASE}/api/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${OPENWEBUI_TOKEN}`,
    },
    body: JSON.stringify({
      model: OPENWEBUI_MODEL,
      messages: [
        { role: "system", content: SANXINGDUI_SYSTEM_PROMPT },
        ...history,
        { role: "user", content: userQuestion },
      ],
      stream: false,
    }),
  });

  if (!response.ok) {
    const rawText = await response.text();
    let detail = rawText;

    try {
      const parsed = JSON.parse(rawText) as OpenWebUIErrorResponse;
      detail = parsed.detail || parsed.message || rawText;
    } catch {
      // keep raw response text as detail
    }

    if (response.status === 401) {
      throw new Error(
        `401 Unauthorized：请检查 VITE_OPENWEBUI_API_KEY 是否有效（建议在 Open WebUI 重新生成 API Key），并确认 VITE_OPENWEBUI_MODEL 使用的是模型 ID 而非显示名称。${detail ? ` 详情：${detail}` : ""}`,
      );
    }

    throw new Error(detail || `Open WebUI 请求失败：${response.status}`);
  }

  const data = (await response.json()) as OpenWebUIResponse;
  const content = data.choices?.[0]?.message?.content?.trim();

  if (!content) {
    throw new Error("Open WebUI 未返回有效回复");
  }

  return content;
}