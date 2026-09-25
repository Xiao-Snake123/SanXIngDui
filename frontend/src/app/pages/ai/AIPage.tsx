import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useParams } from "react-router";
import { Info, ServerCrash, TriangleAlert } from "lucide-react";
import { ChatPanel } from "@/app/components/ChatPanel";
import { SessionList } from "@/app/components/SessionList";
import type { ArtifactPayload } from "@/app/components/ArtifactView";
import { useAgentChat } from "@/app/hooks/useAgentChat";
import { useAgentRestoration } from "@/app/hooks/useAgentRestoration";
import {
  AGENT_BASE,
  fetchAgentHealth,
  intentToRequest,
  proposalToOverride,
  resolveAssetUrl,
  type AgentHealth,
  type ChatIntent,
  type ChatProposal,
} from "@/services/agentApi";

const GOLD = "#D4AF37";
const TEAL = "#45A29E";
const MUTED = "#556372";
const BG = "#0D1117";

/**
 * Tab 现在只用来切换起手示例，不再决定表单结构 —— 意图由对话理解得出。
 * 保留它们的唯一理由是：新用户不知道「这个系统能做什么」，需要几个范例。
 */
const QUICK_STARTS: Record<string, string[]> = {
  scene: [
    "做一个大祭司站在青铜神树祭坛前的场景，博物馆纪实摄影风格",
    "黄金面具在三星堆祭祀坑出土现场，考古档案照片风格",
    "青铜纵目面具在博物馆展厅里，电影级写实风格",
  ],
  figure: [
    "还原一个古蜀大祭司的人物形象，全身像",
    "纵目面具祭司的面部特写，真实照片风格",
    "鱼凫王朝的贵族半身像，博物馆纪实摄影",
  ],
  artifact: [
    "帮我修复这件破损青铜面具，用最小干预修复的方式",
    "残缺玉璋的病害情况，我要做诊断",
    "锈蚀铜头像修复后的博物馆展陈效果",
  ],
  style: [
    "把青铜大立人迁移成赛博古蜀风格，强度 80%",
    "把青铜神树改成神庙壁画的风格",
    "把青铜面具做成考古素描的样子",
  ],
};

const GREETINGS: Record<string, string> = {
  scene: "想复原哪个三星堆场景？",
  figure: "想还原谁的形象？",
  artifact: "想修复哪件文物？",
  style: "想把器物迁移成什么风格？",
};

/**
 * 单栏对话式页面。
 *
 * 结构上刻意保持「一个滚动区 + 一个固定输入框」：
 * 不做左右分栏、不做侧边栏、不做 Tab 表单。
 * 理由是一次复原任务的所有状态（意图 / 方案 / 出图 / 质检 / 文案）
 * 都归属于同一次对话，把它们摊在同一个时间线上，因果才不需要用户自己拼。
 */
export function AIPage() {
  const { tab } = useParams<{ tab: string }>();
  const activeTab = QUICK_STARTS[tab ?? ""] ? (tab as string) : "scene";

  const chat = useAgentChat();
  const restore = useAgentRestoration();
  const [selectedProposal, setSelectedProposal] = useState<{ id: string; title: string } | null>(null);
  const [lockedPrompt, setLockedPrompt] = useState<string | null>(null);
  const [fastMode, setFastMode] = useState(true);
  const [imageModel, setImageModel] = useState("z-image-turbo");
  /** 出图结果归属到「触发它的那条助手消息」，这样它才能内联在正确的位置 */
  const [ownerMessageId, setOwnerMessageId] = useState<string | null>(null);

  const handleGenerate = async (
    proposal: ChatProposal,
    editedPrompt: string,
    intent: ChatIntent | null,
    messageId: string,
  ) => {
    setSelectedProposal({ id: proposal.id, title: proposal.title });
    setOwnerMessageId(messageId);

    const resolvedIntent: ChatIntent = intent ?? { kind: "scene" };
    const override = proposalToOverride(proposal, { prompt: editedPrompt });
    setLockedPrompt(override.prompt);

    // 用与「无对话的出图」完全相同的执行器，避免两条链路行为漂移
    await restore.run({
      ...intentToRequest(resolvedIntent),
      session_id: chat.sessionId ?? undefined,
      prompt_override: override,
      fast: fastMode,
      model_image: imageModel,
    });
  };

  const handleReset = async () => {
    await chat.reset();
    setSelectedProposal(null);
    setLockedPrompt(null);
    setOwnerMessageId(null);
    restore.reset();
  };

  // ── 会话记录：切换 / 新建 / 删除 ──────────────────────────────────────────
  // 三者都要清掉「出图相关状态」：那些东西属于某条具体消息，
  // 换会话后还挂着上一场的结果会造成张冠李戴。
  const clearRestoreState = () => {
    setSelectedProposal(null);
    setLockedPrompt(null);
    setOwnerMessageId(null);
    restore.reset();
  };

  const handleSelectSession = async (id: string) => {
    await chat.openSession(id);
    clearRestoreState();
  };

  const handleNewSession = () => {
    chat.startNewSession();
    clearRestoreState();
  };

  const handleDeleteSession = async (id: string) => {
    await chat.removeSession(id);
    clearRestoreState();
  };

  const handleClearMemory = async () => {
    await chat.clearMemory();
    clearRestoreState();
  };

  const artifactsByMessage = useMemo<Record<string, ArtifactPayload>>(() => {
    if (!ownerMessageId) return {};
    // `image_url` 有两种形态，必须一视同仁地过一遍 resolveAssetUrl：
    //   * DashScope 返回 OSS 绝对直链 —— resolveAssetUrl 对 http(s) 原样返回；
    //   * 本地占位图返回 `/media/xxx.png` —— 不过这一层的话，浏览器会去请求
    //     前端自己的 5173/media/xxx.png，而 vite 把它当 SPA 路由**返回 200 + HTML**。
    //     `<img>` 拿到 HTML 解不出像素，界面显示成破图。
    //     （实测：5173/media 返回 `text/html 598B`，5173/agent/media 才是 `image/png`。）
    const rawImageUrl = restore.result?.image_url ? restore.result.image_url : null;
    const imageUrl = rawImageUrl ? resolveAssetUrl(rawImageUrl) : null;
    const provider = String(restore.result?.image_provider ?? "");
    const placeholder = provider.includes("placeholder");

    return {
      [ownerMessageId]: {
        running: restore.running,
        progress: restore.progress,
        stageLabel: [...restore.steps].reverse().find((step) => step.status === "running")?.label ?? "",
        steps: restore.steps,
        qaHistory: restore.qaHistory,
        imageUrl,
        roundImages: restore.roundImages.map((item) => ({
          ...item,
          url: resolveAssetUrl(item.url),
        })),
        finalRound: restore.roundImages.length
          ? restore.roundImages[restore.roundImages.length - 1].round
          : 0,
        provider,
        isPlaceholder: placeholder,
        imageError: String(restore.result?.image?.error ?? "").slice(0, 160),
        qa: restore.result?.qa ?? null,
        revisions: restore.result?.revisions ?? 0,
        copy: restore.copy,
        evidence: restore.evidence,
        lockedPrompt,
        proposalTitle: selectedProposal?.title ?? "",
        taskId: restore.taskId,
        durationMs: restore.elapsedMs,
        stopped: restore.stopped,
        onStop: restore.stop,
        onClear: () => {
          restore.reset();
          setOwnerMessageId(null);
        },
      },
    };
  }, [ownerMessageId, restore, lockedPrompt, selectedProposal]);

  return (
    <div
      style={{
        paddingTop: 64,
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: BG,
        boxSizing: "border-box",
      }}
    >
      <ServiceBanner />
      {restore.error && (
        <Notice
          tone="error"
          icon={<ServerCrash size={14} color="#fca5a5" />}
          title="复原任务失败"
          text={restore.error}
        />
      )}
      {chat.error && (
        <Notice
          tone="error"
          icon={<TriangleAlert size={14} color="#fca5a5" />}
          title="对话请求失败"
          text={chat.error}
        />
      )}

      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <SessionList
          sessions={chat.sessions}
          currentId={chat.sessionId}
          onSelect={handleSelectSession}
          onNew={handleNewSession}
          onDelete={handleDeleteSession}
          onClearMemory={handleClearMemory}
        />
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <ChatPanel
            messages={chat.messages}
            sending={chat.sending}
            stage={chat.stage}
            sessionId={chat.sessionId}
            quickStarts={QUICK_STARTS[activeTab]}
            generating={restore.running}
            selectedProposalId={selectedProposal?.id ?? null}
            artifactsByMessage={artifactsByMessage}
            greeting={GREETINGS[activeTab]}
            fastMode={fastMode}
            imageModel={imageModel}
            onFastModeChange={setFastMode}
            onImageModelChange={setImageModel}
            onSend={chat.send}
            onReset={handleReset}
            onGenerate={handleGenerate}
          />
        </div>
      </div>
    </div>
  );
}

/** 顶部服务状态条。默认折叠成一行，避免把对话区挤小。 */
function ServiceBanner() {
  const [health, setHealth] = useState<AgentHealth | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    void fetchAgentHealth().then((result) => {
      if (!alive) return;
      if (result) setHealth(result);
      else setUnreachable(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  if (unreachable) {
    return (
      <Notice
        tone="error"
        icon={<ServerCrash size={14} color="#fca5a5" />}
        title="无法连接多智能体后端"
        text={`请确认后端已启动（${AGENT_BASE}）。界面不会伪造进度：连不上就是连不上。`}
      />
    );
  }
  if (!health) return null;

  const degraded: string[] = [];
  if (!health.models_configured) degraded.push("未配置 DASHSCOPE_API_KEY（对话与文案降级为规则实现）");
  if (health.retrieval?.embedder_degraded) degraded.push("向量通道降级为本地哈希向量");
  if (health.engine?.fallback_reason) degraded.push(`编排引擎回退：${health.engine.fallback_reason}`);

  const active = Object.entries(health.providers ?? {})
    .filter(([, ready]) => ready === true)
    .map(([name]) => name);
  const hasReal = active.some((name) => name !== "local-placeholder");
  if (!hasReal) {
    degraded.push("没有任何真实出图通道，出图将是本地占位示意图（非生成图）");
  } else if (active.includes("free-third-party")) {
    degraded.push("出图使用第三方免鉴权通道（带水印、不保证稳定性）");
  }

  const ok = degraded.length === 0;
  const color = ok ? TEAL : GOLD;

  return (
    <div style={{ borderBottom: `1px solid ${color}30`, background: `${color}0A`, flexShrink: 0 }}>
      <button
        onClick={() => setOpen((value) => !value)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "7px 20px",
          background: "none",
          border: "none",
          cursor: "pointer",
          color: MUTED,
          fontFamily: "'Noto Sans SC', sans-serif",
          fontSize: 10.5,
        }}
      >
        <Info size={12} color={color} />
        <span style={{ color: "#8A9BAD" }}>
          LangGraph · 史料 {health.retrieval?.corpus_size ?? 0} 条 · 出图通道 {active.join(" / ") || "无"}
        </span>
        <span style={{ color: color }}>{ok ? "运行正常" : `降级 ${degraded.length} 项`}</span>
        <span style={{ marginLeft: "auto" }}>{open ? "收起" : "详情"}</span>
      </button>
      {open && (
        <div style={{ padding: "0 20px 10px", maxWidth: 760, margin: "0 auto" }}>
          {ok ? (
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: MUTED, margin: 0 }}>
              所有依赖均已配置。
            </p>
          ) : (
            degraded.map((item) => (
              <p
                key={item}
                style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: MUTED, margin: "0 0 4px", lineHeight: 1.7 }}
              >
                · {item}
              </p>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function Notice({
  tone,
  icon,
  title,
  text,
}: {
  tone: "error" | "warn";
  icon: ReactNode;
  title: string;
  text: string;
}) {
  const color = tone === "error" ? "#D44545" : GOLD;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        background: `${color}12`,
        borderBottom: `1px solid ${color}40`,
        padding: "10px 20px",
        flexShrink: 0,
      }}
    >
      <span style={{ marginTop: 1, flexShrink: 0 }}>{icon}</span>
      <div style={{ maxWidth: 760 }}>
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#C5C6C7", margin: "0 0 3px" }}>
          {title}
        </p>
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: MUTED, margin: 0, lineHeight: 1.7 }}>
          {text}
        </p>
      </div>
    </div>
  );
}

export default AIPage;
