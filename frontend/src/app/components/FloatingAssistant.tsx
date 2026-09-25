import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "motion/react";
import { X, Send, ChevronDown, BookOpen, ExternalLink, Info } from "lucide-react";
import { useAssistant } from "../context/AssistantContext";
import { askKnowledgeBase, sourceTypeLabel, type AskCitation } from "../../services";

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  /** 引用：出处、原文、链接全部来自后端语料，不由前端拼写 */
  citations?: AskCitation[];
  /** 争议、证据不足、需注意之处 */
  caveats?: string[];
  /** 语料里没有可依据的记载 —— 必须与普通回答区分渲染 */
  refused?: boolean;
  confidence?: string;
}

const PRIEST_AVATAR =
  "https://images.unsplash.com/photo-1771692822834-1ae0564af77f?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=200";

const INITIAL_MESSAGES: Message[] = [
  {
    id: 1,
    role: "assistant",
    text: "吾乃古蜀大祭司，守护三星堆圣地三千载。汝有何疑问，尽可相询。\n\n吾所言皆出自可查之典籍与记载，每条结论之下可展阅原文与出处 —— 若典籍无载，吾会直说不知，不作妄语。",
  },
];

const PROACTIVE_PROMPTS = [
  "青铜纵目面具究竟有多宽？",
  "金杖是怎样的形制？",
  "青铜神树有几层枝干？",
  "蚕丛的眼睛有什么特别？",
];

/**
 * 快捷提问。
 *
 * 这四条是**按语料实测覆盖度挑的**（对应 golden set 里能稳定召回的条目），
 * 而不是按「听起来有趣」挑 —— 否则用户第一次点开就吃到一句
 * 「语料里没有相关记载」，会把「诚实的拒答」误当成「这系统不行」。
 */
const QUICK_PROMPTS = [
  "青铜纵目面具的宽和高",
  "金杖有多长",
  "青铜神树有几层几枝",
  "蚕丛的眼睛有什么特别",
];

/** 来源类型的配色：一级来源更亮，科普/未知更暗 —— 可信度要能一眼看出来 */
const SOURCE_TYPE_COLOR: Record<string, string> = {
  excavation_report: "#D4AF37",
  museum_official: "#D4AF37",
  ancient_text: "#C9A227",
  academic_paper: "#8FB8A8",
  encyclopedia: "#45A29E",
  popular_media: "#7A8794",
  project_doc: "#7A8794",
  unknown: "#B4574E",
};

function CitationCard({ citation }: { citation: AskCitation }) {
  const [open, setOpen] = useState(false);
  const color = SOURCE_TYPE_COLOR[citation.source_type] ?? "#556372";

  return (
    <div
      style={{
        borderLeft: `2px solid ${color}`,
        background: "rgba(11,12,16,0.5)",
        borderRadius: "0 6px 6px 0",
        padding: "8px 10px",
        marginBottom: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 3 }}>
        <span
          style={{
            fontFamily: "'Noto Sans SC', sans-serif",
            fontSize: 10,
            color,
            border: `1px solid ${color}55`,
            borderRadius: 3,
            padding: "0 4px",
            flexShrink: 0,
          }}
        >
          {sourceTypeLabel(citation.source_type)} · {citation.authority.toFixed(2)}
        </span>
        <span
          style={{
            fontFamily: "'Noto Sans SC', sans-serif",
            fontSize: 11,
            color: "#C5C6C7",
            lineHeight: 1.5,
          }}
        >
          [{citation.index}] {citation.title}
          {citation.locator ? ` · ${citation.locator}` : ""}
        </span>
      </div>

      <p
        style={{
          fontFamily: "'Noto Sans SC', sans-serif",
          fontSize: 10,
          color: "#7A8794",
          lineHeight: 1.6,
          marginBottom: 4,
        }}
      >
        {citation.source}
      </p>

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <button
          onClick={() => setOpen(!open)}
          style={{
            background: "none",
            border: "none",
            padding: 0,
            color: "#556372",
            fontFamily: "'Noto Sans SC', sans-serif",
            fontSize: 10,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 3,
          }}
        >
          <ChevronDown
            size={9}
            style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}
          />
          {open ? "收起原文" : "查看原文"}
        </button>
        {citation.url && (
          <a
            href={citation.url}
            target="_blank"
            rel="noreferrer"
            style={{
              color: "#45A29E",
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 10,
              textDecoration: "none",
              display: "flex",
              alignItems: "center",
              gap: 3,
            }}
          >
            <ExternalLink size={9} />
            原始页面
          </a>
        )}
      </div>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            style={{ overflow: "hidden" }}
          >
            <p
              style={{
                fontFamily: "'Noto Serif SC', serif",
                fontSize: 11,
                color: "#A8B4BE",
                lineHeight: 1.9,
                marginTop: 8,
                paddingTop: 8,
                borderTop: "1px solid rgba(255,255,255,0.06)",
              }}
            >
              {citation.quote}
            </p>
            {citation.note && (
              <p
                style={{
                  fontFamily: "'Noto Sans SC', sans-serif",
                  fontSize: 10,
                  color: "#8A7B3F",
                  lineHeight: 1.6,
                  marginTop: 6,
                }}
              >
                说明：{citation.note}
              </p>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function CitationPanel({ citations }: { citations: AskCitation[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ marginTop: 10 }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          background: "rgba(212,175,55,0.08)",
          border: "1px solid rgba(212,175,55,0.2)",
          borderRadius: 4,
          padding: "3px 8px",
          color: "#D4AF37",
          fontSize: 11,
          cursor: "pointer",
          fontFamily: "'Noto Sans SC', sans-serif",
        }}
      >
        <BookOpen size={10} />
        依据 ({citations.length})
        <ChevronDown
          size={10}
          style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}
        />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            style={{ overflow: "hidden" }}
          >
            <div style={{ marginTop: 8 }}>
              {citations.map((citation) => (
                <CitationCard key={citation.doc_id + citation.index} citation={citation} />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function FloatingAssistant() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>(INITIAL_MESSAGES);
  const [input, setInput] = useState("");
  const [typing, setTyping] = useState(false);
  const [proactive, setProactive] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const msgIdRef = useRef(100);
  const { query, setQuery } = useAssistant();

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, typing]);

  useEffect(() => {
    if (!open) {
      const timer = setTimeout(() => {
        setProactive(PROACTIVE_PROMPTS[Math.floor(Math.random() * PROACTIVE_PROMPTS.length)]);
      }, 5000);
      return () => clearTimeout(timer);
    }
    setProactive(null);
  }, [open]);

  useEffect(() => {
    if (query) {
      setOpen(true);
      setTimeout(() => {
        void handleSendMessage(query);
        setQuery("");
      }, 400);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const handleSendMessage = async (text?: string) => {
    const question = (text ?? input).trim();
    if (!question || typing) return;
    setInput("");
    setMessages((prev) => [...prev, { id: msgIdRef.current++, role: "user", text: question }]);
    setTyping(true);

    try {
      // 不再有「关键词命中就返回硬编码答案」的快路径：
      // 那条路径会绕过检索，而它附带的出处无从核实 —— 留着它等于
      // 让一部分回答仍然无法溯源，把整条链路的可信度拉回去。
      const result = await askKnowledgeBase(question);
      setMessages((prev) => [
        ...prev,
        {
          id: msgIdRef.current++,
          role: "assistant",
          text: result.answer,
          citations: result.citations,
          caveats: result.caveats,
          refused: result.refused,
          confidence: result.confidence,
        },
      ]);
    } catch (error) {
      const reason = error instanceof Error ? error.message : "未知错误";
      setMessages((prev) => [
        ...prev,
        {
          id: msgIdRef.current++,
          role: "assistant",
          text: `暂时无法连接问答服务：${reason}\n\n请确认后端已启动（默认 8123 端口，前端通过 /agent 代理转发）。`,
        },
      ]);
    } finally {
      setTyping(false);
    }
  };

  return (
    <>
      <AnimatePresence>
        {proactive && !open && (
          <motion.div
            initial={{ opacity: 0, x: 20, scale: 0.9 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: 20, scale: 0.9 }}
            style={{
              position: "fixed",
              bottom: 100,
              right: 24,
              zIndex: 150,
              maxWidth: 260,
              background: "#1F2833",
              border: "1px solid rgba(212,175,55,0.3)",
              borderRadius: 12,
              padding: "12px 16px",
              boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
            }}
          >
            <p
              style={{
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 13,
                color: "#C5C6C7",
                lineHeight: 1.6,
                marginBottom: 10,
              }}
            >
              {proactive}
            </p>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                onClick={() => {
                  setOpen(true);
                  setProactive(null);
                }}
                style={{
                  flex: 1,
                  padding: "6px 10px",
                  background: "rgba(69,162,158,0.15)",
                  border: "1px solid rgba(69,162,158,0.3)",
                  borderRadius: 6,
                  color: "#45A29E",
                  fontFamily: "'Noto Sans SC', sans-serif",
                  fontSize: 11,
                  cursor: "pointer",
                }}
              >
                向大祭司提问
              </button>
              <button
                onClick={() => setProactive(null)}
                style={{
                  padding: "6px 10px",
                  background: "transparent",
                  border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: 6,
                  color: "#556372",
                  fontSize: 11,
                  cursor: "pointer",
                }}
              >
                稍后
              </button>
            </div>
            <div
              style={{
                position: "absolute",
                bottom: -7,
                right: 36,
                width: 12,
                height: 12,
                background: "#1F2833",
                border: "1px solid rgba(212,175,55,0.3)",
                transform: "rotate(45deg)",
                borderTop: "none",
                borderLeft: "none",
              }}
            />
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.95 }}
            style={{
              position: "fixed",
              bottom: 84,
              right: 24,
              zIndex: 150,
              width: "min(400px, calc(100vw - 48px))",
              height: 560,
              background: "#0D1117",
              border: "1px solid rgba(212,175,55,0.2)",
              borderRadius: 16,
              boxShadow: "0 20px 60px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.03)",
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                padding: "16px 20px",
                borderBottom: "1px solid rgba(212,175,55,0.1)",
                background: "linear-gradient(135deg, rgba(31,40,51,1), rgba(15,20,28,1))",
                display: "flex",
                alignItems: "center",
                gap: 12,
              }}
            >
              <div style={{ position: "relative" }}>
                <img
                  src={PRIEST_AVATAR}
                  alt="大祭司"
                  style={{
                    width: 40,
                    height: 40,
                    borderRadius: "50%",
                    objectFit: "cover",
                    border: "2px solid rgba(212,175,55,0.4)",
                    filter: "sepia(0.4) contrast(1.1)",
                  }}
                />
                <span
                  style={{
                    position: "absolute",
                    bottom: 0,
                    right: 0,
                    width: 10,
                    height: 10,
                    background: "#45A29E",
                    borderRadius: "50%",
                    border: "2px solid #0D1117",
                  }}
                />
              </div>
              <div>
                <p
                  style={{
                    fontFamily: "'Noto Serif SC', serif",
                    fontSize: 14,
                    fontWeight: 700,
                    color: "#D4AF37",
                    letterSpacing: 1,
                  }}
                >
                  古蜀大祭司
                </p>
                <p
                  style={{
                    fontFamily: "'Noto Sans SC', sans-serif",
                    fontSize: 11,
                    color: "#45A29E",
                    letterSpacing: 1,
                  }}
                >
                  专属史料库 · 每条结论可溯源
                </p>
              </div>
              <button
                onClick={() => setOpen(false)}
                style={{
                  marginLeft: "auto",
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  color: "#556372",
                  display: "flex",
                  alignItems: "center",
                  padding: 4,
                  borderRadius: 4,
                }}
                onMouseEnter={(event) => ((event.currentTarget as HTMLElement).style.color = "#C5C6C7")}
                onMouseLeave={(event) => ((event.currentTarget as HTMLElement).style.color = "#556372")}
              >
                <X size={16} />
              </button>
            </div>

            <div
              style={{
                flex: 1,
                overflowY: "auto",
                padding: "16px",
                display: "flex",
                flexDirection: "column",
                gap: 12,
                scrollbarWidth: "thin",
                scrollbarColor: "rgba(69,162,158,0.2) transparent",
              }}
            >
              {messages.map((msg) => (
                <motion.div
                  key={msg.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  style={{
                    display: "flex",
                    flexDirection: msg.role === "user" ? "row-reverse" : "row",
                    gap: 8,
                    alignItems: "flex-start",
                  }}
                >
                  {msg.role === "assistant" && (
                    <img
                      src={PRIEST_AVATAR}
                      alt=""
                      style={{
                        width: 28,
                        height: 28,
                        borderRadius: "50%",
                        objectFit: "cover",
                        flexShrink: 0,
                        filter: "sepia(0.4)",
                        border: "1px solid rgba(212,175,55,0.3)",
                      }}
                    />
                  )}
                  <div
                    style={{
                      maxWidth: "82%",
                      background:
                        msg.role === "user"
                          ? "rgba(69,162,158,0.15)"
                          : "rgba(31,40,51,0.8)",
                      border:
                        msg.role === "user"
                          ? "1px solid rgba(69,162,158,0.25)"
                          : "1px solid rgba(255,255,255,0.06)",
                      borderRadius: msg.role === "user" ? "12px 4px 12px 12px" : "4px 12px 12px 12px",
                      padding: "10px 14px",
                    }}
                  >
                    <p
                      style={{
                        fontFamily: "'Noto Sans SC', sans-serif",
                        fontSize: 13,
                        color: msg.role === "user" ? "#C5C6C7" : "#A8B4BE",
                        lineHeight: 1.75,
                        whiteSpace: "pre-line",
                      }}
                    >
                      {msg.text}
                    </p>

                    {msg.caveats && msg.caveats.length > 0 && (
                      <div
                        style={{
                          marginTop: 8,
                          paddingTop: 8,
                          borderTop: "1px solid rgba(255,255,255,0.06)",
                        }}
                      >
                        {msg.caveats.map((caveat, index) => (
                          <p
                            key={index}
                            style={{
                              display: "flex",
                              gap: 5,
                              fontFamily: "'Noto Sans SC', sans-serif",
                              fontSize: 11,
                              color: "#8A7B3F",
                              lineHeight: 1.6,
                              marginBottom: index < msg.caveats!.length - 1 ? 4 : 0,
                            }}
                          >
                            <Info size={11} style={{ flexShrink: 0, marginTop: 3 }} />
                            {caveat}
                          </p>
                        ))}
                      </div>
                    )}

                    {msg.citations && msg.citations.length > 0 && (
                      <CitationPanel citations={msg.citations} />
                    )}
                  </div>
                </motion.div>
              ))}

              {typing && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  style={{ display: "flex", gap: 8, alignItems: "center" }}
                >
                  <img
                    src={PRIEST_AVATAR}
                    alt=""
                    style={{
                      width: 28,
                      height: 28,
                      borderRadius: "50%",
                      objectFit: "cover",
                      filter: "sepia(0.4)",
                      border: "1px solid rgba(212,175,55,0.3)",
                    }}
                  />
                  <div
                    style={{
                      background: "rgba(31,40,51,0.8)",
                      border: "1px solid rgba(255,255,255,0.06)",
                      borderRadius: "4px 12px 12px 12px",
                      padding: "12px 16px",
                      display: "flex",
                      gap: 5,
                      alignItems: "center",
                    }}
                  >
                    {[0, 0.2, 0.4].map((delay, index) => (
                      <motion.span
                        key={index}
                        animate={{ y: [0, -5, 0] }}
                        transition={{ duration: 0.8, repeat: Infinity, delay }}
                        style={{
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: "#45A29E",
                          display: "block",
                        }}
                      />
                    ))}
                  </div>
                </motion.div>
              )}
              <div ref={messagesEndRef} />
            </div>

            <div
              style={{
                padding: "8px 12px",
                borderTop: "1px solid rgba(255,255,255,0.04)",
                display: "flex",
                gap: 6,
                overflowX: "auto",
                scrollbarWidth: "none",
              }}
            >
              {QUICK_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  onClick={() => void handleSendMessage(prompt)}
                  style={{
                    flexShrink: 0,
                    padding: "4px 10px",
                    background: "rgba(69,162,158,0.06)",
                    border: "1px solid rgba(69,162,158,0.15)",
                    borderRadius: 12,
                    color: "#556372",
                    fontFamily: "'Noto Sans SC', sans-serif",
                    fontSize: 11,
                    cursor: "pointer",
                    transition: "all 0.2s",
                    whiteSpace: "nowrap",
                  }}
                  onMouseEnter={(event) => {
                    (event.currentTarget as HTMLElement).style.color = "#45A29E";
                    (event.currentTarget as HTMLElement).style.borderColor = "rgba(69,162,158,0.3)";
                  }}
                  onMouseLeave={(event) => {
                    (event.currentTarget as HTMLElement).style.color = "#556372";
                    (event.currentTarget as HTMLElement).style.borderColor = "rgba(69,162,158,0.15)";
                  }}
                >
                  {prompt}
                </button>
              ))}
            </div>

            <div
              style={{
                padding: "12px 16px",
                borderTop: "1px solid rgba(255,255,255,0.06)",
                display: "flex",
                gap: 8,
                alignItems: "center",
                background: "rgba(11,12,16,0.6)",
              }}
            >
              <input
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void handleSendMessage();
                  }
                }}
                placeholder="向大祭司提问…"
                style={{
                  flex: 1,
                  background: "rgba(31,40,51,0.8)",
                  border: "1px solid rgba(69,162,158,0.15)",
                  borderRadius: 8,
                  padding: "9px 14px",
                  color: "#C5C6C7",
                  fontFamily: "'Noto Sans SC', sans-serif",
                  fontSize: 13,
                  outline: "none",
                }}
                onFocus={(event) => ((event.target as HTMLElement).style.borderColor = "rgba(69,162,158,0.4)")}
                onBlur={(event) => ((event.target as HTMLElement).style.borderColor = "rgba(69,162,158,0.15)")}
              />
              <button
                onClick={() => void handleSendMessage()}
                disabled={!input.trim() || typing}
                style={{
                  width: 38,
                  height: 38,
                  borderRadius: 8,
                  background: input.trim() ? "rgba(212,175,55,0.15)" : "rgba(255,255,255,0.03)",
                  border: input.trim() ? "1px solid rgba(212,175,55,0.3)" : "1px solid rgba(255,255,255,0.06)",
                  color: input.trim() ? "#D4AF37" : "#3a4550",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  cursor: input.trim() ? "pointer" : "not-allowed",
                  transition: "all 0.2s",
                  flexShrink: 0,
                }}
              >
                <Send size={14} />
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <motion.button
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        onClick={() => setOpen(!open)}
        style={{
          position: "fixed",
          bottom: 24,
          right: 24,
          zIndex: 151,
          width: 56,
          height: 56,
          borderRadius: "50%",
          background: open ? "rgba(31,40,51,1)" : "linear-gradient(135deg, #D4AF37, #a8861e)",
          border: "2px solid rgba(212,175,55,0.4)",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          boxShadow: "0 4px 24px rgba(212,175,55,0.35)",
          transition: "background 0.3s",
          overflow: "hidden",
        }}
      >
        {open ? (
          <X size={20} color="#D4AF37" />
        ) : (
          <img
            src={PRIEST_AVATAR}
            alt="大祭司"
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              filter: "sepia(0.4) contrast(1.1)",
            }}
          />
        )}
      </motion.button>

      {!open && (
        <motion.div
          initial={{ opacity: 0, x: 10 }}
          animate={{ opacity: 1, x: 0 }}
          style={{
            position: "fixed",
            bottom: 36,
            right: 88,
            zIndex: 151,
            background: "rgba(11,12,16,0.9)",
            border: "1px solid rgba(212,175,55,0.2)",
            borderRadius: 8,
            padding: "4px 10px",
            pointerEvents: "none",
          }}
        >
          <p
            style={{
              fontFamily: "'Noto Serif SC', serif",
              fontSize: 12,
              color: "#D4AF37",
              whiteSpace: "nowrap",
              letterSpacing: 1,
            }}
          >
            古蜀智脑
          </p>
        </motion.div>
      )}
    </>
  );
}
