import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  resetChatSession,
  streamChat,
  type ChatIntent,
  type ChatProposal,
  type EvidenceChunk,
  type TraceEvent,
} from "@/services/agentApi";

export interface ChatMessageView {
  id: string;
  role: "user" | "assistant";
  content: string;
  streaming: boolean;
  proposals: ChatProposal[];
  evidence: EvidenceChunk[];
  intent: ChatIntent | null;
  followups: string[];
  mode: string;
}

/** 把 trace 事件翻译成人能看懂的一句话，替代「转圈圈」。 */
function describeTrace(event: TraceEvent): string | null {
  switch (event.kind) {
    case "node_start":
      return "正在理解你的需求…";
    case "conversation_grounded": {
      const hits = Number(event.payload?.hits ?? 0);
      return hits > 0 ? `已检索到 ${hits} 条史料依据` : "未检索到直接相关的史料";
    }
    case "llm_call": {
      const tag = String(event.payload?.tag ?? "");
      if (tag.includes("chat_reply")) return "正在组织回复…";
      if (tag.includes("chat_proposals")) return "正在撰写提示词方案…";
      if (tag.includes("chat_intent")) return "正在解析意图…";
      return "调用模型中…";
    }
    case "degraded":
      return `已降级：${String(event.payload?.fallback ?? "")}`;
    case "chat_turn_done":
      return "";
    default:
      return null;
  }
}

let sequence = 0;

function nextId(prefix: string): string {
  sequence += 1;
  return `${prefix}-${sequence}`;
}

/**
 * 驱动「GPT 式」复原对话。
 *
 * 设计取舍：所有内容都由服务端事件驱动，不在前端编造任何文本。
 * 流式回复会先出现一个空的助手气泡，`delta` 事件逐字填充 ——
 * 这与「等服务端返回完整结果再一次性显示」在体感上差别很大，
 * 而 GPT 式交互的核心体验恰恰就在这个「逐字出现」上。
 */
export function useAgentChat() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageView[]>([]);
  const [sending, setSending] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const streamingIdRef = useRef<string | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const patchStreaming = useCallback((patch: Partial<ChatMessageView>) => {
    const id = streamingIdRef.current;
    if (!id) return;
    setMessages((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }, []);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const assistantId = nextId("assistant");
      streamingIdRef.current = assistantId;
      setError(null);
      setSending(true);
      setStage("正在理解你的需求…");

      setMessages((prev) => [
        ...prev,
        {
          id: nextId("user"),
          role: "user",
          content: trimmed,
          streaming: false,
          proposals: [],
          evidence: [],
          intent: null,
          followups: [],
          mode: "",
        },
        {
          id: assistantId,
          role: "assistant",
          content: "",
          streaming: true,
          proposals: [],
          evidence: [],
          intent: null,
          followups: [],
          mode: "",
        },
      ]);

      try {
        await streamChat(
          trimmed,
          sessionId,
          {
            onSession: (payload) => setSessionId(payload.session_id),
            onTrace: (event) => {
              const label = describeTrace(event);
              if (label !== null) setStage(label);
            },
            onIntent: (payload) =>
              patchStreaming({ intent: payload.intent, mode: payload.mode }),
            onEvidence: (payload) => patchStreaming({ evidence: payload.items }),
            onDelta: (delta) =>
              setMessages((prev) =>
                prev.map((item) =>
                  item.id === assistantId ? { ...item, content: item.content + delta } : item,
                ),
              ),
            onProposals: (payload) => patchStreaming({ proposals: payload.items }),
            // 服务端下发的完整文本是权威版本（与逐字拼接的结果一致），
            // 但它可能为空字符串（纯降级路径），此时不能把已经流式拼好的内容覆盖掉
            onMessage: (payload) =>
              patchStreaming(
                payload.text ? { content: payload.text, mode: payload.mode } : { mode: payload.mode },
              ),
            onFollowups: (items) => patchStreaming({ followups: items }),
            onError: (message) => {
              setError(message);
              patchStreaming({ content: `⚠️ ${message}`, streaming: false });
            },
          },
          controller.signal,
        );
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setError((err as Error).message);
          patchStreaming({ content: `⚠️ ${(err as Error).message}`, streaming: false });
        }
      } finally {
        setMessages((prev) =>
          prev.map((item) => (item.id === assistantId ? { ...item, streaming: false } : item)),
        );
        streamingIdRef.current = null;
        setSending(false);
        setStage("");
      }
    },
    [patchStreaming, sending, sessionId],
  );

  const reset = useCallback(async () => {
    abortRef.current?.abort();
    const current = sessionId;
    setMessages([]);
    setSessionId(null);
    setError(null);
    setStage("");
    if (current) await resetChatSession(current);
  }, [sessionId]);

  const derived = useMemo(() => {
    const lastAssistant = [...messages].reverse().find((item) => item.role === "assistant");
    const allProposals = messages.flatMap((item) => item.proposals);
    return {
      lastIntent: lastAssistant?.intent ?? null,
      lastProposals: lastAssistant?.proposals ?? [],
      lastEvidence: lastAssistant?.evidence ?? [],
      lastFollowups: lastAssistant?.followups ?? [],
      proposalCount: allProposals.length,
      isEmpty: messages.length === 0,
    };
  }, [messages]);

  return { sessionId, messages, sending, stage, error, ...derived, send, reset };
}
