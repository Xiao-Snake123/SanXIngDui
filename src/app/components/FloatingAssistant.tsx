import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "motion/react";
import { X, Send, ChevronDown, BookOpen } from "lucide-react";
import { useAssistant } from "../context/AssistantContext";
import { generateAssistantReply } from "../../services";

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  citations?: { label: string; source: string }[];
  typing?: boolean;
}

interface ChatHistoryMessage {
  role: "user" | "assistant";
  content: string;
}

const PRIEST_AVATAR = "https://images.unsplash.com/photo-1771692822834-1ae0564af77f?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=200";

const KNOWLEDGE_BASE: { keywords: string[]; answer: string; citations?: { label: string; source: string }[] }[] = [
  {
    keywords: ["金杖", "杖", "权杖"],
    answer:
      "金杖是古蜀最高权力的象征，以金箔包裹木芯制成，全长142厘米，重约463克，含金量高达94%。\n\n杖身刻有鱼、鸟、箭头和人头组成的图语系统，学界认为这是「鱼凫王」王朝的王徽——鱼凫（野鸭）是古蜀第三代王朝的图腾。这套图语是否为古蜀文字的前身，至今尚无定论。\n\n目前金杖藏于四川广汉三星堆博物馆，是国家一级文物。",
    citations: [
      { label: "《三星堆：古蜀文明的瑰宝》", source: "四川省文物考古研究院，2021年" },
      { label: "《金杖图语解析》", source: "《考古学报》2019年第3期" },
    ],
  },
  {
    keywords: ["黄金面具", "金面具", "含金量"],
    answer:
      "2021年新出土的黄金面具是目前同时期国内最重的黄金面具，重约280克，含金量约85%。\n\n面具以金箔捶揲成型，工艺精湛。面部特征高度抽象：宽鼻、大耳、方形脸，是古蜀神灵崇拜的具象体现。\n\n其背面设有固定用的穿孔，推测原本贴附在大型青铜头像上使用，是祭祀场合中神灵降临的载体。",
    citations: [
      { label: "《2021年三星堆祭祀坑发掘报告》", source: "四川省文物考古研究院，2022年" },
      { label: "《黄金面具制作工艺分析》", source: "《文物保护与考古科学》2022年第2期" },
    ],
  },
  {
    keywords: ["神树", "青铜神树", "建木", "宇宙树"],
    answer:
      "青铜神树高达396厘米，是目前发现的同时期世界最高青铜器。\n\n神树共三层枝干，每层三根共九根，每枝末端有花果与神鸟，树干一侧有神龙盘绕而下。这与《山海经》所载「建木」高度吻合——建木是天地之间的神圣天梯，天帝与群神由此往来天人之际。\n\n树顶的神鸟与古代太阳崇拜密切相关，是古蜀天文观念的实物体现。",
    citations: [
      { label: "《山海经·海内南经》", source: "先秦典籍" },
      { label: "《三星堆神树与古代宇宙观》", source: "《四川文物》2018年第5期" },
    ],
  },
  {
    keywords: ["大立人", "立人", "双手", "姿势"],
    answer:
      "青铜大立人高262厘米（含底座），是目前发现的同时代世界最大最完整的青铜立人雕像。\n\n大立人双手呈环握状高举，原本握持物至今成谜。主流观点有三：其一认为握持的是象牙，因发掘现场附近出土了大量象牙；其二认为握持权杖或神旗；其三认为握持的是玉璋类礼器。\n\n大立人全身着三层精美祭服，刻有龙纹、蚕纹等纹饰，推测代表古蜀国最高级别的大祭司或王者形象。",
    citations: [
      { label: "《三星堆青铜器研究》", source: "张良仁著，文物出版社，2020年" },
      { label: "《青铜大立人身份考》", source: "《考古》2017年第8期" },
    ],
  },
  {
    keywords: ["纵目", "眼睛", "面具", "蚕丛"],
    answer:
      "青铜纵目人面像的眼球向外突出约16厘米，远超人类生理极限。\n\n这种夸张造型对应了古籍记载：《华阳国志》载「有蜀侯蚕丛，其目纵」，即第一代蜀王蚕丛的眼睛是「纵目」（竖着长的或向外突出的）。学界普遍认为纵目面具是蚕丛神灵形象的具象化。\n\n此外，纵目也可能象征「千里眼」——能够洞察天地万物的神圣视力，是古蜀神明全知全能属性的视觉表达。",
    citations: [
      { label: "《华阳国志·蜀志》", source: "常璩著，东晋" },
      { label: "《纵目人面像与蚕丛神话》", source: "《民族学刊》2019年第4期" },
    ],
  },
];

const INITIAL_MESSAGES: Message[] = [
  {
    id: 1,
    role: "assistant",
    text: "吾乃古蜀大祭司，守护三星堆圣地三千载。汝有何疑问，尽可相询——文物之谜、古蜀之史、祭祀之礼，皆在吾知。",
  },
];

const PROACTIVE_PROMPTS = [
  "对青铜大立人感兴趣吗？你可以问我：他双手究竟握着什么？",
  "金杖上刻有神秘图案，你知道它们的含义吗？",
  "三星堆为何没有文字记载？这是世界文明史上最大的谜团之一。",
  "黄金面具的含金量究竟有多高？它是如何制作出来的？",
];

function CitationTag({ citations }: { citations: { label: string; source: string }[] }) {
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
        文献来源 ({citations.length})
        <ChevronDown size={10} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 0.2s" }} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            style={{ overflow: "hidden" }}
          >
            <div
              style={{
                marginTop: 8,
                background: "rgba(11,12,16,0.6)",
                border: "1px solid rgba(212,175,55,0.1)",
                borderRadius: 6,
                padding: "10px 12px",
              }}
            >
              {citations.map((c, i) => (
                <div key={i} style={{ marginBottom: i < citations.length - 1 ? 8 : 0 }}>
                  <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#D4AF37", marginBottom: 1 }}>
                    [{i + 1}] {c.label}
                  </p>
                  <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#556372" }}>
                    {c.source}
                  </p>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

interface FloatingAssistantProps {}

export function FloatingAssistant({}: FloatingAssistantProps) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>(INITIAL_MESSAGES);
  const [input, setInput] = useState("");
  const [typing, setTyping] = useState(false);
  const [proactive, setProactive] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const msgIdRef = useRef(100);
  const { query, setQuery } = useAssistant();

  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, typing]);

  // Show proactive bubble after 5s if chat not open
  useEffect(() => {
    if (!open) {
      const t = setTimeout(() => {
        const idx = Math.floor(Math.random() * PROACTIVE_PROMPTS.length);
        setProactive(PROACTIVE_PROMPTS[idx]);
      }, 5000);
      return () => clearTimeout(t);
    } else {
      setProactive(null);
    }
  }, [open]);

  // Handle external query from context
  useEffect(() => {
    if (query) {
      setOpen(true);
      setTimeout(() => {
        handleSendMessage(query);
        setQuery("");
      }, 400);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const handleSendMessage = async (text?: string) => {
    const q = (text ?? input).trim();
    if (!q || typing) return;
    setInput("");

    const userMsg: Message = { id: msgIdRef.current++, role: "user", text: q };
    setMessages((prev) => [...prev, userMsg]);
    setTyping(true);

    const match = KNOWLEDGE_BASE.find((kb) =>
      kb.keywords.some((kw) => q.includes(kw))
    );

    if (match) {
      window.setTimeout(() => {
        const assistantMsg: Message = {
          id: msgIdRef.current++,
          role: "assistant",
          text: match.answer,
          citations: match.citations,
        };

        setTyping(false);
        setMessages((prev) => [...prev, assistantMsg]);
      }, 800 + Math.random() * 400);
      return;
    }

    try {
      const history = messages
        .slice(-6)
        .map((message): ChatHistoryMessage => ({
          role: message.role,
          content: message.text,
        }));

      const answer = await generateAssistantReply(q, history);
      const assistantMsg: Message = {
        id: msgIdRef.current++,
        role: "assistant",
        text: answer,
      };

      setMessages((prev) => [...prev, assistantMsg]);
    } catch (error) {
      const fallbackText = error instanceof Error
        ? `暂时无法连接 Open WebUI：${error.message}`
        : "暂时无法连接 Open WebUI，请稍后重试。";

      const assistantMsg: Message = {
        id: msgIdRef.current++,
        role: "assistant",
        text: fallbackText,
      };

      setMessages((prev) => [...prev, assistantMsg]);
    } finally {
      setTyping(false);
    }
  };

  return (
    <>
      {/* Proactive bubble */}
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
            {/* Bubble arrow */}
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

      {/* Chat Panel */}
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
              width: "min(380px, calc(100vw - 48px))",
              height: 520,
              background: "#0D1117",
              border: "1px solid rgba(212,175,55,0.2)",
              borderRadius: 16,
              boxShadow: "0 20px 60px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.03)",
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
            }}
          >
            {/* Header */}
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
                  RAG 智能助手 · 在线
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
                  transition: "color 0.2s",
                }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.color = "#C5C6C7")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.color = "#556372")}
              >
                <X size={16} />
              </button>
            </div>

            {/* Messages */}
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
                      maxWidth: "80%",
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
                    {msg.citations && <CitationTag citations={msg.citations} />}
                  </div>
                </motion.div>
              ))}

              {/* Typing indicator */}
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
                    {[0, 0.2, 0.4].map((delay, i) => (
                      <motion.span
                        key={i}
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

            {/* Quick prompts */}
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
              {["金杖的图案", "纵目面具含义", "神树与建木", "黄金面具工艺"].map((q) => (
                <button
                  key={q}
                  onClick={() => {
                    setInput(q);
                    handleSendMessage(q);
                  }}
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
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.color = "#45A29E";
                    (e.currentTarget as HTMLElement).style.borderColor = "rgba(69,162,158,0.3)";
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.color = "#556372";
                    (e.currentTarget as HTMLElement).style.borderColor = "rgba(69,162,158,0.15)";
                  }}
                >
                  {q}
                </button>
              ))}
            </div>

            {/* Input */}
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
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSendMessage();
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
                  transition: "border-color 0.2s",
                }}
                onFocus={(e) => ((e.target as HTMLElement).style.borderColor = "rgba(69,162,158,0.4)")}
                onBlur={(e) => ((e.target as HTMLElement).style.borderColor = "rgba(69,162,158,0.15)")}
              />
              <button
                onClick={() => handleSendMessage()}
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

      {/* Floating button */}
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
          background: open
            ? "rgba(31,40,51,1)"
            : "linear-gradient(135deg, #D4AF37, #a8861e)",
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

      {/* Label badge */}
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