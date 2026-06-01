import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Sparkles, Download, Share2, RefreshCw, X } from "lucide-react";

const TRIVIA = [
  "你知道吗？青铜神树的铸造采用了复杂的「分铸法」，即先铸各部件，再拼合焊接，体现了3000年前的超高铸造工艺。",
  "三星堆一号祭祀坑中出土的象牙超过80根，全部被人为砸断并火烧，这是一场规模浩大的主动毁器仪式。",
  "黄金面具出土时被刻意折叠放置，考古学家认为这是古蜀人在祭祀结束后的「封存神灵」仪式。",
  "三星堆遗址面积约12平方公里，目前仅发掘了极小部分，地下仍保存着大量未知文物。",
  "青铜大立人的服饰上刻有龙纹、蚕纹等复杂图案，共有三层，推测为重大祭祀场合的专用礼服。",
  "三星堆出土的青铜眼形器，因其形状像放大的人眼，被考古学家推测与「千里眼」神话崇拜有关。",
  "金杖上刻有鱼、鸟、箭头和人头四种图案，这套组合被认为可能是古蜀「鱼凫王」王朝的徽号。",
  "三星堆文化没有留下任何文字记录，其语言、文字体系存在至今成谜，是世界文明史上最大的谜团之一。",
];

const IDENTITY_OPTIONS = [
  "大祭司｜sxd_standing_figure, high priest",
  "青铜立人守护者｜sxd_standing_figure, bronze guardian",
  "纵目祭司｜sxd_zongmu_mask, ritual priest",
  "黄金祭面使者｜sxd_golden_mask, ceremonial envoy",
];

const SCENE_OPTIONS = [
  "青铜神树祭坛｜sxd_bronze_tree altar",
  "古蜀神庙前庭｜ancient Shu temple forecourt",
  "祭祀坑遗址｜sacrificial pit site",
  "博物馆主展厅｜museum main exhibition hall",
];

const ITEM_OPTIONS = [
  "金杖｜golden scepter",
  "青铜神树枝杈｜bronze sacred tree branch",
  "玉璋｜jade zhang tablet",
  "青铜纵目面具｜sxd_zongmu_mask relic",
];

const STYLE_OPTIONS = [
  "史诗油画风｜epic oil painting style",
  "博物馆写实摄影｜museum-grade realistic photography",
  "工笔重彩｜meticulous heavy-color painting",
  "电影级暗调光影｜cinematic low-key dramatic light",
];

const GENERATED_IMAGES = [
  "https://images.unsplash.com/photo-1763116987110-0e1bbe16a49e?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=800",
  "https://images.unsplash.com/photo-1775729841536-8335a857c94d?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=800",
  "https://images.unsplash.com/photo-1695902046953-1bf8caee5ac3?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=800",
];

export function AIRestoration() {
  const [identity, setIdentity] = useState(IDENTITY_OPTIONS[0]);
  const [scene, setScene] = useState(SCENE_OPTIONS[0]);
  const [item, setItem] = useState(ITEM_OPTIONS[0]);
  const [style, setStyle] = useState(STYLE_OPTIONS[0]);
  const [generating, setGenerating] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [triviaIdx, setTriviaIdx] = useState(0);
  const [progress, setProgress] = useState(0);
  const [generatedImg, setGeneratedImg] = useState(GENERATED_IMAGES[0]);
  const [showPoster, setShowPoster] = useState(false);
  const [posterCaption, setPosterCaption] = useState("");
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const progressRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const handleGenerate = () => {
    setGenerating(true);
    setGenerated(false);
    setProgress(0);
    setTriviaIdx(0);

    const randomImg = GENERATED_IMAGES[Math.floor(Math.random() * GENERATED_IMAGES.length)];
    setGeneratedImg(randomImg);

    let idx = 0;
    timerRef.current = setInterval(() => {
      idx = (idx + 1) % TRIVIA.length;
      setTriviaIdx(idx);
    }, 3200);

    let prog = 0;
    progressRef.current = setInterval(() => {
      prog += Math.random() * 3 + 1;
      if (prog >= 100) {
        prog = 100;
        clearInterval(progressRef.current!);
      }
      setProgress(Math.min(100, prog));
    }, 350);

    setTimeout(() => {
      clearInterval(timerRef.current!);
      clearInterval(progressRef.current!);
      setProgress(100);
      setGenerating(false);
      setGenerated(true);

      const captions = [
        `千年尘封，一朝觉醒。${identity}手持${item}，在${scene}前凝望星空，三星堆文明的脉搏在此刻重新跳动。`,
        `当AI之眼穿越三千年，${identity}从历史的褶皱中走出，${item}在${scene}的神光中熠熠生辉。`,
        `古蜀之梦，今朝成真。这位${identity}，正是那个沉默了三千年的文明，向今人发出的低语。`,
      ];
      setPosterCaption(captions[Math.floor(Math.random() * captions.length)]);
    }, 14000);
  };

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (progressRef.current) clearInterval(progressRef.current);
    };
  }, []);

  const prompt = `三星堆文明，${identity}，正站在${scene}前，手持${item}，${style}，史诗光影，8K超清，考古级细节`;

  const SelectField = ({
    label,
    value,
    options,
    onChange,
  }: {
    label: string;
    value: string;
    options: string[];
    onChange: (v: string) => void;
  }) => (
    <div style={{ marginBottom: 20 }}>
      <label
        style={{
          fontFamily: "'Noto Sans SC', sans-serif",
          fontSize: 11,
          color: "#45A29E",
          letterSpacing: 2,
          display: "block",
          marginBottom: 8,
        }}
      >
        {label}
      </label>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {options.map((opt) => (
          <button
            key={opt}
            onClick={() => onChange(opt)}
            style={{
              padding: "6px 14px",
              borderRadius: 6,
              border: value === opt ? "1px solid #D4AF37" : "1px solid rgba(255,255,255,0.1)",
              background: value === opt ? "rgba(212,175,55,0.12)" : "rgba(255,255,255,0.03)",
              color: value === opt ? "#D4AF37" : "#8A9BAD",
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 13,
              cursor: "pointer",
              transition: "all 0.2s",
            }}
          >
            {opt}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <section
      id="ai-restore"
      style={{
        background: "#0B0C10",
        padding: "100px 0 80px",
        position: "relative",
        borderTop: "1px solid rgba(212,175,55,0.08)",
      }}
    >
      {/* Top decor */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: "50%",
          transform: "translateX(-50%)",
          width: 1,
          height: 60,
          background: "linear-gradient(to bottom, transparent, rgba(212,175,55,0.4))",
        }}
      />

      <div className="max-w-7xl mx-auto px-6">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          style={{ marginBottom: 60 }}
        >
          <p
            style={{
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 11,
              fontWeight: 300,
              color: "#45A29E",
              letterSpacing: 6,
              marginBottom: 12,
            }}
          >
            AI Restoration Engine / 时光回溯
          </p>
          <h2
            style={{
              fontFamily: "'Noto Serif SC', serif",
              fontSize: "clamp(28px, 4vw, 52px)",
              fontWeight: 900,
              color: "#EFEFEF",
              marginBottom: 16,
              lineHeight: 1.2,
            }}
          >
            AI 复原<span style={{ color: "#D4AF37" }}>古蜀神韵</span>
          </h2>
          <p
            style={{
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 15,
              fontWeight: 300,
              color: "#8A9BAD",
              maxWidth: 520,
              lineHeight: 1.8,
            }}
          >
            无需专业提示词，选择元素即可生成专属古蜀复原图，AI 将构建三千年前的视觉奇观。
          </p>
        </motion.div>

        {/* Two-column layout */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 32,
            alignItems: "start",
          }}
          className="max-md:grid-cols-1"
        >
          {/* Left: Controls */}
          <motion.div
            initial={{ opacity: 0, x: -30 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            style={{
              background: "#1F2833",
              border: "1px solid rgba(69,162,158,0.15)",
              borderRadius: 16,
              padding: 32,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                marginBottom: 28,
                paddingBottom: 20,
                borderBottom: "1px solid rgba(255,255,255,0.06)",
              }}
            >
              <div
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: "50%",
                  background: "rgba(212,175,55,0.15)",
                  border: "1px solid rgba(212,175,55,0.3)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <Sparkles size={15} color="#D4AF37" />
              </div>
              <span
                style={{
                  fontFamily: "'Noto Serif SC', serif",
                  fontSize: 16,
                  fontWeight: 700,
                  color: "#EFEFEF",
                  letterSpacing: 1,
                }}
              >
                古蜀元素控制台
              </span>
            </div>

            <p
              style={{
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 13,
                color: "#556372",
                marginBottom: 24,
                lineHeight: 1.7,
              }}
            >
              我希望复原一个{" "}
              <span style={{ color: "#D4AF37", borderBottom: "1px dashed rgba(212,175,55,0.5)" }}>
                {identity}
              </span>
              ，他/她正在{" "}
              <span style={{ color: "#45A29E", borderBottom: "1px dashed rgba(69,162,158,0.5)" }}>
                {scene}
              </span>
              {" "}前，手持{" "}
              <span style={{ color: "#D4AF37", borderBottom: "1px dashed rgba(212,175,55,0.5)" }}>
                {item}
              </span>
              ，以{" "}
              <span style={{ color: "#45A29E", borderBottom: "1px dashed rgba(69,162,158,0.5)" }}>
                {style}
              </span>
              {" "}呈现。
            </p>

            <SelectField label="● 人物身份" value={identity} options={IDENTITY_OPTIONS} onChange={setIdentity} />
            <SelectField label="● 场景地点" value={scene} options={SCENE_OPTIONS} onChange={setScene} />
            <SelectField label="● 手持器物" value={item} options={ITEM_OPTIONS} onChange={setItem} />
            <SelectField label="● 艺术风格" value={style} options={STYLE_OPTIONS} onChange={setStyle} />

            {/* Prompt preview */}
            <div
              style={{
                background: "rgba(0,0,0,0.3)",
                border: "1px solid rgba(69,162,158,0.15)",
                borderRadius: 8,
                padding: "12px 16px",
                marginBottom: 24,
                marginTop: 8,
              }}
            >
              <p
                style={{
                  fontFamily: "'Noto Sans SC', sans-serif",
                  fontSize: 10,
                  color: "#45A29E",
                  letterSpacing: 2,
                  marginBottom: 6,
                }}
              >
                生成提示词预览
              </p>
              <p
                style={{
                  fontFamily: "monospace",
                  fontSize: 11,
                  color: "#556372",
                  lineHeight: 1.6,
                }}
              >
                {prompt}
              </p>
            </div>

            <button
              onClick={handleGenerate}
              disabled={generating}
              style={{
                width: "100%",
                padding: "14px",
                borderRadius: 10,
                border: "none",
                background: generating
                  ? "rgba(69,162,158,0.2)"
                  : "linear-gradient(135deg, #D4AF37, #a8861e)",
                color: generating ? "#45A29E" : "#0B0C10",
                fontFamily: "'Noto Serif SC', serif",
                fontSize: 16,
                fontWeight: 700,
                cursor: generating ? "not-allowed" : "pointer",
                letterSpacing: 2,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
                transition: "all 0.3s",
                boxShadow: generating ? "none" : "0 4px 24px rgba(212,175,55,0.4)",
              }}
            >
              {generating ? (
                <>
                  <RefreshCw size={16} style={{ animation: "spin 1s linear infinite" }} />
                  考古挖掘中…
                </>
              ) : (
                <>
                  <Sparkles size={16} />
                  {generated ? "重新生成" : "开始复原"}
                </>
              )}
            </button>
          </motion.div>

          {/* Right: Canvas */}
          <motion.div
            initial={{ opacity: 0, x: 30 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            style={{
              background: "#0D1117",
              border: "1px solid rgba(212,175,55,0.12)",
              borderRadius: 16,
              overflow: "hidden",
              aspectRatio: "4/5",
              position: "relative",
              minHeight: 480,
            }}
          >
            {/* Default / Idle state */}
            {!generating && !generated && (
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 16,
                }}
              >
                <div
                  style={{
                    width: 80,
                    height: 80,
                    borderRadius: "50%",
                    border: "2px dashed rgba(212,175,55,0.2)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Sparkles size={32} color="rgba(212,175,55,0.3)" />
                </div>
                <p
                  style={{
                    fontFamily: "'Noto Serif SC', serif",
                    fontSize: 18,
                    color: "rgba(212,175,55,0.3)",
                    letterSpacing: 2,
                  }}
                >
                  实时渲染画布
                </p>
                <p
                  style={{
                    fontFamily: "'Noto Sans SC', sans-serif",
                    fontSize: 13,
                    color: "#3a4550",
                    textAlign: "center",
                    maxWidth: 220,
                    lineHeight: 1.8,
                  }}
                >
                  请在左侧选择元素后点击「开始复原」，AI 将为你生成专属古蜀场景图
                </p>
              </div>
            )}

            {/* Loading state */}
            {generating && (
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  padding: 32,
                  background: "rgba(11,12,16,0.95)",
                }}
              >
                {/* Spinning artifact ring */}
                <div style={{ position: "relative", width: 120, height: 120, marginBottom: 32 }}>
                  <motion.div
                    animate={{ rotate: 360 }}
                    transition={{ duration: 3, repeat: Infinity, ease: "linear" }}
                    style={{
                      position: "absolute",
                      inset: 0,
                      borderRadius: "50%",
                      border: "2px solid transparent",
                      borderTopColor: "#D4AF37",
                      borderRightColor: "rgba(212,175,55,0.3)",
                    }}
                  />
                  <motion.div
                    animate={{ rotate: -360 }}
                    transition={{ duration: 4, repeat: Infinity, ease: "linear" }}
                    style={{
                      position: "absolute",
                      inset: 16,
                      borderRadius: "50%",
                      border: "2px solid transparent",
                      borderTopColor: "#45A29E",
                      borderLeftColor: "rgba(69,162,158,0.3)",
                    }}
                  />
                  <div
                    style={{
                      position: "absolute",
                      inset: "50%",
                      width: 24,
                      height: 24,
                      borderRadius: "50%",
                      background: "rgba(212,175,55,0.2)",
                      transform: "translate(-50%, -50%)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <span style={{ color: "#D4AF37", fontSize: 12 }}>⚙</span>
                  </div>
                </div>

                <p
                  style={{
                    fontFamily: "'Noto Serif SC', serif",
                    fontSize: 18,
                    color: "#D4AF37",
                    letterSpacing: 3,
                    marginBottom: 8,
                  }}
                >
                  考古挖掘中…
                </p>
                <p
                  style={{
                    fontFamily: "'Noto Sans SC', sans-serif",
                    fontSize: 12,
                    color: "#45A29E",
                    letterSpacing: 2,
                    marginBottom: 24,
                  }}
                >
                  AI 模型正在重建古蜀图景
                </p>

                {/* Progress bar */}
                <div
                  style={{
                    width: "100%",
                    height: 3,
                    background: "rgba(255,255,255,0.06)",
                    borderRadius: 2,
                    marginBottom: 32,
                    overflow: "hidden",
                  }}
                >
                  <motion.div
                    animate={{ width: `${progress}%` }}
                    transition={{ duration: 0.4 }}
                    style={{
                      height: "100%",
                      background: "linear-gradient(90deg, #45A29E, #D4AF37)",
                      borderRadius: 2,
                    }}
                  />
                </div>

                {/* Trivia */}
                <div
                  style={{
                    background: "rgba(31,40,51,0.8)",
                    border: "1px solid rgba(212,175,55,0.12)",
                    borderRadius: 10,
                    padding: "16px 20px",
                    maxWidth: "100%",
                  }}
                >
                  <p
                    style={{
                      fontFamily: "'Noto Sans SC', sans-serif",
                      fontSize: 10,
                      color: "#D4AF37",
                      letterSpacing: 2,
                      marginBottom: 8,
                    }}
                  >
                    ◆ 三星堆冷知识
                  </p>
                  <AnimatePresence mode="wait">
                    <motion.p
                      key={triviaIdx}
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -8 }}
                      transition={{ duration: 0.4 }}
                      style={{
                        fontFamily: "'Noto Sans SC', sans-serif",
                        fontSize: 13,
                        color: "#8A9BAD",
                        lineHeight: 1.8,
                      }}
                    >
                      {TRIVIA[triviaIdx]}
                    </motion.p>
                  </AnimatePresence>
                </div>
              </div>
            )}

            {/* Generated state */}
            {generated && (
              <div style={{ position: "absolute", inset: 0 }}>
                <motion.img
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 1 }}
                  src={generatedImg}
                  alt="AI Generated"
                  style={{
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                    filter: "sepia(0.3) contrast(1.2) saturate(1.3) hue-rotate(5deg)",
                  }}
                />

                {/* Overlay info */}
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.8 }}
                  style={{
                    position: "absolute",
                    bottom: 0,
                    left: 0,
                    right: 0,
                    background: "linear-gradient(to top, rgba(11,12,16,0.95) 0%, rgba(11,12,16,0) 100%)",
                    padding: "40px 24px 24px",
                  }}
                >
                  <p
                    style={{
                      fontFamily: "'Noto Serif SC', serif",
                      fontSize: 16,
                      color: "#D4AF37",
                      marginBottom: 4,
                      letterSpacing: 1,
                    }}
                  >
                    {identity} · {scene}
                  </p>
                  <p
                    style={{
                      fontFamily: "'Noto Sans SC', sans-serif",
                      fontSize: 12,
                      color: "#556372",
                      marginBottom: 16,
                    }}
                  >
                    古蜀文明 · AI 复原图 · {style}
                  </p>
                  <div style={{ display: "flex", gap: 10 }}>
                    <button
                      onClick={() => setShowPoster(true)}
                      style={{
                        flex: 1,
                        padding: "10px",
                        borderRadius: 8,
                        border: "none",
                        background: "linear-gradient(135deg, #D4AF37, #a8861e)",
                        color: "#0B0C10",
                        fontFamily: "'Noto Sans SC', sans-serif",
                        fontSize: 13,
                        fontWeight: 700,
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        gap: 6,
                      }}
                    >
                      <Share2 size={13} />
                      生成考古纪念海报
                    </button>
                    <button
                      style={{
                        padding: "10px 16px",
                        borderRadius: 8,
                        border: "1px solid rgba(69,162,158,0.4)",
                        background: "rgba(69,162,158,0.1)",
                        color: "#45A29E",
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        fontFamily: "'Noto Sans SC', sans-serif",
                        fontSize: 13,
                      }}
                    >
                      <Download size={13} />
                      下载
                    </button>
                  </div>
                </motion.div>

                {/* AI badge */}
                <div
                  style={{
                    position: "absolute",
                    top: 16,
                    left: 16,
                    background: "rgba(11,12,16,0.8)",
                    border: "1px solid rgba(69,162,158,0.3)",
                    borderRadius: 6,
                    padding: "4px 10px",
                    fontFamily: "'Noto Sans SC', sans-serif",
                    fontSize: 10,
                    color: "#45A29E",
                    letterSpacing: 1,
                  }}
                >
                  AI 生成 · 仅供展示
                </div>
              </div>
            )}
          </motion.div>
        </div>
      </div>

      {/* Poster Modal */}
      <AnimatePresence>
        {showPoster && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.85)",
              zIndex: 200,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: 24,
            }}
            onClick={() => setShowPoster(false)}
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              onClick={(e) => e.stopPropagation()}
              style={{
                background: "#0D1117",
                border: "1px solid rgba(212,175,55,0.3)",
                borderRadius: 16,
                overflow: "hidden",
                maxWidth: 400,
                width: "100%",
                boxShadow: "0 0 80px rgba(212,175,55,0.2)",
              }}
            >
              {/* Poster header */}
              <div
                style={{
                  padding: "20px 24px 16px",
                  borderBottom: "1px solid rgba(212,175,55,0.1)",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <span
                  style={{
                    fontFamily: "'Noto Serif SC', serif",
                    fontSize: 16,
                    color: "#D4AF37",
                    letterSpacing: 2,
                  }}
                >
                  考古纪念海报
                </span>
                <button
                  onClick={() => setShowPoster(false)}
                  style={{
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "#556372",
                    display: "flex",
                    alignItems: "center",
                  }}
                >
                  <X size={18} />
                </button>
              </div>

              {/* Poster content */}
              <div style={{ padding: 24 }}>
                <div
                  style={{
                    background: "#1F2833",
                    borderRadius: 12,
                    overflow: "hidden",
                    border: "1px solid rgba(212,175,55,0.2)",
                    marginBottom: 20,
                    position: "relative",
                  }}
                >
                  <img
                    src={generatedImg}
                    alt="poster"
                    style={{
                      width: "100%",
                      height: 200,
                      objectFit: "cover",
                      filter: "sepia(0.3) contrast(1.2) saturate(1.3)",
                    }}
                  />
                  <div
                    style={{
                      padding: "16px 20px",
                      background: "linear-gradient(135deg, rgba(31,40,51,1), rgba(11,12,16,1))",
                    }}
                  >
                    <p
                      style={{
                        fontFamily: "'Noto Serif SC', serif",
                        fontSize: 14,
                        color: "#D4AF37",
                        letterSpacing: 1,
                        marginBottom: 8,
                      }}
                    >
                      三星堆 · 古蜀神韵
                    </p>
                    <p
                      style={{
                        fontFamily: "'Noto Sans SC', sans-serif",
                        fontSize: 12,
                        color: "#8A9BAD",
                        lineHeight: 1.7,
                        marginBottom: 12,
                      }}
                    >
                      {posterCaption}
                    </p>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                      }}
                    >
                      <div>
                        <p
                          style={{
                            fontFamily: "'Noto Sans SC', sans-serif",
                            fontSize: 10,
                            color: "#45A29E",
                            letterSpacing: 2,
                          }}
                        >
                          {identity} · {scene}
                        </p>
                        <p
                          style={{
                            fontFamily: "'Noto Sans SC', sans-serif",
                            fontSize: 10,
                            color: "#3a4550",
                          }}
                        >
                          sanxingdui.ai · AI 复原生成
                        </p>
                      </div>
                      {/* Fake QR */}
                      <div
                        style={{
                          width: 48,
                          height: 48,
                          background: "repeating-conic-gradient(#45A29E 0% 25%, #0D1117 0% 50%) 0 0 / 6px 6px",
                          borderRadius: 4,
                          border: "2px solid rgba(69,162,158,0.3)",
                        }}
                      />
                    </div>
                  </div>
                </div>

                <button
                  style={{
                    width: "100%",
                    padding: "12px",
                    borderRadius: 8,
                    border: "none",
                    background: "linear-gradient(135deg, #D4AF37, #a8861e)",
                    color: "#0B0C10",
                    fontFamily: "'Noto Sans SC', sans-serif",
                    fontSize: 14,
                    fontWeight: 700,
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 8,
                    letterSpacing: 1,
                  }}
                >
                  <Share2 size={15} />
                  保存并分享
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </section>
  );
}