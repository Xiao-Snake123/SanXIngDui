import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  clearChatSessions,
  deleteChatSession,
  deleteUserProfile,
  fetchChatSession,
  fetchChatSessions,
  getUserId,
  streamChat,
  type ChatIntent,
  type ChatMessageRecord,
  type ChatProposal,
  type ChatSessionSummary,
  type EvidenceChunk,
  type TraceEvent,
} from "@/services/agentApi";

/**
 * 当前会话 id 的本地记忆。
 *
 * 为什么必须持久化：会话 id 原本只活在 React state 里，刷新页面就丢了——
 * 服务端其实还存着这段对话，但前端不知道 id，找不回来。
 * 存进 localStorage 之后，刷新页面能自动回到刚才那段对话。
 */
const SESSION_ID_KEY = "sxd.current_session_id";

function readStoredSessionId(): string | null {
  try {
    return localStorage.getItem(SESSION_ID_KEY);
  } catch {
    return null;
  }
}

function writeStoredSessionId(value: string | null): void {
  try {
    if (value) localStorage.setItem(SESSION_ID_KEY, value);
    else localStorage.removeItem(SESSION_ID_KEY);
  } catch {
    /* 隐私模式下不可用：退化为不记忆当前会话，功能不受影响 */
  }
}

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
  const [sessionId, setSessionId] = useState<string | null>(readStoredSessionId);
  const [messages, setMessages] = useState<ChatMessageView[]>([]);
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
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

  const initializedRef = useRef(false);

  /** 更新当前会话 id，并同步写进本地记忆（刷新页面能回到这段对话）。 */
  const applySessionId = useCallback((value: string | null) => {
    setSessionId(value);
    writeStoredSessionId(value);
  }, []);

  /** 拉取会话列表。没有 user_id 就不请求——服务端也无从判断「这是谁的会话」。 */
  const refreshSessions = useCallback(async () => {
    const userId = getUserId();
    if (!userId) return;
    setSessions(await fetchChatSessions(userId));
  }, []);

  /**
   * 打开一个历史会话：拉详情并渲染成消息列表（含引用卡片）。
   *
   * 引用块是**完整存下来**的（不只是标题），因为回看时正文里带着 [1] 标记，
   * 点不开出处就等于「能看到结论、追不到来源」——那正是本项目要消灭的问题。
   */
  const openSession = useCallback(
    async (id: string) => {
      const detail = await fetchChatSession(id);
      if (!detail) {
        // 记录已经不在了（被删除）：清掉本地记忆，别让用户卡在一个死 id 上
        applySessionId(null);
        setMessages([]);
        return;
      }
      const history: ChatMessageView[] = detail.messages.map(
        (item: ChatMessageRecord, index: number) => ({
          id: `${id}-${index}`,
          role: item.role,
          content: item.content,
          streaming: false,
          proposals: item.proposals ?? [],
          evidence: item.evidence ?? [],
          intent: item.intent ?? null,
          followups: [],
          mode: "",
        }),
      );
      setMessages(history);
      applySessionId(id);
      setError(null);
    },
    [applySessionId],
  );

  /** 开始一段新对话（不动历史列表，只是不挂在任何旧会话上）。 */
  const startNewSession = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    applySessionId(null);
    setError(null);
    setStage("");
  }, [applySessionId]);

  /** 删除一个会话：后端清记录与缓存；若删的是当前会话，则顺带开始新的。 */
  const removeSession = useCallback(
    async (id: string) => {
      await deleteChatSession(id);
      if (id === sessionId) {
        setMessages([]);
        applySessionId(null);
      }
      await refreshSessions();
    },
    [applySessionId, refreshSessions, sessionId],
  );

  /**
   * 清除记忆：全部会话记录 + 长期画像，然后回到空状态。
   *
   * 两件事必须都做——只清会话的话，AI 仍然「认识你」（画像还在）；
   * 只清画像的话，聊天记录还躺在列表里。用户说「忘了我」时，两者缺一不可。
   */
  const clearMemory = useCallback(async () => {
    const userId = getUserId();
    if (!userId) return;
    abortRef.current?.abort();
    const removedSessions = await clearChatSessions(userId);
    const removedFacts = await deleteUserProfile(userId);
    if (removedSessions < 0 || removedFacts < 0) {
      // 后端用 -1 表示「失败」，区别于 0（本来就没数据）。
      // 必须让用户看见：点了清除却静默没生效，比直接报错更糟。
      setError("清除记忆未完成，请重试");
      return;
    }
    setMessages([]);
    applySessionId(null);
    setSessions([]);
    setError(null);
    setStage("");
  }, [applySessionId]);

  // 进入页面时：先拉列表；若本地还记着上次会话，就恢复它的历史消息。
  // 用 ref 守卫，保证只跑一次（不因依赖变化反复拉取）。
  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    void (async () => {
      await refreshSessions();
      const stored = readStoredSessionId();
      if (stored) await openSession(stored);
    })();
  }, [openSession, refreshSessions]);

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
            onSession: (payload) => applySessionId(payload.session_id),
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
        // 本轮已落库：列表里的标题 / 轮数 / 排序都会变，刷新一次
        void refreshSessions();
      }
    },
    [applySessionId, patchStreaming, refreshSessions, sending, sessionId],
  );

  /** 结束当前对话：删掉会话记录（后端同时清缓存），回到「未挂载会话」状态。 */
  const reset = useCallback(async () => {
    abortRef.current?.abort();
    const current = sessionId;
    setMessages([]);
    applySessionId(null);
    setError(null);
    setStage("");
    // 删的是记录本身：列表里不该再留着一个已经清空的会话
    if (current) await deleteChatSession(current);
    await refreshSessions();
  }, [applySessionId, refreshSessions, sessionId]);

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

  return {
    sessionId,
    messages,
    sending,
    stage,
    error,
    // 会话记录：列表与切换 / 新建 / 删除
    sessions,
    refreshSessions,
    openSession,
    startNewSession,
    removeSession,
    clearMemory,
    ...derived,
    send,
    reset,
  };
}
