import { useMemo, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  Image as ImageIcon,
  Info,
  Library,
  PenLine,
  Route,
  ShieldCheck,
  TriangleAlert,
  XCircle,
} from "lucide-react";
import type { AgentStep, QaRecord } from "@/app/hooks/useAgentRestoration";
import type { EvidenceChunk, RestoreRequest } from "@/services/agentApi";

const GOLD = "#D4AF37";
const TEAL = "#45A29E";
const RED = "#D44545";
const MUTED = "#556372";
const PANEL = "#111820";

const NODE_ICONS: Record<string, typeof BrainCircuit> = {
  planner: BrainCircuit,
  supervisor: Route,
  retrieval: Library,
  restoration: ImageIcon,
  quality: ShieldCheck,
  copywriting: PenLine,
  finalize: CheckCircle2,
};

const TONE_COLOR: Record<AgentStep["tone"], string> = {
  info: TEAL,
  ok: "#6BAF8E",
  warn: GOLD,
  error: RED,
};

export interface AgentTracePanelProps {
  steps: AgentStep[];
  qaHistory: QaRecord[];
  warnings: string[];
  evidence: EvidenceChunk[];
  progress: number;
  running: boolean;
  engine: string | null;
  elapsedMs: number;
  taskId: string | null;
  goal?: string;
  request?: Partial<RestoreRequest>;
}

function formatMs(value?: number): string {
  if (value === undefined || value === null) return "";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`;
}

function StepRow({ step, isLast }: { step: AgentStep; isLast: boolean }) {
  const Icon = NODE_ICONS[step.node] ?? CircleDashed;
  const color = TONE_COLOR[step.tone];
  const roundLabel = step.round > 0 ? `第 ${step.round + 1} 轮` : "";

  return (
    <div style={{ display: "flex", gap: 12, position: "relative" }}>
      {/* 时间线主轴 */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: 22 }}>
        <div
          style={{
            width: 22,
            height: 22,
            borderRadius: "50%",
            border: `1px solid ${step.status === "running" ? color : "rgba(255,255,255,0.12)"}`,
            background: step.status === "running" ? `${color}22` : "rgba(255,255,255,0.03)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          {step.status === "running" ? (
            <motion.span
              animate={{ rotate: 360 }}
              transition={{ duration: 1.1, repeat: Infinity, ease: "linear" }}
              style={{
                width: 10,
                height: 10,
                borderRadius: "50%",
                border: `2px solid transparent`,
                borderTopColor: color,
                borderRightColor: `${color}55`,
                display: "block",
              }}
            />
          ) : step.status === "failed" ? (
            <XCircle size={12} color={RED} />
          ) : (
            <CheckCircle2 size={12} color={color} />
          )}
        </div>
        {!isLast && (
          <div style={{ flex: 1, width: 1, background: "rgba(255,255,255,0.08)", marginTop: 4 }} />
        )}
      </div>

      <div style={{ flex: 1, paddingBottom: 14, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 3 }}>
          <Icon size={12} color={color} />
          <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12.5, color: "#C5C6C7" }}>
            {step.label}
          </span>
          {roundLabel && (
            <span
              style={{
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 9.5,
                color: GOLD,
                border: `1px solid ${GOLD}44`,
                background: `${GOLD}12`,
                borderRadius: 4,
                padding: "0 5px",
                lineHeight: "15px",
              }}
            >
              {roundLabel}
            </span>
          )}
          {step.durationMs !== undefined && (
            <span style={{ fontFamily: "monospace", fontSize: 10, color: MUTED, marginLeft: "auto" }}>
              {formatMs(step.durationMs)}
            </span>
          )}
        </div>
        {step.summary && (
          <p
            style={{
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 11,
              color: MUTED,
              lineHeight: 1.65,
              margin: 0,
              wordBreak: "break-word",
            }}
          >
            {step.summary}
          </p>
        )}
      </div>
    </div>
  );
}

function QaCard({ record }: { record: QaRecord }) {
  const [open, setOpen] = useState(false);

  // 跳过质检（占位图不可判定）时没有分数、没有阈值可谈。
  // 单独给一张说明卡，而不是把 null 当 0 画一条「0 分」的进度条 ——
  // 那会让人以为模型画得很差，实际是根本没有可判定的对象。
  if (record.skipped) {
    return (
      <div
        style={{
          background: "rgba(0,0,0,0.28)",
          border: `1px solid ${TEAL}33`,
          borderRadius: 8,
          padding: "10px 12px",
          marginBottom: 8,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Info size={13} color={TEAL} />
          <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11.5, color: "#C5C6C7" }}>
            第 {record.round + 1} 轮质检
          </span>
          <span
            style={{
              fontFamily: "monospace",
              fontSize: 11,
              color: TEAL,
              border: `1px solid ${TEAL}44`,
              borderRadius: 4,
              padding: "0 5px",
              lineHeight: "16px",
            }}
          >
            未质检
          </span>
        </div>
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: MUTED, margin: "7px 0 0", lineHeight: 1.75 }}>
          {record.reason || "本次输出不是模型生成图，无法进行风格一致性质检。"}
        </p>
      </div>
    );
  }

  const score = record.score ?? 0;
  const threshold = record.threshold ?? 0;
  const ratio = Math.max(0, Math.min(1, score));
  const color = record.passed ? "#6BAF8E" : score >= threshold * 0.85 ? GOLD : RED;
  const decisionLabel =
    record.decision === "accept" ? "通过" : record.decision === "revise" ? "回炉重做" : "停止回炉";

  return (
    <div
      style={{
        background: "rgba(0,0,0,0.28)",
        border: `1px solid ${color}33`,
        borderRadius: 8,
        padding: "10px 12px",
        marginBottom: 8,
      }}
    >
      <div
        style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
        onClick={() => setOpen((value) => !value)}
      >
        <ShieldCheck size={13} color={color} />
        <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11.5, color: "#C5C6C7" }}>
          第 {record.round + 1} 轮质检
        </span>
        <span
          style={{
            fontFamily: "monospace",
            fontSize: 11,
            color,
            border: `1px solid ${color}44`,
            borderRadius: 4,
            padding: "0 5px",
            lineHeight: "16px",
          }}
        >
          {decisionLabel}
        </span>
        <span style={{ fontFamily: "monospace", fontSize: 11, color: MUTED, marginLeft: "auto" }}>
          {score.toFixed(3)} / {threshold.toFixed(2)}
        </span>
        <ChevronDown
          size={12}
          color={MUTED}
          style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .2s" }}
        />
      </div>

      {/* 得分条 + 阈值刻度 */}
      <div style={{ position: "relative", height: 4, background: "rgba(255,255,255,0.06)", borderRadius: 2, marginTop: 9 }}>
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${ratio * 100}%` }}
          transition={{ duration: 0.5 }}
          style={{ height: "100%", background: color, borderRadius: 2 }}
        />
        <div
          title={`通过阈值 ${threshold}`}
          style={{
            position: "absolute",
            left: `${threshold * 100}%`,
            top: -3,
            width: 1,
            height: 10,
            background: GOLD,
          }}
        />
      </div>

      <div style={{ display: "flex", gap: 10, marginTop: 7, flexWrap: "wrap" }}>
        <span style={{ fontFamily: "monospace", fontSize: 10, color: MUTED }}>
          客观指标 {(record.objectiveScore ?? 0).toFixed(3)}
        </span>
        <span style={{ fontFamily: "monospace", fontSize: 10, color: MUTED }}>
          VLM 裁判 {record.judgeScore === null ? "未启用" : record.judgeScore.toFixed(3)}
        </span>
        {record.degraded && (
          <span style={{ fontFamily: "monospace", fontSize: 10, color: GOLD }}>降级判定</span>
        )}
      </div>

      {/*
        把「被封顶」和「分数不够」分开说。
        没有这一行时，界面上只会并排出现「总分 0.40」和「客观指标 0.81」——
        看起来自相矛盾，实际是融合分 0.745 已经过线、被时代错配一票否决压了下来。
        这两种情况的处置完全不同：前者要修那一处错配，后者才需要重画或换模型。
      */}
      {record.cappedBy === "anachronism" && record.rawScore !== null && (
        <p
          style={{
            fontFamily: "'Noto Sans SC', sans-serif",
            fontSize: 10.5,
            color: GOLD,
            margin: "7px 0 0",
            lineHeight: 1.7,
            padding: "5px 8px",
            background: "rgba(212,175,55,0.07)",
            borderLeft: `2px solid ${GOLD}`,
            borderRadius: "0 4px 4px 0",
          }}
        >
          融合分 {record.rawScore.toFixed(3)} 本已
          {record.rawScore >= threshold ? "越过" : "低于"}阈值 {threshold.toFixed(2)}，
          因时代错配被一票否决封顶至 {score.toFixed(3)}。
          {record.rawScore >= threshold && " 除该处错配外，其余维度均已达标。"}
        </p>
      )}

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            style={{ overflow: "hidden" }}
          >
            {record.anachronisms.length > 0 && (
              <div style={{ marginTop: 9 }}>
                <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10.5, color: RED, margin: "0 0 4px" }}>
                  ⚠ 时代错配（一票否决）
                </p>
                <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10.5, color: MUTED, margin: 0, lineHeight: 1.7 }}>
                  {record.anachronisms.join("；")}
                </p>
              </div>
            )}
            {record.feedback.length > 0 && (
              <div style={{ marginTop: 9 }}>
                <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10.5, color: TEAL, margin: "0 0 4px" }}>
                  回炉修改指令
                </p>
                <ul style={{ margin: 0, paddingLeft: 15 }}>
                  {record.feedback.map((item, index) => (
                    <li
                      key={index}
                      style={{
                        fontFamily: "'Noto Sans SC', sans-serif",
                        fontSize: 10.5,
                        color: MUTED,
                        lineHeight: 1.75,
                      }}
                    >
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {record.reason && (
              <p
                style={{
                  fontFamily: "'Noto Sans SC', sans-serif",
                  fontSize: 10.5,
                  color: MUTED,
                  margin: "9px 0 0",
                  lineHeight: 1.7,
                }}
              >
                决策依据：{record.reason}
              </p>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function AgentTracePanel(props: AgentTracePanelProps) {
  const { steps, qaHistory, warnings, evidence, progress, running, engine, elapsedMs, taskId, goal } = props;
  const [showEvidence, setShowEvidence] = useState(false);

  const engineLabel = engine === "langgraph" ? "LangGraph" : engine === "builtin" ? "内置调度器" : "—";
  const activeStep = useMemo(() => [...steps].reverse().find((step) => step.status === "running"), [steps]);

  return (
    <div
      style={{
        background: PANEL,
        border: "1px solid rgba(69,162,158,0.14)",
        borderRadius: 12,
        padding: 16,
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      {/* 头部 */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <BrainCircuit size={14} color={GOLD} />
        <span style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 13, fontWeight: 700, color: "#EFEFEF" }}>
          多智能体执行轨迹
        </span>
        <span
          style={{
            fontFamily: "monospace",
            fontSize: 9.5,
            color: TEAL,
            border: `1px solid ${TEAL}44`,
            borderRadius: 4,
            padding: "1px 6px",
          }}
        >
          {engineLabel}
        </span>
        {running && activeStep && (
          <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10.5, color: GOLD }}>
            {activeStep.label} 执行中…
          </span>
        )}
        <span style={{ fontFamily: "monospace", fontSize: 10, color: MUTED, marginLeft: "auto" }}>
          {elapsedMs > 0 ? `${(elapsedMs / 1000).toFixed(1)}s` : ""}
          {taskId ? ` · ${taskId}` : ""}
        </span>
      </div>

      {/* 进度 */}
      <div style={{ height: 3, background: "rgba(255,255,255,0.06)", borderRadius: 2 }}>
        <motion.div
          animate={{ width: `${progress}%` }}
          transition={{ duration: 0.35 }}
          style={{ height: "100%", borderRadius: 2, background: `linear-gradient(90deg, ${TEAL}, ${GOLD})` }}
        />
      </div>

      {goal && (
        <p
          style={{
            fontFamily: "'Noto Sans SC', sans-serif",
            fontSize: 11,
            color: "#8A9BAD",
            margin: 0,
            lineHeight: 1.7,
            borderLeft: `2px solid ${GOLD}66`,
            paddingLeft: 9,
          }}
        >
          {goal}
        </p>
      )}

      {/* 步骤时间线 */}
      <div style={{ maxHeight: 260, overflowY: "auto", paddingRight: 4 }}>
        {steps.length === 0 ? (
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: MUTED, margin: 0 }}>
            尚未开始。点击「开始复原」后，这里会实时显示每个 Agent 的执行情况。
          </p>
        ) : (
          steps.map((step, index) => (
            <StepRow key={step.id} step={step} isLast={index === steps.length - 1} />
          ))
        )}
      </div>

      {/* 质检记录 */}
      {qaHistory.length > 0 && (
        <div>
          <p
            style={{
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 10,
              color: TEAL,
              letterSpacing: 2,
              margin: "0 0 7px",
            }}
          >
            ● 风格一致性质检（Self-Correction）
          </p>
          {qaHistory.map((record) => (
            <QaCard key={record.round} record={record} />
          ))}
        </div>
      )}

      {/* 史料证据 */}
      {evidence.length > 0 && (
        <div>
          <button
            onClick={() => setShowEvidence((value) => !value)}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 6,
              marginBottom: 7,
            }}
          >
            <Library size={11} color={TEAL} />
            <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: TEAL, letterSpacing: 2 }}>
              ● 检索到的史料依据（{evidence.length}）
            </span>
            <ChevronDown
              size={11}
              color={MUTED}
              style={{ transform: showEvidence ? "rotate(180deg)" : "none", transition: "transform .2s" }}
            />
          </button>
          <AnimatePresence initial={false}>
            {showEvidence && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                style={{ overflow: "hidden" }}
              >
                {evidence.slice(0, 5).map((chunk) => (
                  <div
                    key={chunk.doc_id}
                    style={{
                      borderLeft: "2px solid rgba(69,162,158,0.3)",
                      paddingLeft: 9,
                      marginBottom: 9,
                    }}
                  >
                    <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#C5C6C7", margin: "0 0 2px" }}>
                      {chunk.title}
                    </p>
                    <p
                      style={{
                        fontFamily: "'Noto Sans SC', sans-serif",
                        fontSize: 10.5,
                        color: MUTED,
                        margin: 0,
                        lineHeight: 1.7,
                      }}
                    >
                      {chunk.text.slice(0, 110)}…
                    </p>
                    <p style={{ fontFamily: "monospace", fontSize: 9.5, color: "#3a4550", margin: "3px 0 0" }}>
                      {chunk.source} · 融合分 {chunk.scores.final.toFixed(3)} · {chunk.channels.join("+")}
                    </p>
                  </div>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* 降级 / 异常告警 */}
      {warnings.length > 0 && (
        <div
          style={{
            background: "rgba(212,175,55,0.06)",
            border: `1px solid ${GOLD}22`,
            borderRadius: 8,
            padding: "9px 11px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 5 }}>
            <TriangleAlert size={11} color={GOLD} />
            <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: GOLD, letterSpacing: 1 }}>
              ● 降级与告警（{warnings.length}）
            </span>
          </div>
          {warnings.slice(-4).map((warning, index) => (
            <p
              key={index}
              style={{
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 10.5,
                color: MUTED,
                margin: "0 0 3px",
                lineHeight: 1.65,
              }}
            >
              {warning}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
