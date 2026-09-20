import { useState, type ReactNode } from "react";
import { motion } from "motion/react";
import {
  ChevronDown,
  Download,
  Info,
  Link2,
  Lock,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Square,
  TriangleAlert,
} from "lucide-react";
import { AgentTracePanel } from "@/app/components/AgentTracePanel";
import type { AgentStep, QaRecord, RoundImage } from "@/app/hooks/useAgentRestoration";
import type { CopyPayload, EvidenceChunk, QaVerdict } from "@/services/agentApi";

const GOLD = "#D4AF37";
const TEAL = "#45A29E";
const MUTED = "#556372";
const RED = "#D9705F";
const GREEN = "#6BAF8E";
const CARD = "rgba(255,255,255,0.025)";

export interface ArtifactPayload {
  running: boolean;
  progress: number;
  /** 正在跑的阶段名（如「图像修复 Worker」） */
  stageLabel: string;
  steps: AgentStep[];
  qaHistory: QaRecord[];
  imageUrl: string | null;
  /** 每一轮的出图（URL 已解析为浏览器可达地址） */
  roundImages: RoundImage[];
  /** 最后一轮的轮次号，用来在画廊里标出「最终」 */
  finalRound: number;
  provider: string;
  isPlaceholder: boolean;
  imageError: string;
  qa: QaVerdict | null;
  revisions: number;
  copy: CopyPayload | null;
  evidence: EvidenceChunk[];
  /** 用户选定的提示词原文（锁定后不再改写） */
  lockedPrompt: string | null;
  proposalTitle: string;
  taskId: string | null;
  durationMs: number;
  /** 本次运行是被用户主动停止的（不是失败，也不是跑完） */
  stopped: boolean;
  /** 中断本次复原。已生成的图与史料会保留。 */
  onStop: () => void;
  onClear: () => void;
}

/**
 * 出图结果的「对话内附件」。
 *
 * 为什么不是右侧独立画布：
 * 一次出图和产生它的那次对话是同一件事的两个阶段。放在右栏会让人以为
 * 「左边是聊天、右边是另一个工具」，而实际上右边的图完全依赖左边选定的提示词。
 * 按 ChatGPT 的做法内联到消息流里，因果一眼可见。
 *
 * 折叠策略：过程信息（执行轨迹）默认收起，成品（图 + 质检结论 + 文案）默认展开。
 * 因为用户先要看到「画出来了什么」，再决定要不要追究「怎么画出来的」。
 */
/**
 * 各轮出图。
 *
 * 为什么每一轮都要显示：回炉的每一次都会重画，而「这一轮改成了什么样」是判断
 * 「回炉到底有没有用」的唯一依据。只给最终一张，用户无法分辨前面几轮是逐步好转、
 * 还是原地打转 —— 实测就出现过连出四轮、分数纹丝不动（都封顶在 0.40）的情况，
 * 而界面上完全看不出来。
 *
 * 所以每一格都配上该轮的质检结论：图和分放在一起，才看得出「分是从哪一轮开始不动的」。
 * 跳过质检的轮次只标「未质检」，不给分数 —— 用 0 冒充等于伪造结论。
 */
function RoundGallery({
  images,
  qaHistory,
  finalRound,
}: {
  images: RoundImage[];
  qaHistory: QaRecord[];
  finalRound: number;
}) {
  return (
    <div style={{ marginTop: 12 }}>
      <p
        style={{
          fontFamily: "'Noto Sans SC', sans-serif",
          fontSize: 11,
          color: MUTED,
          margin: "0 0 7px",
        }}
      >
        各轮出图（{images.length} 轮）
        {images.length > 1 && " · 点开对比，可以看出回炉是否真的改变了画面"}
      </p>
      <div style={{ display: "flex", gap: 8, overflowX: "auto", paddingBottom: 6 }}>
        {images.map((image) => {
          const record = qaHistory.find((item) => item.round === image.round);
          const isFinal = image.round === finalRound;
          const scoreColor = record?.skipped
            ? MUTED
            : record?.passed
              ? GREEN
              : record?.cappedBy
                ? GOLD
                : RED;
          return (
            <div
              key={`${image.round}-${image.url}`}
              style={{
                flex: "0 0 132px",
                border: `1px solid ${isFinal ? `${GOLD}66` : "rgba(255,255,255,0.08)"}`,
                borderRadius: 9,
                overflow: "hidden",
                background: "#0F1218",
              }}
            >
              <img
                src={image.url}
                alt={`第 ${image.round + 1} 轮出图`}
                style={{ width: "100%", aspectRatio: "1 / 1", objectFit: "cover", display: "block" }}
              />
              <div style={{ padding: "5px 7px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <span
                    style={{
                      fontFamily: "'Noto Sans SC', sans-serif",
                      fontSize: 10.5,
                      color: isFinal ? GOLD : MUTED,
                    }}
                  >
                    第 {image.round + 1} 轮{isFinal ? " · 最终" : ""}
                  </span>
                  {record && !record.skipped && record.score !== null && (
                    <span
                      style={{
                        marginLeft: "auto",
                        fontFamily: "monospace",
                        fontSize: 10,
                        color: scoreColor,
                      }}
                    >
                      {record.score.toFixed(2)}
                    </span>
                  )}
                  {record?.skipped && (
                    <span style={{ marginLeft: "auto", fontSize: 9.5, color: MUTED }}>未质检</span>
                  )}
                </div>
                {record?.cappedBy && (
                  <p style={{ fontSize: 9.5, color: GOLD, margin: "3px 0 0", lineHeight: 1.5 }}>
                    因时代错配封顶
                  </p>
                )}
                {image.degraded && !record?.skipped && (
                  <p style={{ fontSize: 9.5, color: MUTED, margin: "3px 0 0" }}>降级通道</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function ArtifactView({ artifact }: { artifact: ArtifactPayload }) {
  const [traceOpen, setTraceOpen] = useState(false);
  const placeholder = artifact.isPlaceholder;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 10 }}
    >
      {/* 出图进行中 */}
      {artifact.running && !artifact.imageUrl && (
        <div
          style={{
            border: "1px solid rgba(212,175,55,0.18)",
            borderRadius: 12,
            padding: "14px 16px",
            background: CARD,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 10 }}>
            <motion.span
              animate={{ rotate: 360 }}
              transition={{ duration: 1.6, repeat: Infinity, ease: "linear" }}
              style={{ display: "inline-flex" }}
            >
              <Sparkles size={13} color={GOLD} />
            </motion.span>
            <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12.5, color: "#DDE3EA" }}>
              {artifact.stageLabel || "多智能体协作中…"}
            </span>
            <span style={{ marginLeft: "auto", fontFamily: "monospace", fontSize: 11, color: GOLD }}>
              {Math.round(artifact.progress)}%
            </span>
            {/*
              停止入口。复原任务实测可以跑三分钟以上（多轮回炉 + 每轮质检），
              期间没有任何中断手段 —— 用户只能干等，或者刷掉页面（那会连
              已经跑出来的部分一起丢掉）。放在进度条同一行、常驻可见。
            */}
            <button
              onClick={artifact.onStop}
              title="中断本次复原。已经生成的图与史料都会保留。"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 4,
                background: "rgba(217,112,95,0.10)",
                border: `1px solid ${RED}55`,
                borderRadius: 6,
                padding: "3px 9px",
                color: RED,
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 11,
                cursor: "pointer",
                flexShrink: 0,
              }}
            >
              <Square size={9} fill={RED} />
              停止
            </button>
          </div>
          <div style={{ height: 3, background: "rgba(255,255,255,0.06)", borderRadius: 2 }}>
            <motion.div
              animate={{ width: `${artifact.progress}%` }}
              transition={{ duration: 0.4 }}
              style={{ height: "100%", background: `linear-gradient(90deg, ${TEAL}, ${GOLD})`, borderRadius: 2 }}
            />
          </div>
          {/* 实时节点流：让「多智能体」这件事在等待时也看得见 */}
          <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6 }}>
            {artifact.steps.map((step) => (
              <span
                key={step.id}
                style={{
                  fontFamily: "'Noto Sans SC', sans-serif",
                  fontSize: 10,
                  padding: "2px 8px",
                  borderRadius: 20,
                  border: `1px solid ${step.status === "running" ? "rgba(212,175,55,0.4)" : "rgba(255,255,255,0.07)"}`,
                  color: step.status === "running" ? GOLD : MUTED,
                }}
              >
                {step.label}
                {step.durationMs ? ` · ${Math.round(step.durationMs)}ms` : ""}
              </span>
            ))}
          </div>
        </div>
      )}

      {/*
        被用户停止：如实说清「停在哪儿、留下了什么」。
        既不能伪装成跑完（那会让人以为这就是最终结果），也不能渲染成一次事故
        （停止是用户的意图，不是故障）。
      */}
      {artifact.stopped && !artifact.running && (
        <div
          style={{
            border: "1px solid rgba(138,155,173,0.28)",
            borderRadius: 10,
            padding: "10px 13px",
            background: "rgba(138,155,173,0.06)",
            display: "flex",
            gap: 8,
          }}
        >
          <Info size={14} color="#8A9BAD" style={{ flexShrink: 0, marginTop: 1 }} />
          <div>
            <p
              style={{
                fontFamily: "'Noto Serif SC', serif",
                fontSize: 12,
                color: "#C5C6C7",
                margin: "0 0 3px",
              }}
            >
              已按你的要求中断
            </p>
            <p
              style={{
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 11,
                color: MUTED,
                lineHeight: 1.7,
                margin: 0,
              }}
            >
              未跑完的步骤不会再继续。
              {artifact.roundImages.length > 0
                ? `已经生成的 ${artifact.roundImages.length} 轮图与命中的史料都保留在上面。`
                : "本次还没有产出图像。"}
              想重新开始就再发一次请求。
            </p>
          </div>
        </div>
      )}

      {/* 成品图 */}
      {artifact.imageUrl && (
        <div
          style={{
            border: "1px solid rgba(255,255,255,0.07)",
            borderRadius: 12,
            overflow: "hidden",
            position: "relative",
            background: "#0F1218",
          }}
        >
          <img
            src={artifact.imageUrl}
            alt={artifact.proposalTitle || "复原结果"}
            style={{ width: "100%", display: "block" }}
          />

          <div style={{ position: "absolute", top: 10, left: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
            <QaBadge qa={artifact.qa} />
            {artifact.provider && (
              <Chip tone="info" text={placeholder ? "占位图" : describeProvider(artifact.provider)} />
            )}
            {artifact.revisions > 0 && <Chip tone="gold" text={`回炉 ${artifact.revisions} 次`} />}
          </div>

          {placeholder && (
            /* 占位图必须被大声指出来：它是一张程序画的示意图，
               不说清楚就会被读成「生图逻辑坏了 / 画得跟三星堆无关」。 */
            <div
              style={{
                margin: 12,
                padding: "11px 13px",
                borderRadius: 10,
                background: "rgba(120,53,15,0.92)",
                border: `1px solid ${GOLD}`,
              }}
            >
              <div style={{ display: "flex", gap: 8 }}>
                <TriangleAlert size={15} color="#fde68a" style={{ flexShrink: 0, marginTop: 1 }} />
                <div>
                  <p style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 12.5, color: "#fde68a", margin: "0 0 4px" }}>
                    这不是生成图，是占位示意图
                  </p>
                  <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "rgba(255,255,255,0.85)", lineHeight: 1.7, margin: 0 }}>
                    所有真实出图通道本次都没成功，系统按设计降级为本地示意图（与文物无关）。
                    {/*
                      这里不写「解决方式：填入 DASHSCOPE_API_KEY」。
                      真实原因可能是限流、网络、内容审核——而 Key 常常是配好的。
                      曾经那句写死的劝告会把用户引去翻 .env 找一个根本不缺的东西。
                      真正的原因现在由后端逐条透传到 imageError 里，直接显示它。
                    */}
                    {artifact.imageError ? `上游返回：${artifact.imageError}` : ""}
                    <br />
                    若上面的原因指向「通道未配置」，在 backend/.env 填入 DASHSCOPE_API_KEY 即可恢复真实出图。
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 各轮出图 —— 回炉每重画一次都是一张新图，每一轮都要能看到 */}
      {artifact.roundImages.length > 0 && (
        <RoundGallery
          images={artifact.roundImages}
          qaHistory={artifact.qaHistory}
          finalRound={artifact.finalRound}
        />
      )}

      {/* 质检结论（可展开细节） */}
      {artifact.qa && <QaSummary qa={artifact.qa} />}

      {/* 提示词锁定说明 —— 这是「确定性出图」对用户的承诺，必须可见 */}
      {artifact.lockedPrompt && (
        <details
          style={{
            border: "1px solid rgba(69,162,158,0.18)",
            borderRadius: 10,
            background: "rgba(69,162,158,0.04)",
            padding: "9px 12px",
          }}
        >
          <summary
            style={{
              cursor: "pointer",
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 11,
              color: TEAL,
              display: "flex",
              alignItems: "center",
              gap: 6,
              listStyle: "none",
            }}
          >
            <Lock size={11} />
            本次出图已锁定为你选定的提示词
            <span style={{ color: MUTED, fontSize: 10 }}>（展开查看原文）</span>
          </summary>
          <p
            style={{
              fontFamily: "ui-monospace, monospace",
              fontSize: 10.5,
              color: "#8C97A3",
              lineHeight: 1.75,
              margin: "8px 0 0",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {artifact.lockedPrompt}
          </p>
        </details>
      )}

      {/* 执行轨迹：默认收起 */}
      <div>
        <button
          onClick={() => setTraceOpen((open) => !open)}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "5px 11px",
            borderRadius: 20,
            border: "1px solid rgba(255,255,255,0.08)",
            background: "transparent",
            color: MUTED,
            fontFamily: "'Noto Sans SC', sans-serif",
            fontSize: 11,
            cursor: "pointer",
          }}
        >
          <ChevronDown
            size={12}
            style={{ transform: traceOpen ? "rotate(180deg)" : "none", transition: "transform 0.18s" }}
          />
          多智能体执行轨迹
          <span style={{ color: "#3a4550" }}>
            {artifact.steps.length} 个节点
            {artifact.taskId ? ` · ${artifact.taskId}` : ""}
          </span>
        </button>
        {traceOpen && (
          <div style={{ marginTop: 10 }}>
            <AgentTracePanel
              steps={artifact.steps}
              qaHistory={artifact.qaHistory}
              warnings={[]}
              evidence={artifact.evidence}
              progress={artifact.progress}
              running={artifact.running}
              engine={null}
              taskId={artifact.taskId}
              elapsedMs={artifact.durationMs}
            />
          </div>
        )}
      </div>

      {/* 科普文案 */}
      {artifact.copy && <CopyCard copy={artifact.copy} />}

      <div style={{ display: "flex", gap: 7 }}>
        <MiniButton icon={<RotateCcw size={11} />} text="清空本次结果" onClick={artifact.onClear} />
        {artifact.imageUrl && (
          <MiniButton
            icon={<Download size={11} />}
            text="下载"
            onClick={() => {
              const link = document.createElement("a");
              link.href = artifact.imageUrl as string;
              link.download = `${artifact.proposalTitle || "sanxingdui"}.png`;
              link.click();
            }}
          />
        )}
        {artifact.imageUrl && (
          <MiniButton
            icon={<Link2 size={11} />}
            text="复制链接"
            onClick={() => void navigator.clipboard.writeText(artifact.imageUrl as string)}
          />
        )}
      </div>
    </motion.div>
  );
}

function describeProvider(provider: string): string {
  if (provider.includes("third-party")) return "第三方通道";
  if (provider.includes("dashscope")) return "千问图像";
  return provider;
}

function QaBadge({ qa }: { qa: QaVerdict | null }) {
  if (!qa) return <Chip tone="info" text="未质检" />;
  if (qa.skipped) return <Chip tone="warn" text="未质检 · 不可判定" />;
  const score = Number(qa.score ?? 0).toFixed(2);
  return (
    <Chip
      tone={qa.passed ? "ok" : "warn"}
      icon={qa.passed ? <ShieldCheck size={10} /> : <ShieldAlert size={10} />}
      text={`${qa.passed ? "质检通过" : "质检未达标"} ${score}`}
    />
  );
}

function QaSummary({ qa }: { qa: QaVerdict }) {
  if (qa.skipped) {
    return (
      <div
        style={{
          border: "1px solid rgba(212,175,55,0.2)",
          borderRadius: 10,
          padding: "10px 12px",
          background: "rgba(212,175,55,0.04)",
        }}
      >
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11.5, color: GOLD, margin: "0 0 4px" }}>
          本次未质检
        </p>
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#8C97A3", lineHeight: 1.75, margin: 0 }}>
          {qa.skip_reason}
        </p>
      </div>
    );
  }

  const feedback = qa.feedback ?? [];
  const anachronisms = qa.anachronisms ?? [];
  return (
    <details
      style={{
        border: "1px solid rgba(255,255,255,0.07)",
        borderRadius: 10,
        padding: "9px 12px",
        background: CARD,
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          fontFamily: "'Noto Sans SC', sans-serif",
          fontSize: 11.5,
          color: "#C5C6C7",
          listStyle: "none",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <Info size={11} color={TEAL} />
        风格一致性质检
        <span style={{ color: MUTED, fontSize: 10 }}>
          客观指标 {qa.objective_score === null ? "—" : Number(qa.objective_score).toFixed(2)}
          {qa.judge_score === null ? " · VLM 裁判未启用" : ` · VLM ${Number(qa.judge_score).toFixed(2)}`}
        </span>
      </summary>
      <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 7 }}>
        {feedback.length === 0 && anachronisms.length === 0 && (
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: MUTED, margin: 0 }}>
            无待修正项。
          </p>
        )}
        {feedback.map((item) => (
          <p key={item} style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#8C97A3", lineHeight: 1.75, margin: 0 }}>
            · {item}
          </p>
        ))}
        {anachronisms.length > 0 && (
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#fca5a5", lineHeight: 1.75, margin: 0 }}>
            时代错配：{anachronisms.join("、")}
          </p>
        )}
      </div>
    </details>
  );
}

function CopyCard({ copy }: { copy: CopyPayload }) {
  const text = String(copy.text ?? "");
  return (
    <details
      style={{
        border: "1px solid rgba(255,255,255,0.07)",
        borderRadius: 10,
        padding: "9px 12px",
        background: CARD,
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          fontFamily: "'Noto Sans SC', sans-serif",
          fontSize: 11.5,
          color: "#C5C6C7",
          listStyle: "none",
        }}
      >
        科普文案 · {text.length} 字
        <span style={{ color: MUTED, fontSize: 10 }}>（{String(copy.mode ?? "")}）</span>
      </summary>
      <p
        style={{
          fontFamily: "'Noto Sans SC', sans-serif",
          fontSize: 11.5,
          color: "#8C97A3",
          lineHeight: 1.9,
          margin: "8px 0 0",
          whiteSpace: "pre-wrap",
        }}
      >
        {text}
      </p>
      {(copy.sources ?? []).length > 0 && (
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10.5, color: "#4b5563", margin: "8px 0 0" }}>
          引用：{(copy.sources ?? []).map((item) => `《${item.title}》`).join("、")}
        </p>
      )}
    </details>
  );
}

function Chip({
  tone,
  text,
  icon,
}: {
  tone: "ok" | "warn" | "info" | "gold";
  text: string;
  icon?: ReactNode;
}) {
  const palette = {
    ok: { border: "rgba(69,162,158,0.5)", color: "#7FD1CD" },
    warn: { border: "rgba(212,175,55,0.5)", color: GOLD },
    info: { border: "rgba(255,255,255,0.12)", color: "#9AA5B1" },
    gold: { border: "rgba(212,175,55,0.4)", color: GOLD },
  }[tone];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: 20,
        border: `1px solid ${palette.border}`,
        background: "rgba(11,12,16,0.72)",
        color: palette.color,
        fontFamily: "'Noto Sans SC', sans-serif",
        fontSize: 10,
        backdropFilter: "blur(4px)",
      }}
    >
      {icon}
      {text}
    </span>
  );
}

function MiniButton({ icon, text, onClick }: { icon: ReactNode; text: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        padding: "5px 10px",
        borderRadius: 7,
        border: "1px solid rgba(255,255,255,0.08)",
        background: "transparent",
        color: MUTED,
        fontFamily: "'Noto Sans SC', sans-serif",
        fontSize: 10.5,
        cursor: "pointer",
      }}
    >
      {icon}
      {text}
    </button>
  );
}
