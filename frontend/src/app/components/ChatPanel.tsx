import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUp, CornerDownLeft, Library, RefreshCw, Sparkles } from "lucide-react";
import { ArtifactView, type ArtifactPayload } from "@/app/components/ArtifactView";
import { PromptProposalCard } from "@/app/components/PromptProposalCard";
import type { ChatMessageView } from "@/app/hooks/useAgentChat";
import type { ChatIntent, ChatProposal } from "@/services/agentApi";

const GOLD = "#D4AF37";
const TEAL = "#45A29E";
const MUTED = "#556372";

/** 单列宽度。与 ChatGPT 一致：内容有最大宽度，不随窗口无限拉宽。 */
const COLUMN = 760;

export interface ChatPanelProps {
  messages: ChatMessageView[];
  sending: boolean;
  stage: string;
  sessionId: string | null;
  quickStarts: string[];
  generating: boolean;
  selectedProposalId: string | null;
  /** 已完成的出图结果，按「触发它的那条助手消息」归属 */
  artifactsByMessage: Record<string, ArtifactPayload>;
  greeting: string;
  fastMode: boolean;
  imageModel: string;
  onFastModeChange: (fast: boolean) => void;
  onImageModelChange: (model: string) => void;
  onSend: (text: string) => void;
  onReset: () => void;
  onGenerate: (
    proposal: ChatProposal,
    editedPrompt: string,
    intent: ChatIntent | null,
    messageId: string,
  ) => void;
}

/**
 * 单栏对话视图（ChatGPT 式）。
 *
 * 为什么改成单栏：原先的「左对话 / 右画布」把一件事拆成了两个区域，
 * 但右侧的图完全依赖左侧选定的提示词 —— 用户得在两个区域之间来回看才能建立因果。
 * 单栏把「说的话 → 给的方案 → 选的方案 → 出的图 → 质检结论」排成一条时间线，
 * 因果天然自明，也更接近人们已经熟悉的对话式交互。
 *
 * 三条体验规则：
 * 1. **即时反馈**：发送后立刻插入用户气泡与空的助手气泡，不等服务端；
 * 2. **过程可见**：`stage` 用一句人话说明当前在做什么，而不是转圈圈；
 * 3. **可回车发送**：Enter 发送、Shift+Enter 换行。
 */
export function ChatPanel({
  messages,
  sending,
  stage,
  sessionId,
  quickStarts,
  generating,
  selectedProposalId,
  artifactsByMessage,
  greeting,
  fastMode,
  imageModel,
  onFastModeChange,
  onImageModelChange,
  onSend,
  onReset,
  onGenerate,
}: ChatPanelProps) {
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending, artifactsByMessage]);

  const submit = () => {
    const text = draft.trim();
    if (!text || sending) return;
    onSend(text);
    setDraft("");
  };

  const empty = messages.length === 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      {/* 顶部：标题 + 新会话。不用侧边栏，保持单栏。 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "10px 20px",
          borderBottom: "1px solid rgba(255,255,255,0.05)",
          position: "relative",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Sparkles size={14} color={GOLD} />
          <span style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 13.5, fontWeight: 700, color: "#EFEFEF" }}>
            古蜀智脑
          </span>
          <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10.5, color: MUTED }}>
            三星堆复原多智能体
          </span>
        </div>
        <button
          onClick={onReset}
          style={{
            position: "absolute",
            right: 20,
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            padding: "5px 12px",
            borderRadius: 20,
            border: "1px solid rgba(255,255,255,0.1)",
            background: "rgba(255,255,255,0.03)",
            color: MUTED,
            fontFamily: "'Noto Sans SC', sans-serif",
            fontSize: 11,
            cursor: "pointer",
          }}
        >
          <RefreshCw size={11} />
          新会话
        </button>
      </div>

      {/* 消息流 */}
      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        {empty ? (
          /* 空状态：内容垂直居中，像 ChatGPT 的欢迎页 */
          <div
            style={{
              minHeight: "100%",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              padding: "40px 20px",
              gap: 22,
            }}
          >
            <p
              style={{
                fontFamily: "'Noto Serif SC', serif",
                fontSize: 26,
                color: "#EFEFEF",
                margin: 0,
                textAlign: "center",
                letterSpacing: 1,
              }}
            >
              {greeting}
            </p>
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 9,
                justifyContent: "center",
                maxWidth: COLUMN,
              }}
            >
              {quickStarts.map((text) => (
                <button
                  key={text}
                  onClick={() => onSend(text)}
                  style={{
                    padding: "9px 14px",
                    borderRadius: 22,
                    border: "1px solid rgba(212,175,55,0.22)",
                    background: "rgba(212,175,55,0.05)",
                    color: "#C5C6C7",
                    fontFamily: "'Noto Sans SC', sans-serif",
                    fontSize: 12,
                    cursor: "pointer",
                    textAlign: "left",
                    lineHeight: 1.6,
                    maxWidth: 340,
                  }}
                >
                  {text}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div style={{ maxWidth: COLUMN, margin: "0 auto", padding: "26px 20px 8px" }}>
            <AnimatePresence initial={false}>
              {messages.map((message) => {
                const artifact = artifactsByMessage[message.id];
                return message.role === "user" ? (
              <motion.div
                key={message.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                style={{ display: "flex", justifyContent: "flex-end", marginBottom: 14 }}
              >
                <div
                  style={{
                    maxWidth: "82%",
                    background: "rgba(69,162,158,0.14)",
                    border: "1px solid rgba(69,162,158,0.25)",
                    borderRadius: "12px 12px 3px 12px",
                    padding: "9px 13px",
                    fontFamily: "'Noto Sans SC', sans-serif",
                    fontSize: 12.5,
                    color: "#DDE3EA",
                    lineHeight: 1.8,
                  }}
                >
                  {message.content}
                </div>
              </motion.div>
            ) : (
              <motion.div
                key={message.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                style={{ display: "flex", gap: 9, marginBottom: 16 }}
              >
                <div
                  style={{
                    width: 26,
                    height: 26,
                    borderRadius: "50%",
                    flexShrink: 0,
                    background: "rgba(212,175,55,0.12)",
                    border: "1px solid rgba(212,175,55,0.3)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    marginTop: 2,
                  }}
                >
                  <Sparkles size={12} color={GOLD} />
                </div>

                <div style={{ flex: 1, minWidth: 0 }}>
                  {message.content ? (
                    <p
                      style={{
                        fontFamily: "'Noto Sans SC', sans-serif",
                        fontSize: 12.5,
                        color: "#C5C6C7",
                        lineHeight: 2,
                        margin: 0,
                        whiteSpace: "pre-wrap",
                      }}
                    >
                      {message.content}
                      {message.streaming && <Caret />}
                    </p>
                  ) : (
                    message.streaming && (
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <TypingDots />
                        <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11.5, color: MUTED }}>
                          {stage || "思考中…"}
                        </span>
                      </div>
                    )
                  )}

                  {/* 史料依据 */}
                  {message.evidence.length > 0 && (
                    <div style={{ marginTop: 10 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                        <Library size={11} color={TEAL} />
                        <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10.5, color: TEAL }}>
                          检索到 {message.evidence.length} 条史料依据
                        </span>
                      </div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                        {message.evidence.slice(0, 4).map((chunk) => (
                          <span
                            key={chunk.doc_id}
                            title={chunk.text.slice(0, 120)}
                            style={{
                              fontFamily: "'Noto Sans SC', sans-serif",
                              fontSize: 10,
                              color: MUTED,
                              border: "1px solid rgba(255,255,255,0.07)",
                              borderRadius: 4,
                              padding: "2px 7px",
                              lineHeight: "15px",
                            }}
                          >
                            《{chunk.title}》
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 提示词方案 */}
                  {message.proposals.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                      <p
                        style={{
                          fontFamily: "'Noto Sans SC', sans-serif",
                          fontSize: 10.5,
                          color: GOLD,
                          letterSpacing: 1.5,
                          margin: "0 0 8px",
                        }}
                      >
                        {(() => {
                          if (message.proposals.length === 1) {
                            return "◆ 我替你定了角度 · 可直接生成，也可以改提示词";
                          }
                          const recommended = message.proposals.find((p) => p.recommended);
                          const title = recommended?.title ?? message.proposals[0].title;
                          return `◆ AI 选定了「${title}」· 另给了 ${message.proposals.length - 1} 个方向，可直接生成或换选`;
                        })()}
                      </p>
                      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                        {message.proposals.map((proposal, index) => (
                          <PromptProposalCard
                            key={proposal.id}
                            proposal={proposal}
                            index={index}
                            selected={
                              selectedProposalId === proposal.id ||
                              (selectedProposalId === null && proposal.recommended === true)
                            }
                            generating={generating}
                            onGenerate={(item, editedPrompt) =>
                              onGenerate(item, editedPrompt, message.intent, message.id)
                            }
                          />
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 出图结果内联在同一条消息里：它和这次对话本就是一件事 */}
                  {artifact && <ArtifactView artifact={artifact} />}

                  {/* 追问建议 */}
                  {message.followups.length > 0 && !message.streaming && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 12 }}>
                      {message.followups.map((text) => (
                        <button
                          key={text}
                          onClick={() => onSend(text)}
                          style={{
                            padding: "4px 10px",
                            borderRadius: 20,
                            border: "1px dashed rgba(69,162,158,0.3)",
                            background: "transparent",
                            color: TEAL,
                            fontFamily: "'Noto Sans SC', sans-serif",
                            fontSize: 10.5,
                            cursor: "pointer",
                          }}
                        >
                          {text}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </motion.div>
                );
              })}
            </AnimatePresence>

            <div ref={bottomRef} style={{ height: 8 }} />
          </div>
        )}
      </div>

      {/* 输入区：固定在底部，胶囊形，居中限宽 */}
      <div style={{ padding: "10px 20px 14px" }}>
        <div style={{ maxWidth: COLUMN, margin: "0 auto" }}>
          {/* 模式 / 模型控制条：让用户自己决定「快」还是「精」、用哪个出图模型 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "flex-end",
              gap: 8,
              marginBottom: 8,
            }}
          >
            <div
              style={{
                display: "flex",
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: 16,
                overflow: "hidden",
                flexShrink: 0,
              }}
            >
              <button
                onClick={() => onFastModeChange(true)}
                title="快速模式：跳过质检回炉，首图更快"
                aria-pressed={fastMode}
                style={{
                  padding: "4px 12px",
                  border: "none",
                  cursor: "pointer",
                  fontSize: 11,
                  fontFamily: "'Noto Sans SC', sans-serif",
                  background: fastMode ? "rgba(69,162,158,0.28)" : "transparent",
                  color: fastMode ? "#5EEAD4" : MUTED,
                  transition: "background 0.15s, color 0.15s",
                }}
              >
                快速
              </button>
              <button
                onClick={() => onFastModeChange(false)}
                title="精修模式：完整 VLM 质检与回炉，质量优先"
                aria-pressed={!fastMode}
                style={{
                  padding: "4px 12px",
                  border: "none",
                  cursor: "pointer",
                  fontSize: 11,
                  fontFamily: "'Noto Sans SC', sans-serif",
                  background: !fastMode ? "rgba(212,175,55,0.28)" : "transparent",
                  color: !fastMode ? "#E6C768" : MUTED,
                  transition: "background 0.15s, color 0.15s",
                }}
              >
                精修
              </button>
            </div>
            <select
              value={imageModel}
              onChange={(event) => onImageModelChange(event.target.value)}
              title="出图模型：极速最快，精细质感最好"
              aria-label="选择出图模型"
              style={{
                padding: "4px 8px",
                borderRadius: 16,
                border: "1px solid rgba(255,255,255,0.1)",
                background: "rgba(255,255,255,0.03)",
                color: "#C5C6C7",
                fontSize: 11,
                fontFamily: "'Noto Sans SC', sans-serif",
                cursor: "pointer",
                outline: "none",
                maxWidth: 190,
              }}
            >
              <option value="z-image-turbo">极速 · z-image-turbo</option>
              <option value="qwen-image-plus">均衡 · qwen-image-plus</option>
              <option value="qwen-image-2.0-pro">精细 · qwen-image-2.0-pro</option>
            </select>
          </div>

          {sending && stage && (
            <p
              style={{
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 10.5,
                color: TEAL,
                margin: "0 0 7px 6px",
              }}
            >
              {stage}
            </p>
          )}
          <div
            style={{
              display: "flex",
              alignItems: "flex-end",
              gap: 9,
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 24,
              padding: "9px 9px 9px 18px",
            }}
          >
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  submit();
                }
              }}
              rows={1}
              placeholder="描述你想复原的三星堆场景、人物或器物…"
              aria-label="描述你想复原的三星堆场景、人物或器物"
              style={{
                flex: 1,
                background: "transparent",
                border: "none",
                outline: "none",
                resize: "none",
                color: "#DDE3EA",
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 13,
                lineHeight: 1,
                maxHeight: 140,
                minHeight: 24,
              }}
            />
            <button
              onClick={submit}
              disabled={sending || !draft.trim()}
              title="发送（Enter）"
              aria-label="发送消息"
              style={{
                width: 32,
                height: 32,
                borderRadius: "50%",
                border: "none",
                flexShrink: 0,
                cursor: sending || !draft.trim() ? "not-allowed" : "pointer",
                background: sending || !draft.trim() ? "rgba(255,255,255,0.08)" : GOLD,
                color: sending || !draft.trim() ? MUTED : "#0B0C10",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                transition: "background 0.15s",
              }}
            >
              <ArrowUp size={16} />
            </button>
          </div>
          <p
            style={{
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 10,
              color: "#3a4550",
              margin: "8px 0 0",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 4,
            }}
          >
            <CornerDownLeft size={9} />
            Enter 发送 · Shift + Enter 换行
            {sessionId ? ` · 会话 ${sessionId}` : ""}
          </p>
        </div>
      </div>
    </div>
  );
}

function Caret() {
  return (
    <motion.span
      animate={{ opacity: [1, 0.15, 1] }}
      transition={{ duration: 1, repeat: Infinity }}
      style={{
        display: "inline-block",
        width: 7,
        height: 14,
        marginLeft: 3,
        verticalAlign: "-2px",
        background: GOLD,
        borderRadius: 1,
      }}
    />
  );
}

function TypingDots() {
  return (
    <span style={{ display: "inline-flex", gap: 3 }}>
      {[0, 1, 2].map((index) => (
        <motion.span
          key={index}
          animate={{ opacity: [0.25, 1, 0.25] }}
          transition={{ duration: 1.1, repeat: Infinity, delay: index * 0.18 }}
          style={{ width: 5, height: 5, borderRadius: "50%", background: TEAL, display: "block" }}
        />
      ))}
    </span>
  );
}
