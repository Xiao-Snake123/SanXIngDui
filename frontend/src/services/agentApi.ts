/**
 * 多智能体复原服务客户端。
 *
 * 与重构前的关键差别：
 * - 旧实现是「一次 fetch 拿一张图 URL」，进度条由 `setInterval` 随机数模拟；
 * - 新实现是「一条 SSE 长连接」，服务端把每个 Agent 节点的开始/结束、每次 LLM 调用、
 *   每次质检判决实时推下来。进度值来自真实节点，Agent 轨迹可以直接渲染成时间线。
 *
 * EventSource 不支持 POST，因此这里用 `fetch` + `ReadableStream` 手工解析 SSE 帧。
 * 这样做同时保留了「可携带 JSON body」与「可用 AbortSignal 取消」两个能力。
 */

const RAW_BASE = (import.meta.env.VITE_AGENT_BASE as string | undefined) ?? "/agent";
/** 代理前缀，例如 `/agent`；为空字符串时表示直连后端 */
export const AGENT_BASE = RAW_BASE.replace(/\/$/, "");

export const AGENT_ENDPOINTS = {
  health: `${AGENT_BASE}/api/health`,
  models: `${AGENT_BASE}/api/models`,
  graph: `${AGENT_BASE}/api/graph`,
  styles: `${AGENT_BASE}/api/styles`,
  metrics: `${AGENT_BASE}/api/metrics`,
  restore: `${AGENT_BASE}/api/restore`,
  stream: `${AGENT_BASE}/api/restore/stream`,
  eval: `${AGENT_BASE}/api/eval/run`,
  chat: `${AGENT_BASE}/api/chat/stream`,
  chatReset: `${AGENT_BASE}/api/chat/reset`,
  chatStats: `${AGENT_BASE}/api/chat/stats`,
  // 会话记录（持久化在 PostgreSQL，不是 Redis 缓存）：列表 / 详情 / 删除
  chatSessions: `${AGENT_BASE}/api/chat/sessions`,
  // 历史任务与质量统计来自 PostgreSQL（跨重启累计），不是进程内计数器
  tasks: `${AGENT_BASE}/api/tasks`,
  qualityStats: `${AGENT_BASE}/api/stats/quality`,
  // 领域问答：只依据本项目语料作答，答案与引用一起返回。
  // 之所以由后端提供而不是前端直连模型服务：引用必须来自我们自己的语料，
  // 且「检索不到就明说」这条纪律只有在自己的代码里才拦得住。
  ask: `${AGENT_BASE}/api/ask`,
};

// ─── 领域类型 ────────────────────────────────────────────────────────────────
export type RestoreKind = "scene" | "figure" | "artifact" | "style";

export type AgentNode =
  | "planner"
  | "supervisor"
  | "retrieval"
  | "restoration"
  | "quality"
  | "copywriting"
  | "finalize";

export interface RestoreRequest {
  kind: RestoreKind;
  // 场景复原
  identity?: string;
  scene?: string;
  item?: string;
  style?: string;
  // 人物还原
  gender?: string;
  rank?: string;
  era?: string;
  expression?: string;
  detail?: string;
  // 文物修复
  artifact?: string;
  method?: string;
  mode?: string;
  damage?: string;
  // 风格迁移
  style_preset?: string;
  strength?: number;
  // 通用
  note?: string;
  seed?: number;
  reference_images?: string[];
  max_revisions?: number;
  /** 快速模式：跳过 planner LLM 增强与质检 VLM 裁判/回炉，首图更快。默认 true。 */
  fast?: boolean;
  /** 覆盖出图模型（如 z-image-turbo / qwen-image-plus / qwen-image-2.0-pro）。 */
  model_image?: string;
  session_id?: string;
  /** 用户在对话中选定的提示词方案。提供时 Planner 不再改写 prompt。 */
  prompt_override?: PromptOverride;
}

/** 与后端 `app/api/schemas.py::PromptOverride` 一一对应。 */
export interface PromptOverride {
  prompt: string;
  negative_prompt?: string;
  /** 目标风格档案 key（影响质检阈值） */
  profile_key?: string | null;
  width?: number;
  height?: number;
  /** 是否允许模型扩写提示词。默认 false：选定方案必须逐字使用。 */
  prompt_extend?: boolean;
  proposal_id?: string | null;
  proposal_title?: string | null;
}

export interface TraceEvent {
  seq: number;
  task_id: string;
  kind: string;
  node: string;
  ts: string;
  duration_ms?: number | null;
  payload: Record<string, unknown>;
}

export interface QaVerdict {
  passed: boolean;
  /** 占位图/非生成图时会跳过质检，此时分数无意义（为 null），不能用 0 冒充 */
  skipped?: boolean;
  skip_reason?: string;
  score: number | null;
  objective_score: number | null;
  judge_score: number | null;
  threshold: number | null;
  round_index: number;
  dimensions: Record<string, number>;
  violations: Array<{ metric: string; value: number; expected: string; severity: number; directive: string }>;
  anachronisms: string[];
  feedback: string[];
  reasoning: string;
  degraded: boolean;
  decision?: "accept" | "revise" | "give_up" | "skipped";
  decision_reason?: string;
}

export interface EvidenceChunk {
  doc_id: string;
  title: string;
  text: string;
  object: string;
  era: string;
  category: string;
  tags: string[];
  source: string;
  scores: { bm25: number; dense: number; fused: number; rerank: number; final: number };
  matched_terms: string[];
  channels: string[];
}

export interface CopyPayload {
  text: string;
  mode: string;
  length: number;
  highlights: string[];
  cautions: string[];
  sources: Array<{ doc_id: string; title: string; source: string }>;
  qa_disclosure: string;
  disclaimer: string;
}

export interface RestorationResult {
  ok: boolean;
  task_id: string;
  kind: RestoreKind;
  goal?: string;
  engine: string;
  duration_ms: number;
  image_url?: string | null;
  image_provider?: string | null;
  image_degraded: boolean;
  seed?: number | null;
  plan: {
    goal?: string;
    profile_label?: string;
    planner_mode?: string;
    retrieval_queries?: string[];
    acceptance_criteria?: string[];
    image_spec?: { prompt?: string; negative_prompt?: string };
    subtasks?: Array<{ id: string; agent: string; objective: string }>;
  };
  retrieval: {
    queries?: string[];
    hits?: number;
    synthesis_mode?: string;
    brief?: string;
    key_facts?: string[];
    image_cues?: string[];
    cautions?: string[];
    diagnostics?: Record<string, unknown>;
  };
  evidence: EvidenceChunk[];
  /** 出图细节：实际下发的 prompt、provider、失败原因 —— 排查「图为什么长这样」的第一手材料 */
  image: {
    provider?: string;
    degraded?: boolean;
    prompt?: string;
    negative_prompt?: string;
    error?: string | null;
    round_index?: number;
    profile_key?: string;
    profile_label?: string;
    [key: string]: unknown;
  };
  qa: QaVerdict;
  copy: CopyPayload;
  revision_history: Array<{ round_index: number; image_url: string; provider: string; seed: number | null; prompt: string; degraded: boolean }>;
  revisions: number;
  degraded_components: string[];
  errors: string[];
  usage?: { llm_calls: number; prompt_tokens: number; completion_tokens: number; models: Record<string, number> };
  error?: string;
}

export interface NodeEvent {
  type: "node";
  node: AgentNode;
  label: string;
  progress: number;
  completed: string[];
  next?: string;
  reason?: string;
  elapsed_ms: number;
  patch: Record<string, unknown>;
}

export interface StartEvent {
  type: "start";
  task_id: string;
  kind: RestoreKind;
  max_revisions: number;
  engine: string;
}

export interface DoneEvent {
  type: "done";
  task_id: string;
  result: RestorationResult;
}

// ─── SSE 解析 ────────────────────────────────────────────────────────────────
export interface StreamHandlers {
  onStart?: (event: StartEvent) => void;
  onNode?: (event: NodeEvent) => void;
  onTrace?: (event: TraceEvent) => void;
  onDone?: (event: DoneEvent) => void;
  onError?: (message: string) => void;
}

type SseFrameHandler = (event: string, payload: unknown) => void;

/**
 * 通用的 SSE 帧读取器。
 *
 * 复原接口与对话接口共用同一套帧协议（`event: <type>` + `data: <json>`），
 * 因此解析逻辑只实现一次 —— 否则两个接口迟早会出现「一个能断线重连、
 * 另一个不行」这类不一致。
 */
export async function consumeSse(response: Response, onFrame: SseFrameHandler): Promise<void> {
  if (!response.ok || !response.body) {
    const detail = await response.text().catch(() => "");
    throw new Error(`服务返回 ${response.status}：${detail.slice(0, 300)}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const handleFrame = (frame: string) => {
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (!line || line.startsWith(":")) continue;
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0) return;
    try {
      onFrame(eventName, JSON.parse(dataLines.join("\n")));
    } catch {
      /* 半帧或非 JSON，忽略即可 */
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let separator = buffer.indexOf("\n\n");
      while (separator !== -1) {
        handleFrame(buffer.slice(0, separator));
        buffer = buffer.slice(separator + 2);
        separator = buffer.indexOf("\n\n");
      }
    }
    // 流结束时可能还留着一帧没有结尾空行（服务端提前关闭）。
    // 少了这一行，最后一帧会被静默丢掉 —— 表现为「图已生成但界面卡在 68%」。
    if (buffer.trim()) handleFrame(buffer);
  } finally {
    reader.releaseLock();
  }
}

async function openSse(url: string, body: unknown, signal?: AbortSignal): Promise<Response> {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
}

/**
 * 发起一次流式复原，返回一个可取消的 Promise。
 * 抛出 `AbortError` 表示调用方主动取消，调用方应忽略该异常。
 */
export async function streamRestoration(
  request: RestoreRequest,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await openSse(AGENT_ENDPOINTS.stream, request, signal);
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    handlers.onError?.(
      "无法连接复原服务。请确认后端已启动（backend/scripts/run.ps1），且代理 /agent 指向正确。",
    );
    return;
  }

  try {
    await consumeSse(response, (event, payload) => {
      switch (event) {
        case "start":
          handlers.onStart?.(payload as StartEvent);
          break;
        case "node":
          handlers.onNode?.(payload as NodeEvent);
          break;
        case "trace":
          handlers.onTrace?.(payload as TraceEvent);
          break;
        case "done":
          handlers.onDone?.(payload as DoneEvent);
          break;
        case "error":
          handlers.onError?.((payload as { message?: string }).message ?? "复原任务执行失败");
          break;
        default:
          break;
      }
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    handlers.onError?.(`数据流中断：${(error as Error).message}`);
  }
}

// ─── 对话接口 ────────────────────────────────────────────────────────────────
export interface ChatProposal {
  id: string;
  strategy: string;
  title: string;
  angle: string;
  style_profile: string;
  style_label: string;
  prompt: string;
  negative_prompt: string;
  rationale: string;
  risk: string;
  tags: string[];
  /** 宽度/高度/风格强度/提示词增强 —— 只上报真正会下发给模型的字段，
   *  原先的 steps/cfg/lora_strength 属于扩散采样器（已随 ComfyUI 链路移除），
   *  继续展示等于给用户看一组不生效的旋钮。 */
  params: {
    width: number;
    height: number;
    /** 0~1；仅用于提示词措辞与展示，null 表示该方案不涉及强度 */
    style_strength: number | null;
    prompt_extend: boolean;
  };
  evidence_ids: string[];
  generated_by: string;
  /** AI 选定的默认项；为 true 时该方案是模型推荐、且已默认选中，用户不必再挑。 */
  recommended?: boolean;
}

export interface ChatIntent {
  kind: RestoreKind;
  subject?: string;
  identity?: string;
  scene?: string;
  style?: string;
  method?: string;
  style_preset?: string;
  strength?: number;
  brief?: string;
}

export interface ChatTurnPayload {
  role: "user" | "assistant";
  content: string;
  ts?: number;
  proposals?: ChatProposal[];
  intent?: ChatIntent;
  evidence_titles?: string[];
}

export interface ChatStreamHandlers {
  onSession?: (payload: { session_id: string; turns: number }) => void;
  onTrace?: (event: TraceEvent) => void;
  onIntent?: (payload: { intent: ChatIntent; mode: string }) => void;
  onEvidence?: (payload: { items: EvidenceChunk[]; cues: string[]; meta: Record<string, unknown> }) => void;
  onDelta?: (text: string) => void;
  onProposals?: (payload: { items: ChatProposal[]; mode: string }) => void;
  onMessage?: (payload: { text: string; mode: string }) => void;
  onFollowups?: (items: string[]) => void;
  onError?: (message: string) => void;
}

/**
 * 长期画像的用户标识：首次访问时生成并写进 localStorage，之后一直复用。
 *
 * 为什么不接登录系统：长期记忆需要的是「把跨会话记住的事实归属到同一个人」，
 * 而本项目目前没有账号体系。用一个浏览器级标识即可满足，不必要求用户注册。
 * 代价是换浏览器 / 清缓存会被当作新用户——这是当前无账号状态下的合理折中。
 *
 * 取值受后端校验约束：只允许 `[A-Za-z0-9_-]`，最长 64。
 */
const USER_ID_KEY = "sxd.user_id";

export function getUserId(): string {
  try {
    const existing = localStorage.getItem(USER_ID_KEY);
    if (existing) return existing;
    const random = crypto.randomUUID().replace(/-/g, "").slice(0, 24);
    const generated = `u-${random}`;
    localStorage.setItem(USER_ID_KEY, generated);
    return generated;
  } catch {
    // 隐私模式等场景下 localStorage / crypto 不可用：不传 user_id，
    // 后端退化成「只有会话内短期记忆」，功能不受影响。
    return "";
  }
}

export async function streamChat(
  message: string,
  sessionId: string | null,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  const userId = getUserId();
  try {
    response = await openSse(
      AGENT_ENDPOINTS.chat,
      { message, session_id: sessionId, user_id: userId || undefined },
      signal,
    );
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    handlers.onError?.("无法连接对话服务，请确认后端已启动。");
    return;
  }

  try {
    await consumeSse(response, (event, payload) => {
      const data = payload as Record<string, unknown>;
      switch (event) {
        case "session":
          handlers.onSession?.(payload as { session_id: string; turns: number });
          break;
        case "trace":
          handlers.onTrace?.(payload as TraceEvent);
          break;
        case "intent":
          handlers.onIntent?.(payload as { intent: ChatIntent; mode: string });
          break;
        case "evidence":
          handlers.onEvidence?.(payload as { items: EvidenceChunk[]; cues: string[]; meta: Record<string, unknown> });
          break;
        case "delta":
          handlers.onDelta?.(String(data.text ?? ""));
          break;
        case "proposals":
          handlers.onProposals?.(payload as { items: ChatProposal[]; mode: string });
          break;
        case "message":
          handlers.onMessage?.(payload as { text: string; mode: string });
          break;
        case "followups":
          handlers.onFollowups?.((data.items as string[]) ?? []);
          break;
        case "error":
          handlers.onError?.(String(data.message ?? "对话执行失败"));
          break;
        default:
          break;
      }
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    handlers.onError?.(`对话流中断：${(error as Error).message}`);
  }
}

export async function resetChatSession(sessionId: string): Promise<void> {
  try {
    await fetch(AGENT_ENDPOINTS.chatReset, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
  } catch {
    /* 重置失败不影响本地清空 */
  }
}

/** 会话列表里的一行（后端 chat_sessions 的投影）。 */
export interface ChatSessionSummary {
  session_id: string;
  title: string | null;
  turn_count: number;
  last_message_at: string | null;
  created_at: string | null;
}

/** 会话详情里的一条消息（后端 chat_messages 的投影）。 */
export interface ChatMessageRecord {
  role: "user" | "assistant";
  content: string;
  proposals: ChatProposal[];
  intent: ChatIntent | null;
  evidence_titles: string[];
  /** 完整引用块：回看时正文里的 [1] 要能点开出处的原文与链接。 */
  evidence: EvidenceChunk[];
  created_at: string | null;
}

export interface ChatSessionDetail {
  session_id: string;
  meta: ChatSessionSummary | null;
  messages: ChatMessageRecord[];
}

/** 列出某用户的历史会话（按最近对话倒序）。 */
export async function fetchChatSessions(
  userId: string,
  signal?: AbortSignal,
): Promise<ChatSessionSummary[]> {
  try {
    const url = `${AGENT_ENDPOINTS.chatSessions}?user_id=${encodeURIComponent(userId)}`;
    const response = await fetch(url, { signal });
    if (!response.ok) return [];
    const data = (await response.json()) as { items: ChatSessionSummary[] };
    return data.items ?? [];
  } catch {
    // 拿不到列表不该让整个对话页挂掉：退化成「没有历史会话」
    return [];
  }
}

/** 取某会话的完整消息（回看）。即使 Redis 缓存已过期也能取到——来自持久记录。 */
export async function fetchChatSession(
  sessionId: string,
  signal?: AbortSignal,
): Promise<ChatSessionDetail | null> {
  try {
    const response = await fetch(
      `${AGENT_ENDPOINTS.chatSessions}/${encodeURIComponent(sessionId)}`,
      { signal },
    );
    if (!response.ok) return null;
    return (await response.json()) as ChatSessionDetail;
  } catch {
    return null;
  }
}

/** 清空某用户的全部会话记录（只清「聊过什么」，不动长期画像）。 */
export async function clearChatSessions(userId: string): Promise<number> {
  try {
    const url = `${AGENT_ENDPOINTS.chatSessions}?user_id=${encodeURIComponent(userId)}`;
    const response = await fetch(url, { method: "DELETE" });
    if (!response.ok) return 0;
    const data = (await response.json()) as { removed: number };
    return data.removed ?? 0;
  } catch {
    return 0;
  }
}

/** 删除某用户的长期画像——「这个人是谁」那一层。 */
export async function deleteUserProfile(userId: string): Promise<number> {
  try {
    const response = await fetch(
      `${AGENT_BASE}/api/profile/${encodeURIComponent(userId)}`,
      { method: "DELETE" },
    );
    if (!response.ok) return 0;
    const data = (await response.json()) as { removed: number };
    return data.removed ?? 0;
  } catch {
    return 0;
  }
}

/** 删除会话：后端会同时清掉持久记录与 Redis 缓存。 */
export async function deleteChatSession(sessionId: string): Promise<void> {
  try {
    await fetch(`${AGENT_ENDPOINTS.chatSessions}/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
  } catch {
    /* 删除失败不影响本地刷新 */
  }
}

/** 把方案转成出图请求的 `prompt_override`（可在前端编辑后再传）。 */
export function proposalToOverride(
  proposal: ChatProposal,
  overrides?: Partial<PromptOverride>,
): PromptOverride {
  return {
    prompt: overrides?.prompt ?? proposal.prompt,
    negative_prompt: overrides?.negative_prompt ?? proposal.negative_prompt,
    profile_key: proposal.style_profile,
    width: proposal.params.width,
    height: proposal.params.height,
    // 选定方案一律关闭提示词增强：用户选的就是这份文字，不允许模型改写。
    // 这是「已锁定为你选定的提示词」这句承诺在参数层面的落地。
    prompt_extend: false,
    proposal_id: proposal.id,
    proposal_title: proposal.title,
    ...overrides,
  };
}

/** 把对话意图转成复原请求的主体字段。
 *  返回类型里 `kind` 是必填的：ChatIntent.kind 一定有值，
 *  这样调用方 `restore.run({...intentToRequest(intent), ...})` 才能通过类型检查 ——
 *  用 Partial<RestoreRequest> 会让 kind 变成可选，白白丢失一个已知信息。
 */
export function intentToRequest(
  intent: ChatIntent,
): { kind: RestoreKind } & Partial<RestoreRequest> {
  const base: { kind: RestoreKind } & Partial<RestoreRequest> = {
    kind: intent.kind,
    style: intent.style,
    item: intent.subject,
    identity: intent.identity,
    scene: intent.scene,
    artifact: intent.subject,
    method: intent.method,
    style_preset: intent.style_preset,
  };
  if (intent.strength) base.strength = intent.strength;
  return base;
}

// ─── 非流式辅助接口 ──────────────────────────────────────────────────────────
/**
 * 存储三层（PostgreSQL / Redis / pgvector）的**实际**档位。
 *
 * `backend` 是「真的在用哪一个」，`reason` 是连不上时的原始异常 ——
 * 两者都不是「配置里写了什么」。后端刻意把失败原因原样透出，
 * 前端也不应该把它美化成一句「服务不可用」。
 */
export interface StorageHealth {
  database: {
    backend: string;
    available: boolean;
    configured: boolean;
    server_version: string | null;
    pgvector_version: string | null;
    schema_revision: string | null;
    reason: string | null;
  };
  sessions: { backend: string; available: boolean; configured: boolean; reason: string | null };
  vector: {
    backend: string;
    pgvector_version: string | null;
    dimension: number;
    reason: string | null;
  };
}

export interface AgentHealth {
  status: string;
  engine: { engine: string; configured: string; fallback_reason: string | null };
  providers: Record<string, boolean | string>;
  retrieval: {
    corpus_size: number;
    embedder: string;
    embedder_degraded: boolean;
    files?: string[];
    vector_backend?: string;
    vector_dimension?: number;
    vector_reused?: number;
    vector_embedded?: number;
  };
  models_configured: boolean;
  style_profiles?: number;
  version: string;
  storage?: StorageHealth;
}

export async function fetchAgentHealth(signal?: AbortSignal): Promise<AgentHealth | null> {
  try {
    const response = await fetch(AGENT_ENDPOINTS.health, { signal });
    if (!response.ok) return null;
    return (await response.json()) as AgentHealth;
  } catch {
    return null;
  }
}

// ─── 历史任务与质量统计（来自 PostgreSQL，不是进程内计数）──────────────────
export interface TaskSummary {
  task_id: string;
  kind: string;
  item: string | null;
  identity: string | null;
  scene: string | null;
  style: string | null;
  goal: string | null;
  engine: string | null;
  profile_key: string | null;
  material_key: string | null;
  status: string;
  score: number | null;
  objective_score: number | null;
  judge_score: number | null;
  passed: boolean | null;
  decision: string | null;
  revisions: number;
  provider: string | null;
  image_degraded: boolean;
  qa_skipped: boolean;
  evidence_count: number;
  degraded_components: string[];
  duration_ms: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface TaskImageRow {
  round_index: number;
  image_url: string | null;
  provider: string | null;
  seed: number | null;
  width: number | null;
  height: number | null;
  /** 实际下发的那份提示词（回炉轮次里修正指令在前），不是规划阶段的草稿 */
  prompt: string | null;
  negative_prompt: string | null;
  degraded: boolean;
  bytes_available: boolean;
  created_at: string | null;
}

export interface TaskVerdictRow {
  round_index: number;
  score: number | null;
  objective_score: number | null;
  judge_score: number | null;
  threshold: number | null;
  passed: boolean | null;
  decision: string | null;
  reason: string | null;
  feedback: string[];
  anachronisms: string[];
  dimensions: Record<string, number>;
  violated_rules: Array<Record<string, unknown>>;
  material_key: string | null;
  profile_key: string | null;
  created_at: string | null;
}

export interface TaskDetail extends TaskSummary {
  request: Record<string, unknown>;
  error: string | null;
  images: TaskImageRow[];
  verdicts: TaskVerdictRow[];
}

export interface TaskListResponse {
  items: TaskSummary[];
  total: number;
  limit?: number;
  offset?: number;
  available: boolean;
}

export interface QualityBucket {
  kind: string | null;
  tasks: number;
  /** 真正判过分的任务数（落占位图时质检是 skipped，不算进分母） */
  judged: number;
  passed: number;
  pass_rate: number | null;
  avg_score: number | null;
  avg_revisions: number | null;
  avg_duration_ms: number | null;
  qa_skipped: number;
}

export interface QualityStats {
  available: boolean;
  window_days?: number;
  overall?: QualityBucket;
  by_kind?: QualityBucket[];
  reason?: string;
}

export async function fetchTaskList(
  params: { limit?: number; kind?: string; decision?: string; passed?: boolean } = {},
  signal?: AbortSignal,
): Promise<TaskListResponse | null> {
  const query = new URLSearchParams();
  query.set("limit", String(params.limit ?? 20));
  if (params.kind) query.set("kind", params.kind);
  if (params.decision) query.set("decision", params.decision);
  if (params.passed !== undefined) query.set("passed", String(params.passed));
  try {
    const response = await fetch(`${AGENT_ENDPOINTS.tasks}?${query.toString()}`, { signal });
    if (!response.ok) return null;
    return (await response.json()) as TaskListResponse;
  } catch {
    return null;
  }
}

export async function fetchTaskDetail(taskId: string, signal?: AbortSignal): Promise<TaskDetail | null> {
  try {
    const response = await fetch(`${AGENT_ENDPOINTS.tasks}/${encodeURIComponent(taskId)}`, { signal });
    if (!response.ok) return null;
    return (await response.json()) as TaskDetail;
  } catch {
    return null;
  }
}

export async function fetchQualityStats(days = 30, signal?: AbortSignal): Promise<QualityStats | null> {
  try {
    const response = await fetch(`${AGENT_ENDPOINTS.qualityStats}?days=${days}`, { signal });
    if (!response.ok) return null;
    return (await response.json()) as QualityStats;
  } catch {
    return null;
  }
}

// ─── 模型矩阵 / 图拓扑 / 端点清单 ────────────────────────────────────────────
export interface ModelRole {
  role: string;
  title: string;
  primary: string;
  fallbacks: string[];
  modality: string;
  rationale: string;
  cost_hint: string;
  latency_hint: string;
  tags: string[];
}

export interface ModelMatrix {
  provider: string;
  configured: boolean;
  base_url: string;
  roles: ModelRole[];
  image_generation: {
    primary: string;
    endpoint: string;
    fallback: string;
    prompt_extend: boolean;
    watermark: boolean;
  };
  local_finetune: { base: string; adapter: string | null; enabled: boolean };
}

export async function fetchModelMatrix(signal?: AbortSignal): Promise<ModelMatrix | null> {
  try {
    const response = await fetch(AGENT_ENDPOINTS.models, { signal });
    if (!response.ok) return null;
    return (await response.json()) as ModelMatrix;
  } catch {
    return null;
  }
}

export interface GraphInfo {
  engine: string;
  configured: string;
  fallback_reason: string | null;
  max_revisions: number;
  style_threshold: number;
  max_graph_steps: number;
  topology: { entry: string; nodes: Record<string, string[]>; self_correction_loop: string };
}

export async function fetchGraph(signal?: AbortSignal): Promise<GraphInfo | null> {
  try {
    const response = await fetch(AGENT_ENDPOINTS.graph, { signal });
    if (!response.ok) return null;
    return (await response.json()) as GraphInfo;
  } catch {
    return null;
  }
}

export interface OpenApiOperation {
  method: string;
  path: string;
  summary: string;
  tags: string[];
}

/**
 * 直接读后端的 OpenAPI 文档来渲染端点清单。
 *
 * 为什么不用前端手写一份：手写的清单**一定会漂移**，而漂移的 API 文档比没有文档更糟
 * （调用方会照着错的写）。FastAPI 已经把真实路由暴露成 openapi.json，
 * 拿它渲染就不可能「文档里有的接口其实不存在」（这正是这个页面原来的问题）。
 */
export async function fetchOpenApiOperations(signal?: AbortSignal): Promise<OpenApiOperation[]> {
  try {
    const response = await fetch(`${AGENT_BASE}/openapi.json`, { signal });
    if (!response.ok) return [];
    const spec = (await response.json()) as {
      paths?: Record<string, Record<string, { summary?: string; tags?: string[] }>>;
    };
    const methods = ["get", "post", "put", "patch", "delete"];
    const operations: OpenApiOperation[] = [];
    for (const [path, operationsByMethod] of Object.entries(spec.paths ?? {})) {
      for (const [method, detail] of Object.entries(operationsByMethod ?? {})) {
        if (!methods.includes(method.toLowerCase())) continue;
        operations.push({
          method: method.toUpperCase(),
          path,
          summary: detail?.summary ?? "",
          tags: detail?.tags ?? [],
        });
      }
    }
    return operations.sort((a, b) => a.path.localeCompare(b.path));
  } catch {
    return [];
  }
}

export interface StyleProfileInfo {
  key: string;
  label: string;
  aliases: string[];
  creative: boolean;
  saturation_range: [number, number];
  specular_max: number;
  family_min: Record<string, number>;
  rubric: string;
}

export async function fetchStyleProfiles(): Promise<StyleProfileInfo[]> {
  try {
    const response = await fetch(AGENT_ENDPOINTS.styles);
    if (!response.ok) return [];
    const data = (await response.json()) as { profiles: StyleProfileInfo[] };
    return data.profiles ?? [];
  } catch {
    return [];
  }
}

/** 把后端返回的相对路径（/media/xxx.png）解析成浏览器可访问的地址。 */
export function resolveAssetUrl(url: string | null | undefined): string {
  if (!url) return "";
  if (url.startsWith("data:") || url.startsWith("http://") || url.startsWith("https://")) {
    return url;
  }
  return url.startsWith("/") ? `${AGENT_BASE}${url}` : `${AGENT_BASE}/${url}`;
}

// ─── 引用图转 Data URI（供上传参考图使用）────────────────────────────────────
export async function fileToDataUri(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("读取参考图失败"));
    reader.readAsDataURL(file);
  });
}
