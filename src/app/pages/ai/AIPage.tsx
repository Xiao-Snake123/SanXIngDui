import { useParams, useNavigate } from "react-router";
import { useState, useEffect, useRef, type ReactNode } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Sparkles, Download, Share2, RefreshCw, Upload, Sliders, Wand2 } from "lucide-react";
import { generateSceneImage, getScenePromptPreview } from "@/services";

const TABS = [
  { id: "scene", label: "场景复原" },
  { id: "figure", label: "人物还原" },
  { id: "artifact", label: "文物修复" },
  { id: "style", label: "风格迁移" },
];

const TRIVIA = [
  "青铜神树的铸造采用了复杂的「分铸法」，即先铸各部件，再拼合焊接，体现了3000年前的超高铸造工艺。",
  "三星堆一号祭祀坑中出土的象牙全部被刻意砸断并焚烧，这是一场规模浩大的主动毁器仪式。",
  "黄金面具出土时被刻意折叠放置，考古学家认为这是古蜀人在祭祀结束后的「封存神灵」仪式。",
  "三星堆遗址面积约12平方公里，目前仅发掘了极小部分，地下仍保存着大量未知文物。",
  "青铜大立人的服饰上刻有龙纹、蚕纹等复杂图案，共有三层，推测为重大祭祀场合的专用礼服。",
  "金杖含金量高达94%，是目前中国同时期出土的最大黄金器物，代表最高的神权与王权。",
  "三星堆出土的铜人面具共有20余件，每件面部特征都略有差异，被认为是不同神灵的形象。",
  "2021年新发掘的第四至八号坑，采用恒温恒湿舱现场发掘，代表了中国考古技术的最高水平。",
];

const RESULT_IMAGES = [
  "https://images.unsplash.com/photo-1763116987110-0e1bbe16a49e?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=800",
  "https://images.unsplash.com/photo-1775729841536-8335a857c94d?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=800",
  "https://images.unsplash.com/photo-1695902046953-1bf8caee5ac3?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=800",
  "https://images.unsplash.com/photo-1743952198529-e68b8a9ea972?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=800",
];

// ─── SHARED GENERATION HOOK ──────────────────────────────────────────────────
function useGeneration() {
  const [generating, setGenerating] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [triviaIdx, setTriviaIdx] = useState(0);
  const [progress, setProgress] = useState(0);
  const [resultImg, setResultImg] = useState(RESULT_IMAGES[0]);
  const [error, setError] = useState<string | null>(null);
  const triviaRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const progressRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimers = () => {
    if (triviaRef.current) clearInterval(triviaRef.current);
    if (progressRef.current) clearInterval(progressRef.current);
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    triviaRef.current = null;
    progressRef.current = null;
    timeoutRef.current = null;
  };

  const startVisuals = () => {
    clearTimers();
    setGenerating(true);
    setGenerated(false);
    setProgress(8);
    setTriviaIdx(0);
    setError(null);

    let idx = 0;
    triviaRef.current = setInterval(() => {
      idx = (idx + 1) % TRIVIA.length;
      setTriviaIdx(idx);
    }, 3000);

    progressRef.current = setInterval(() => {
      setProgress((prev) => (prev >= 92 ? prev : Math.min(92, prev + Math.random() * 3 + 1.5)));
    }, 600);
  };

  const finish = (imageUrl?: string) => {
    clearTimers();
    if (imageUrl) {
      setResultImg(imageUrl);
    }
    setProgress(100);
    setGenerating(false);
    setGenerated(true);
    setError(null);
  };

  const fail = (message: string) => {
    clearTimers();
    setGenerating(false);
    setGenerated(false);
    setProgress(0);
    setError(message);
  };

  const start = (duration = 10000) => {
    startVisuals();
    timeoutRef.current = setTimeout(() => finish(), duration);
  };

  const run = async (task: () => Promise<string>) => {
    startVisuals();
    try {
      const imageUrl = await task();
      finish(imageUrl);
    } catch (error) {
      fail(error instanceof Error ? error.message : "生成失败，请稍后重试");
    }
  };

  const reset = () => {
    clearTimers();
    setGenerated(false);
    setGenerating(false);
    setProgress(0);
    setError(null);
  };

  const setProgressValue = (value: number) => {
    setProgress((prev) => Math.max(prev, Math.min(95, value)));
  };

  useEffect(() => () => clearTimers(), []);

  return { generating, generated, triviaIdx, progress, resultImg, error, start, run, reset, setProgressValue };
}

// ─── LOADING CANVAS ──────────────────────────────────────────────────────────
function LoadingCanvas({ triviaIdx, progress }: { triviaIdx: number; progress: number }) {
  return (
    <div style={{
      position: "absolute", inset: 0, display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", padding: 28,
      background: "rgba(11,12,16,0.97)",
    }}>
      <div style={{ position: "relative", width: 100, height: 100, marginBottom: 24 }}>
        <motion.div animate={{ rotate: 360 }} transition={{ duration: 2.5, repeat: Infinity, ease: "linear" }}
          style={{ position: "absolute", inset: 0, borderRadius: "50%", border: "2px solid transparent", borderTopColor: "#D4AF37", borderRightColor: "rgba(212,175,55,0.3)" }} />
        <motion.div animate={{ rotate: -360 }} transition={{ duration: 3.5, repeat: Infinity, ease: "linear" }}
          style={{ position: "absolute", inset: 12, borderRadius: "50%", border: "2px solid transparent", borderTopColor: "#45A29E", borderLeftColor: "rgba(69,162,158,0.3)" }} />
        <div style={{
          position: "absolute", inset: "50%", width: 20, height: 20, borderRadius: "50%",
          background: "rgba(212,175,55,0.2)", transform: "translate(-50%,-50%)",
          display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, color: "#D4AF37",
        }}>⚙</div>
      </div>
      <p style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 16, color: "#D4AF37", letterSpacing: 3, marginBottom: 6 }}>考古挖掘中…</p>
      <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 2, marginBottom: 20 }}>AI 模型正在重建图景</p>
      <div style={{ width: "100%", height: 3, background: "rgba(255,255,255,0.06)", borderRadius: 2, marginBottom: 24 }}>
        <motion.div animate={{ width: `${progress}%` }} transition={{ duration: 0.3 }}
          style={{ height: "100%", background: "linear-gradient(90deg, #45A29E, #D4AF37)", borderRadius: 2 }} />
      </div>
      <div style={{ background: "rgba(31,40,51,0.9)", border: "1px solid rgba(212,175,55,0.12)", borderRadius: 10, padding: "14px 18px", width: "100%" }}>
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#D4AF37", letterSpacing: 2, marginBottom: 6 }}>◆ 三星堆冷知识</p>
        <AnimatePresence mode="wait">
          <motion.p key={triviaIdx} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
            style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#8A9BAD", lineHeight: 1.8 }}>
            {TRIVIA[triviaIdx]}
          </motion.p>
        </AnimatePresence>
      </div>
    </div>
  );
}

// ─── RESULT CANVAS ───────────────────────────────────────────────────────────
function ResultCanvas({ img, label, onReset, filters = "" }: { img: string; label: string; onReset: () => void; filters?: string }) {
  const handleDownload = () => {
    const link = document.createElement("a");
    link.href = img;
    link.download = `${label}.png`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  };
  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <motion.img
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.8 }}
        src={img} alt="Generated"
        style={{ width: "100%", height: "100%", objectFit: "cover", filter: filters || "sepia(0.3) contrast(1.2) saturate(1.3)" }}
      />
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.6 }}
        style={{ position: "absolute", bottom: 0, left: 0, right: 0, background: "linear-gradient(to top, rgba(11,12,16,0.96) 0%, transparent 100%)", padding: "40px 20px 20px" }}
      >
        <p style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 14, color: "#D4AF37", marginBottom: 12 }}>{label}</p>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={onReset} style={{
            flex: 1, padding: "9px", borderRadius: 7, border: "none",
            background: "linear-gradient(135deg, #45A29E, #2c7a77)",
            color: "#fff", fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center", gap: 5,
          }}><RefreshCw size={12} />重新生成</button>
          <button onClick={handleDownload} style={{
            padding: "9px 14px", borderRadius: 7, border: "1px solid rgba(212,175,55,0.3)",
            background: "rgba(212,175,55,0.08)", color: "#D4AF37", cursor: "pointer",
            display: "flex", alignItems: "center", gap: 5, fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12,
          }}><Download size={12} />下载</button>
          <button style={{
            padding: "9px 14px", borderRadius: 7, border: "1px solid rgba(69,162,158,0.2)",
            background: "rgba(69,162,158,0.06)", color: "#45A29E", cursor: "pointer",
            display: "flex", alignItems: "center", gap: 5, fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12,
          }}><Share2 size={12} />分享</button>
        </div>
      </motion.div>
      <div style={{ position: "absolute", top: 12, right: 12, background: "rgba(11,12,16,0.75)", border: "1px solid rgba(69,162,158,0.3)", borderRadius: 5, padding: "3px 8px", fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#45A29E" }}>
        AI 生成 · 仅供展示
      </div>
    </div>
  );
}

// ─── CANVAS WRAPPER ──────────────────────────────────────────────────────────
function CanvasWrapper({ children, generating, generated, triviaIdx, progress, resultImg, resultLabel, onReset, filters }: {
  children?: ReactNode; generating: boolean; generated: boolean;
  triviaIdx: number; progress: number; resultImg: string; resultLabel: string; onReset: () => void; filters?: string;
}) {
  return (
    <div style={{
      background: "#0D1117", border: "1px solid rgba(212,175,55,0.12)",
      borderRadius: 16, overflow: "hidden", aspectRatio: "4/5",
      position: "relative", minHeight: 440,
    }}>
      {!generating && !generated && (
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12 }}>
          <div style={{ width: 64, height: 64, borderRadius: "50%", border: "2px dashed rgba(212,175,55,0.2)", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Sparkles size={28} color="rgba(212,175,55,0.3)" />
          </div>
          <p style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 16, color: "rgba(212,175,55,0.3)", letterSpacing: 2 }}>实时渲染画布</p>
        </div>
      )}
      {generating && <LoadingCanvas triviaIdx={triviaIdx} progress={progress} />}
      {generated && <ResultCanvas img={resultImg} label={resultLabel} onReset={onReset} filters={filters} />}
      {children}
    </div>
  );
}

// ─── SCENE TAB ───────────────────────────────────────────────────────────────
function SelectGroup({ label, options, value, onChange }: { label: string; options: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <label style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 2, display: "block", marginBottom: 8 }}>{label}</label>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
        {options.map((opt) => (
          <button key={opt} onClick={() => onChange(opt)}
            style={{
              padding: "5px 12px", borderRadius: 6,
              border: `1px solid ${value === opt ? "#D4AF37" : "rgba(255,255,255,0.1)"}`,
              background: value === opt ? "rgba(212,175,55,0.12)" : "rgba(255,255,255,0.03)",
              color: value === opt ? "#D4AF37" : "#8A9BAD",
              fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, cursor: "pointer", transition: "all 0.2s",
            }}
          >{opt}</button>
        ))}
      </div>
    </div>
  );
}

function SceneTab() {
  const [identity, setIdentity] = useState("大祭司");
  const [scene, setScene] = useState("博物馆展厅");
  const [item, setItem] = useState("青铜大立人");
  const [style, setStyle] = useState("真实照片风格");
  const gen = useGeneration();
  const prompt = getScenePromptPreview({ identity, scene, item, style });

  const handleGenerate = () => {
    if (gen.generating) {
      return;
    }

    void gen.run(() =>
      generateSceneImage(
        { identity, scene, item, style },
        ({ progress }) => {
          gen.setProgressValue(progress);
        },
      ),
    );
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 28, alignItems: "start" }} className="max-md:block max-md:space-y-6">
      <div style={{ background: "#1F2833", border: "1px solid rgba(69,162,158,0.15)", borderRadius: 16, padding: 28 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20, paddingBottom: 16, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
          <Sparkles size={16} color="#D4AF37" />
          <span style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 15, fontWeight: 700, color: "#EFEFEF" }}>古蜀场景元素控制台</span>
        </div>
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#556372", marginBottom: 20, lineHeight: 1.7 }}>
          我希望复原一个 <span style={{ color: "#D4AF37", borderBottom: "1px dashed rgba(212,175,55,0.4)" }}>{identity}</span>，在
          <span style={{ color: "#45A29E", borderBottom: "1px dashed rgba(69,162,158,0.4)" }}>{scene}</span>前，
          手持<span style={{ color: "#D4AF37", borderBottom: "1px dashed rgba(212,175,55,0.4)" }}>{item}</span>，
          以<span style={{ color: "#45A29E", borderBottom: "1px dashed rgba(69,162,158,0.4)" }}>{style}</span>呈现。
        </p>
        <SelectGroup label="● 人物身份" options={["大祭司", "古蜀王者", "青铜立像化身", "神树守护者", "纵目面具祭司"]} value={identity} onChange={setIdentity} />
        <SelectGroup label="● 场景地点" options={["博物馆展厅", "三星堆祭祀坑", "青铜神树祭坛", "遗址考古现场", "黑色背景展陈"]} value={scene} onChange={setScene} />
        <SelectGroup label="● 核心文物" options={["青铜大立人", "纵目青铜面具", "黄金面具", "青铜神树", "金杖"]} value={item} onChange={setItem} />
        <SelectGroup label="● 画面风格" options={["真实照片风格", "博物馆纪实摄影", "电影级写实", "考古档案照片", "赛伯朋克风格"]} value={style} onChange={setStyle} />
        <div style={{ background: "rgba(0,0,0,0.3)", border: "1px solid rgba(69,162,158,0.12)", borderRadius: 7, padding: "10px 14px", marginBottom: 20 }}>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#45A29E", letterSpacing: 2, marginBottom: 5 }}>生成提示词</p>
          <p style={{ fontFamily: "monospace", fontSize: 11, color: "#556372", lineHeight: 1.6 }}>{prompt}</p>
        </div>
        <p>


        </p>
        
        <button onClick={handleGenerate} disabled={gen.generating}
          style={{
            width: "100%", padding: "13px", borderRadius: 10, border: "none",
            background: gen.generating ? "rgba(69,162,158,0.2)" : "linear-gradient(135deg, #D4AF37, #a8861e)",
            color: gen.generating ? "#45A29E" : "#0B0C10",
            fontFamily: "'Noto Serif SC', serif", fontSize: 15, fontWeight: 700, cursor: gen.generating ? "not-allowed" : "pointer",
            letterSpacing: 2, display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
            boxShadow: gen.generating ? "none" : "0 4px 20px rgba(212,175,55,0.35)",
          }}>
          {gen.generating ? <><RefreshCw size={14} style={{ animation: "spin 1s linear infinite" }} />考古挖掘中…</> : <><Sparkles size={14} />{gen.generated ? "重新生成" : "开始复原"}</>}
        </button>
        {gen.error && (
          <p style={{ marginTop: 12, fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#ff8a8a", lineHeight: 1.6 }}>
            {gen.error}
          </p>
        )}
      </div>
      <CanvasWrapper {...gen} resultLabel={`${identity} · ${scene} · ${style}`} onReset={gen.reset} />
    </div>
  );
}

// ─── FIGURE TAB ──────────────────────────────────────────────────────────────
function FigureTab() {
  const [gender, setGender] = useState("男性");
  const [rank, setRank] = useState("贵族");
  const [era, setEra] = useState("鱼凫王朝");
  const [expression, setExpression] = useState("庄严肃穆");
  const [detail, setDetail] = useState("全身像");
  const gen = useGeneration();

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 28, alignItems: "start" }} className="max-md:block max-md:space-y-6">
      <div style={{ background: "#1F2833", border: "1px solid rgba(69,162,158,0.15)", borderRadius: 16, padding: 28 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20, paddingBottom: 16, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
          <Wand2 size={16} color="#D4AF37" />
          <span style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 15, fontWeight: 700, color: "#EFEFEF" }}>古蜀人物还原设置</span>
        </div>
        <SelectGroup label="● 性别" options={["男性", "女性", "神灵（无性别）"]} value={gender} onChange={setGender} />
        <SelectGroup label="● 社会阶层" options={["大祭司", "贵族", "武士", "工匠", "平民"]} value={rank} onChange={setRank} />
        <SelectGroup label="● 所属王朝" options={["蚕丛王朝", "柏濩王朝", "鱼凫王朝", "杜宇王朝"]} value={era} onChange={setEra} />
        <SelectGroup label="● 人物表情" options={["庄严肃穆", "虔诚膜拜", "威严凛冽", "神秘莫测"]} value={expression} onChange={setExpression} />
        <SelectGroup label="● 构图方式" options={["全身像", "半身像", "面部特写", "侧身像"]} value={detail} onChange={setDetail} />
        <div style={{ background: "rgba(69,162,158,0.06)", border: "1px solid rgba(69,162,158,0.12)", borderRadius: 8, padding: "12px 14px", marginBottom: 20 }}>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", marginBottom: 4 }}>说明</p>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#556372", lineHeight: 1.7 }}>
            基于三星堆出土的青铜头像、服饰纹样与人物图像，AI 将还原古蜀人物的真实面貌与穿着。
          </p>
        </div>
        <button onClick={() => !gen.generating && gen.start(11000)} disabled={gen.generating}
          style={{
            width: "100%", padding: "13px", borderRadius: 10, border: "none",
            background: gen.generating ? "rgba(69,162,158,0.2)" : "linear-gradient(135deg, #D4AF37, #a8861e)",
            color: gen.generating ? "#45A29E" : "#0B0C10",
            fontFamily: "'Noto Serif SC', serif", fontSize: 15, fontWeight: 700, cursor: gen.generating ? "not-allowed" : "pointer",
            letterSpacing: 2, display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
          }}>
          {gen.generating ? <><RefreshCw size={14} style={{ animation: "spin 1s linear infinite" }} />AI 还原中…</> : <><Wand2 size={14} />{gen.generated ? "重新生成" : "还原人物"}</>}
        </button>
      </div>
      <CanvasWrapper {...gen} resultLabel={`${era} ${gender}${rank} · ${expression} · ${detail}`} onReset={gen.reset} filters="sepia(0.2) contrast(1.15) saturate(1.2)" />
    </div>
  );
}

// ─── ARTIFACT REPAIR TAB ─────────────────────────────────────────────────────
const DAMAGED_ARTIFACTS = [
  { name: "破损青铜面具", image: "https://images.unsplash.com/photo-1763116987110-0e1bbe16a49e?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=400", damage: "35%" },
  { name: "残缺玉璋", image: "https://images.unsplash.com/photo-1744631244707-09d5d777034e?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=400", damage: "42%" },
  { name: "锈蚀铜头像", image: "https://images.unsplash.com/photo-1743952198529-e68b8a9ea972?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=400", damage: "28%" },
  { name: "折断金杖残件", image: "https://images.unsplash.com/photo-1634036891026-1a3a7571f82d?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=400", damage: "55%" },
];

function ArtifactTab() {
  const [selected, setSelected] = useState(DAMAGED_ARTIFACTS[0]);
  const [method, setMethod] = useState("智能填补");
  const [mode, setMode] = useState("数字展示");
  const gen = useGeneration();

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 28, alignItems: "start" }} className="max-md:block max-md:space-y-6">
      <div style={{ background: "#1F2833", border: "1px solid rgba(69,162,158,0.15)", borderRadius: 16, padding: 28 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20, paddingBottom: 16, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
          <Sliders size={16} color="#D4AF37" />
          <span style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 15, fontWeight: 700, color: "#EFEFEF" }}>文物修复控制台</span>
        </div>

        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 2, marginBottom: 10 }}>● 选择待修复文物</p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 20 }}>
          {DAMAGED_ARTIFACTS.map((a) => (
            <button key={a.name} onClick={() => setSelected(a)}
              style={{
                background: selected.name === a.name ? "rgba(69,162,158,0.12)" : "rgba(17,24,32,0.8)",
                border: `1px solid ${selected.name === a.name ? "rgba(69,162,158,0.4)" : "rgba(255,255,255,0.06)"}`,
                borderRadius: 8, overflow: "hidden", cursor: "pointer", textAlign: "left",
                transition: "all 0.2s",
              }}
            >
              <img src={a.image} alt={a.name} style={{ width: "100%", height: 60, objectFit: "cover", filter: `brightness(0.6) ${selected.name !== a.name ? "grayscale(0.4)" : ""}` }} />
              <div style={{ padding: "6px 8px" }}>
                <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: selected.name === a.name ? "#45A29E" : "#8A9BAD" }}>{a.name}</p>
                <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#D44545" }}>损毁率 {a.damage}</p>
              </div>
            </button>
          ))}
        </div>

        <SelectGroup label="● 修复方式" options={["智能填补", "历史参照", "概率推断", "3D重建"]} value={method} onChange={setMethod} />
        <SelectGroup label="● 输出模式" options={["数字展示", "博物馆展陈", "学术报告", "VR预览"]} value={mode} onChange={setMode} />

        <div style={{ background: "rgba(212,175,55,0.06)", border: "1px solid rgba(212,175,55,0.12)", borderRadius: 8, padding: "12px 14px", marginBottom: 20 }}>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#D4AF37", marginBottom: 4 }}>免责声明</p>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#556372", lineHeight: 1.7 }}>
            AI 修复结果基于统计推断，仅供学术参考与数字展示，不代表文物真实历史面貌。
          </p>
        </div>

        <button onClick={() => !gen.generating && gen.start(10000)} disabled={gen.generating}
          style={{
            width: "100%", padding: "13px", borderRadius: 10, border: "none",
            background: gen.generating ? "rgba(69,162,158,0.2)" : "linear-gradient(135deg, #D4AF37, #a8861e)",
            color: gen.generating ? "#45A29E" : "#0B0C10",
            fontFamily: "'Noto Serif SC', serif", fontSize: 15, fontWeight: 700, cursor: gen.generating ? "not-allowed" : "pointer",
            letterSpacing: 2, display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
          }}>
          {gen.generating ? <><RefreshCw size={14} style={{ animation: "spin 1s linear infinite" }} />AI 修复中…</> : <><Sparkles size={14} />{gen.generated ? "重新修复" : "开始修复"}</>}
        </button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {/* Before / After comparison */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div style={{ background: "#0D1117", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 10, overflow: "hidden" }}>
            <div style={{ padding: "8px 12px", borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#D44545", letterSpacing: 1 }}>修复前</p>
            </div>
            <img src={selected.image} alt="before" style={{ width: "100%", aspectRatio: "1", objectFit: "cover", filter: "brightness(0.6) contrast(1.2) grayscale(0.3)" }} />
          </div>
          <CanvasWrapper {...gen} resultLabel={`${selected.name} · ${method}修复 · ${mode}`} onReset={gen.reset}
            filters="sepia(0.15) contrast(1.2) saturate(1.4) brightness(1.05)">
            <div style={{ position: "absolute", top: 0, left: 0, right: 0, padding: "8px 12px", background: "rgba(11,12,16,0.7)" }}>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#45A29E", letterSpacing: 1 }}>修复后</p>
            </div>
          </CanvasWrapper>
        </div>

        {/* Stats */}
        {gen.generated && (
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
            style={{ background: "#111820", border: "1px solid rgba(69,162,158,0.15)", borderRadius: 10, padding: 16 }}>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 2, marginBottom: 12 }}>AI 修复报告</p>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {[
                ["修复完整度", "94.2%", "#45A29E"],
                ["置信度", "87.6%", "#D4AF37"],
                ["参考样本", "342件", "#6BAF8E"],
                ["算法版本", "v3.1.2", "#8A9BAD"],
              ].map(([k, v, c]) => (
                <div key={k} style={{ textAlign: "center", padding: "10px", background: "rgba(255,255,255,0.02)", borderRadius: 6 }}>
                  <p style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 16, fontWeight: 700, color: c as string, marginBottom: 2 }}>{v}</p>
                  <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#3a4550" }}>{k}</p>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </div>
    </div>
  );
}

// ─── STYLE TRANSFER TAB ──────────────────────────────────────────────────────
const STYLE_PRESETS = [
  { name: "青铜纹饰", desc: "仿青铜器表面铸造纹样", color: "#7C8D6E", filter: "sepia(0.6) hue-rotate(30deg) contrast(1.3)" },
  { name: "黄金雕刻", desc: "仿金杖金箔捶揲风格", color: "#D4AF37", filter: "sepia(0.8) saturate(1.5) brightness(1.1)" },
  { name: "玉石质感", desc: "仿透闪石玉温润质感", color: "#6BAF8E", filter: "hue-rotate(120deg) saturate(0.8) brightness(1.05)" },
  { name: "考古素描", desc: "考古工作记录线稿风格", color: "#8A9BAD", filter: "grayscale(0.9) contrast(1.4) brightness(0.9)" },
  { name: "神庙壁画", desc: "仿古蜀神庙彩绘风格", color: "#A89060", filter: "sepia(0.4) saturate(1.2) hue-rotate(-10deg)" },
  { name: "赛博古蜀", desc: "科幻与古蜀元素融合", color: "#45A29E", filter: "hue-rotate(180deg) saturate(1.6) contrast(1.2)" },
];

function StyleTab() {
  const [selectedStyle, setSelectedStyle] = useState(STYLE_PRESETS[0]);
  const [strength, setStrength] = useState(75);
  const [sourceImg] = useState(RESULT_IMAGES[2]);
  const gen = useGeneration();

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 28, alignItems: "start" }} className="max-md:block max-md:space-y-6">
      <div style={{ background: "#1F2833", border: "1px solid rgba(69,162,158,0.15)", borderRadius: 16, padding: 28 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20, paddingBottom: 16, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
          <Upload size={16} color="#D4AF37" />
          <span style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 15, fontWeight: 700, color: "#EFEFEF" }}>古蜀风格迁移</span>
        </div>

        {/* Upload area */}
        <div style={{ background: "rgba(0,0,0,0.3)", border: "2px dashed rgba(69,162,158,0.2)", borderRadius: 10, padding: 20, textAlign: "center", marginBottom: 20, cursor: "pointer", transition: "border-color 0.2s" }}
          onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.borderColor = "rgba(69,162,158,0.4)")}
          onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.borderColor = "rgba(69,162,158,0.2)")}>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: "#45A29E", marginBottom: 4 }}>📎 上传目标图片</p>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#3a4550" }}>支持 JPG / PNG / WEBP · 最大 10MB</p>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#3a4550", marginTop: 6 }}>（当前使用内置示例图）</p>
        </div>

        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 2, marginBottom: 10 }}>● 选择古蜀风格预设</p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 20 }}>
          {STYLE_PRESETS.map((s) => (
            <button key={s.name} onClick={() => setSelectedStyle(s)}
              style={{
                background: selectedStyle.name === s.name ? `${s.color}15` : "rgba(17,24,32,0.8)",
                border: `1px solid ${selectedStyle.name === s.name ? s.color + "50" : "rgba(255,255,255,0.06)"}`,
                borderRadius: 8, padding: "10px 12px", cursor: "pointer", textAlign: "left", transition: "all 0.2s",
              }}>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: selectedStyle.name === s.name ? s.color : "#C5C6C7", marginBottom: 2 }}>{s.name}</p>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#3a4550" }}>{s.desc}</p>
            </button>
          ))}
        </div>

        <div style={{ marginBottom: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
            <label style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 2 }}>● 风格强度</label>
            <span style={{ fontFamily: "monospace", fontSize: 12, color: "#D4AF37" }}>{strength}%</span>
          </div>
          <input type="range" min={10} max={100} value={strength} onChange={(e) => setStrength(+e.target.value)}
            style={{ width: "100%", accentColor: "#D4AF37" }} />
        </div>

        <button onClick={() => !gen.generating && gen.start(9000)} disabled={gen.generating}
          style={{
            width: "100%", padding: "13px", borderRadius: 10, border: "none",
            background: gen.generating ? "rgba(69,162,158,0.2)" : "linear-gradient(135deg, #D4AF37, #a8861e)",
            color: gen.generating ? "#45A29E" : "#0B0C10",
            fontFamily: "'Noto Serif SC', serif", fontSize: 15, fontWeight: 700, cursor: gen.generating ? "not-allowed" : "pointer",
            letterSpacing: 2, display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
          }}>
          {gen.generating ? <><RefreshCw size={14} style={{ animation: "spin 1s linear infinite" }} />风格迁移中…</> : <><Sparkles size={14} />{gen.generated ? "重新迁移" : "开始风格迁移"}</>}
        </button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {/* Side by side */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <div>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#556372", letterSpacing: 1, marginBottom: 6, textAlign: "center" }}>原始图像</p>
            <img src={sourceImg} alt="source" style={{ width: "100%", borderRadius: 10, objectFit: "cover", aspectRatio: "1", filter: "brightness(0.85)" }} />
          </div>
          <div>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: selectedStyle.color, letterSpacing: 1, marginBottom: 6, textAlign: "center" }}>{selectedStyle.name} 预览</p>
            <div style={{ width: "100%", aspectRatio: "1", borderRadius: 10, overflow: "hidden", background: "#0D1117", border: "1px solid rgba(255,255,255,0.06)" }}>
              <img src={sourceImg} alt="preview" style={{ width: "100%", height: "100%", objectFit: "cover", filter: selectedStyle.filter }} />
            </div>
          </div>
        </div>

        <CanvasWrapper {...gen} resultLabel={`${selectedStyle.name}风格迁移 · 强度 ${strength}%`} onReset={gen.reset} filters={selectedStyle.filter} />
      </div>
    </div>
  );
}

// ─── MAIN PAGE ───────────────────────────────────────────────────────────────
export function AIPage() {
  const { tab } = useParams<{ tab: string }>();
  const navigate = useNavigate();
  const activeTab = tab || "scene";

  return (
    <div style={{ paddingTop: 64, minHeight: "100vh" }}>
      <div style={{ background: "linear-gradient(180deg, rgba(31,40,51,0.5) 0%, transparent 100%)", borderBottom: "1px solid rgba(212,175,55,0.08)", padding: "40px 0 0" }}>
        <div className="max-w-7xl mx-auto px-6">
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 5, marginBottom: 8 }}>AI RESTORATION ENGINE / 时光回溯</p>
            <h1 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: "clamp(24px,4vw,44px)", fontWeight: 900, color: "#EFEFEF", marginBottom: 4 }}>
              AI 复原<span style={{ color: "#D4AF37" }}>引擎</span>
            </h1>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 14, color: "#8A9BAD", marginTop: 8 }}>
              以人工智能之力，重建三千年前的视觉世界
            </p>
          </motion.div>
          <div style={{ display: "flex", gap: 0, marginTop: 32, borderBottom: "1px solid rgba(255,255,255,0.06)", overflowX: "auto" }}>
            {TABS.map((t) => (
              <button key={t.id} onClick={() => navigate(`/ai/${t.id}`)}
                style={{
                  padding: "12px 24px", background: "none", border: "none",
                  borderBottom: activeTab === t.id ? "2px solid #D4AF37" : "2px solid transparent",
                  color: activeTab === t.id ? "#D4AF37" : "#8A9BAD",
                  fontFamily: "'Noto Sans SC', sans-serif", fontSize: 14, cursor: "pointer",
                  transition: "color 0.2s", whiteSpace: "nowrap", marginBottom: -1,
                }}
                onMouseEnter={(e) => { if (activeTab !== t.id) (e.currentTarget as HTMLElement).style.color = "#C5C6C7"; }}
                onMouseLeave={(e) => { if (activeTab !== t.id) (e.currentTarget as HTMLElement).style.color = "#8A9BAD"; }}
              >{t.label}</button>
            ))}
          </div>
        </div>
      </div>
      <div className="max-w-7xl mx-auto px-6" style={{ paddingTop: 40, paddingBottom: 80 }}>
        <AnimatePresence mode="wait">
          <motion.div key={activeTab} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.3 }}>
            {activeTab === "scene" && <SceneTab />}
            {activeTab === "figure" && <FigureTab />}
            {activeTab === "artifact" && <ArtifactTab />}
            {activeTab === "style" && <StyleTab />}
          </motion.div>
        </AnimatePresence>
      </div>
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}