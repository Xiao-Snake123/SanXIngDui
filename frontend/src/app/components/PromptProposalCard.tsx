import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Check,
  ChevronDown,
  Copy,
  Info,
  Pencil,
  Ruler,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import type { ChatProposal } from "@/services/agentApi";

const GOLD = "#D4AF37";
const TEAL = "#45A29E";
const MUTED = "#556372";
const RED = "#D44545";

export interface PromptProposalCardProps {
  proposal: ChatProposal;
  index: number;
  selected: boolean;
  generating: boolean;
  onGenerate: (proposal: ChatProposal, editedPrompt: string) => void;
}

/**
 * 提示词方案卡片。
 *
 * 三块信息缺一不可，这是刻意的产品取舍：
 * 1. **angle**（这是什么角度）—— 一眼看清 AI 这次选了哪个视觉角度；
 * 2. **rationale**（为什么这么选）—— 决策由 AI 做，但用户要有否决权，就得知道依据；
 * 3. **risk**（可能出什么问题）—— 把已知风险前置，比事后解释更有用。
 *
 * 现在通常只渲染一张卡（AI 自己拍板一个角度，不再摆四个让用户挑），
 * 但组件仍按"可能有多张"实现，以便将来重新提供对比视图。
 *
 * 提示词本身默认折叠，但**可编辑**：允许用户微调后再生成，
 * 生成时把编辑后的版本作为 `prompt_override` 发出去。
 */
export function PromptProposalCard({
  proposal,
  index,
  selected,
  generating,
  onGenerate,
}: PromptProposalCardProps) {
  const [showPrompt, setShowPrompt] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(proposal.prompt);
  const [copied, setCopied] = useState(false);

  // 方案对象被服务端替换（例如用户追加了一轮需求）时，重置本地草稿
  useEffect(() => {
    setDraft(proposal.prompt);
    setEditing(false);
  }, [proposal.id, proposal.prompt]);

  const edited = draft.trim() !== proposal.prompt.trim();

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.06 }}
      style={{
        background: selected ? "rgba(212,175,55,0.05)" : "rgba(0,0,0,0.22)",
        border: `1px solid ${selected ? "rgba(212,175,55,0.45)" : "rgba(255,255,255,0.07)"}`,
        borderRadius: 10,
        padding: 14,
        display: "flex",
        flexDirection: "column",
        gap: 9,
      }}
    >
      {/* 标题行 */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span
          style={{
            fontFamily: "monospace",
            fontSize: 10,
            color: selected ? GOLD : MUTED,
            border: `1px solid ${selected ? `${GOLD}55` : "rgba(255,255,255,0.1)"}`,
            borderRadius: 4,
            padding: "0 5px",
            lineHeight: "16px",
          }}
        >
          {String.fromCharCode(65 + index)}
        </span>
        <span style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 14, fontWeight: 700, color: "#EFEFEF" }}>
          {proposal.title}
        </span>
        {proposal.recommended && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 3, fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: GOLD, border: `1px solid ${GOLD}55`, borderRadius: 4, padding: "0 5px", lineHeight: "15px" }}>
            <Sparkles size={9} />AI 推荐
          </span>
        )}
        {selected && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 3, fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: GOLD }}>
            <Check size={10} />已选
          </span>
        )}
        {proposal.generated_by === "rule+llm" && (
          <span style={{ fontFamily: "monospace", fontSize: 9.5, color: TEAL, marginLeft: "auto" }}>
            模型润色
          </span>
        )}
      </div>

      <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11.5, color: TEAL, margin: 0, lineHeight: 1.7 }}>
        {proposal.angle}
      </p>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <Chip text={proposal.style_label} />
        {proposal.tags.map((tag) => (
          <Chip key={tag} text={tag} muted />
        ))}
      </div>

      {/* 为什么推荐 */}
      <div style={{ display: "flex", gap: 7, alignItems: "flex-start" }}>
        <Info size={11} color={TEAL} style={{ marginTop: 4, flexShrink: 0 }} />
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11.5, color: "#A9B4BF", margin: 0, lineHeight: 1.8 }}>
          {proposal.rationale}
        </p>
      </div>

      {/* 已知风险 */}
      <div style={{ display: "flex", gap: 7, alignItems: "flex-start" }}>
        <TriangleAlert size={11} color={GOLD} style={{ marginTop: 4, flexShrink: 0 }} />
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: MUTED, margin: 0, lineHeight: 1.8 }}>
          {proposal.risk}
        </p>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <Ruler size={10} color={MUTED} />
        {/* 只展示真实生效的参数。
            原先这里是「steps 32 · cfg 6 · LoRA 0.85」——那是扩散采样器的旋钮，
            只有 ComfyUI 链路才消费；改成 API 出图后一个都不会被下发，
            界面照旧显示就等于拿假参数误导用户。 */}
        <span style={{ fontFamily: "monospace", fontSize: 10, color: MUTED }}>
          {proposal.params.width}×{proposal.params.height}
          {proposal.params.style_strength !== null &&
            ` · 风格强度 ${Math.round(proposal.params.style_strength * 100)}%`}
          {" · 提示词增强 "}
          {proposal.params.prompt_extend ? "开" : "关"}
        </span>
      </div>

      {/* 提示词（可折叠 + 可编辑） */}
      <button
        onClick={() => setShowPrompt((value) => !value)}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 5,
          alignSelf: "flex-start",
        }}
      >
        <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10.5, color: TEAL }}>
          {showPrompt ? "收起提示词" : "查看完整提示词"}
        </span>
        <ChevronDown
          size={11}
          color={TEAL}
          style={{ transform: showPrompt ? "rotate(180deg)" : "none", transition: "transform .2s" }}
        />
      </button>

      <AnimatePresence initial={false}>
        {showPrompt && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            style={{ overflow: "hidden" }}
          >
            <div
              style={{
                background: "rgba(0,0,0,0.35)",
                border: `1px solid ${edited ? "rgba(212,175,55,0.35)" : "rgba(255,255,255,0.06)"}`,
                borderRadius: 7,
                padding: "9px 11px",
              }}
            >
              {editing ? (
                <textarea
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  rows={7}
                  style={{
                    width: "100%",
                    background: "transparent",
                    border: "none",
                    outline: "none",
                    resize: "vertical",
                    fontFamily: "monospace",
                    fontSize: 11,
                    color: "#C5C6C7",
                    lineHeight: 1.75,
                  }}
                />
              ) : (
                <p style={{ fontFamily: "monospace", fontSize: 10.5, color: MUTED, margin: 0, lineHeight: 1.8, maxHeight: 150, overflowY: "auto" }}>
                  {draft}
                </p>
              )}
            </div>

            <div style={{ display: "flex", gap: 6, marginTop: 7, flexWrap: "wrap" }}>
              <MiniButton
                icon={<Pencil size={10} />}
                label={editing ? "完成编辑" : "编辑提示词"}
                onClick={() => setEditing((value) => !value)}
              />
              <MiniButton
                icon={copied ? <Check size={10} /> : <Copy size={10} />}
                label={copied ? "已复制" : "复制"}
                onClick={handleCopy}
              />
              {edited && (
                <MiniButton
                  icon={<TriangleAlert size={10} />}
                  label="还原为推荐版本"
                  onClick={() => {
                    setDraft(proposal.prompt);
                    setEditing(false);
                  }}
                />
              )}
            </div>

            {proposal.negative_prompt && (
              <p style={{ fontFamily: "monospace", fontSize: 10, color: "#3a4550", margin: "8px 0 0", lineHeight: 1.7 }}>
                负向词：{proposal.negative_prompt.slice(0, 200)}
                {proposal.negative_prompt.length > 200 ? "…" : ""}
              </p>
            )}
            {proposal.evidence_ids.length > 0 && (
              <p style={{ fontFamily: "monospace", fontSize: 10, color: "#3a4550", margin: "5px 0 0" }}>
                依据史料：{proposal.evidence_ids.join("、")}
              </p>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      <button
        onClick={() => onGenerate(proposal, draft)}
        disabled={generating}
        style={{
          marginTop: 2,
          padding: "9px 12px",
          borderRadius: 8,
          border: "none",
          cursor: generating ? "not-allowed" : "pointer",
          background: generating
            ? "rgba(69,162,158,0.18)"
            : `linear-gradient(135deg, ${GOLD}, #a8861e)`,
          color: generating ? TEAL : "#0B0C10",
          fontFamily: "'Noto Serif SC', serif",
          fontSize: 12.5,
          fontWeight: 700,
          letterSpacing: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 6,
        }}
      >
        <Sparkles size={12} />
        {edited ? "用编辑后的提示词生成" : "用这个方案生成"}
      </button>
    </motion.div>
  );
}

function Chip({ text, muted }: { text: string; muted?: boolean }) {
  return (
    <span
      style={{
        fontFamily: "'Noto Sans SC', sans-serif",
        fontSize: 10,
        color: muted ? MUTED : TEAL,
        border: `1px solid ${muted ? "rgba(255,255,255,0.08)" : "rgba(69,162,158,0.28)"}`,
        borderRadius: 4,
        padding: "1px 6px",
        lineHeight: "15px",
      }}
    >
      {text}
    </span>
  );
}

function MiniButton({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "4px 9px",
        borderRadius: 6,
        border: "1px solid rgba(69,162,158,0.22)",
        background: "rgba(69,162,158,0.06)",
        color: TEAL,
        fontFamily: "'Noto Sans SC', sans-serif",
        fontSize: 10.5,
        cursor: "pointer",
      }}
    >
      {icon}
      {label}
    </button>
  );
}

export const PROPOSAL_PALETTE = { GOLD, TEAL, MUTED, RED };
