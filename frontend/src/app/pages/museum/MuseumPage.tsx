import { useParams, useNavigate } from "react-router";
import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import { MessageSquare, X, ZoomIn, RotateCcw, Play, Pause, Search } from "lucide-react";
import { useAssistant } from "../../context/AssistantContext";

// ─── DATA ────────────────────────────────────────────────────────────────────

const ARTIFACTS = [
  {
    id: 1, name: "青铜大立人", nameEn: "Bronze Standing Figure", cat: "青铜器",
    year: "约公元前1200年", height: "262 cm（含座）", material: "青铜", weight: "约180kg", pit: "四号坑",
    image: "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384367727_JyYCD9wh.png?imageMogr2/format/webp/ignore-error/1",
    thumb: "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384367727_JyYCD9wh.png?imageMogr2/format/webp/ignore-error/1",
    desc: "世界同时代最大最完整的青铜立人，双手呈环握状，推测原握象牙或权杖，象征古蜀最高祭司或王权。",
    mystery: "双手握持物至今成谜，极具争议。",
    aiQuery: "青铜大立人双手究竟握着什么？",
  },
  {
    id: 2, name: "黄金面具", nameEn: "Gold Mask", cat: "黄金器",
    year: "约公元前1300年", height: "23.5 cm", material: "金箔（含金量85%）", weight: "约280g", pit: "五号坑",
    image: "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384343223_KHMb6w7J.png?imageMogr2/format/webp/ignore-error/1",
    thumb: "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384343223_KHMb6w7J.png?imageMogr2/format/webp/ignore-error/1",
    desc: "目前同时期国内最重黄金面具，捶揲成型，宽鼻大耳，是古蜀神灵崇拜的具象体现。",
    mystery: "背面穿孔暗示原附于青铜头像，其宗教用途不明。",
    aiQuery: "黄金面具含金量有多高？是如何制作的？",
  },
  {
    id: 3, name: "青铜神树", nameEn: "Bronze Sacred Tree", cat: "青铜器",
    year: "约公元前1200年", height: "396 cm", material: "青铜（分铸法）", weight: "约84kg", pit: "一号坑",
    image: "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384410467_ssHipbjC.png?imageMogr2/format/webp/ignore-error/1",
    thumb: "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384410467_ssHipbjC.png?imageMogr2/format/webp/ignore-error/1",
    desc: "目前已知最高青铜器，三层九枝，神龙盘绕，被认为是《山海经》「建木」的实物对应，沟通天地之神树。",
    mystery: "树顶原有一鸟形器已残缺，其完整形态成谜。",
    aiQuery: "青铜神树与《山海经》建木有何关联？",
  },
  {
    id: 4, name: "青铜纵目人面像", nameEn: "Protruding Eye Mask", cat: "青铜器",
    year: "约公元前1100年", height: "65 cm", material: "青铜", weight: "约3.5kg", pit: "二号坑",
    image: "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384809191_Tsa2RfYH.png?imageMogr2/format/webp/ignore-error/1",
    thumb: "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384809191_Tsa2RfYH.png?imageMogr2/format/webp/ignore-error/1",
    desc: "眼球外突16cm、耳廓极张，为古蜀「千里眼·顺风耳」的神灵形象，与《华阳国志》蚕丛「其目纵」记载契合。",
    mystery: "「纵目」是神话夸张还是某种真实生理特征的记录？",
    aiQuery: "纵目人面像的纵目象征什么？与蚕丛王有何关联？",
  },
  {
    id: 5, name: "金杖", nameEn: "Gold Staff", cat: "黄金器",
    year: "约公元前1200年", height: "142 cm", material: "金箔（含金量94%）", weight: "约463g", pit: "一号坑",
    image: "https://industry.map.qq.com/cloud/file/san/mustsee/seven/1687934881425_kCyeNaA7.png?imageMogr2/format/webp/ignore-error/1",
    thumb: "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384391380_PA8HNZaR.png?imageMogr2/format/webp/ignore-error/1",
    desc: "金箔包裹木芯，杖身刻有鱼、鸟、箭头、人头组成的图语系统，是古蜀「鱼凫王」王徽，代表最高神权。",
    mystery: "其图语是否为古蜀文字前身，学界争议持续至今。",
    aiQuery: "金杖上的图案有什么含义？是古蜀文字起源吗？",
  },
  {
    id: 6, name: "商玉璋", nameEn: "Jade Zhang", cat: "玉器",
    year: "约公元前1500年", height: "54.2 cm", material: "透闪石玉", weight: "约320g", pit: "二号坑",
    image: "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384743465_38A4e9yk.png?imageMogr2/format/webp/ignore-error/1",
    thumb: "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384743465_38A4e9yk.png?imageMogr2/format/webp/ignore-error/1",
    desc: "祭祀六礼器之一，用于礼南方。三星堆玉璋部分刻有跪拜祭司图像，是目前已知最早的祭祀场景图像记录。",
    mystery: "玉璋来源与中原地区相似，暗示两地之间存在礼器文化交流。",
    aiQuery: "玉璋在古代祭祀中有什么用途？",
  },
  {
    id: 7, name: "铜头像", nameEn: "Bronze Head", cat: "青铜器",
    year: "约公元前1100年", height: "40.2 cm", material: "青铜+金箔", weight: "约2.1kg", pit: "二号坑",
    image: "https://smart-tourism-1314167384.cos.ap-nanjing.myqcloud.com/cloud/policy/1687781912545_D8CPckAB.jpg?imageMogr2/format/webp/ignore-error/1",
    thumb: "https://smart-tourism-1314167384.cos.ap-nanjing.myqcloud.com/cloud/policy/1687781912545_D8CPckAB.jpg?imageMogr2/format/webp/ignore-error/1",
    desc: "三星堆出土多件大型铜头像，部分贴有金箔面罩，面部特征统一却略有差异，被认为是神灵或先王的集体祭祀像。",
    mystery: "铜头像的造型似乎与人类有明显差异，可能是神灵的理想化形象。",
    aiQuery: "三星堆铜头像代表的是神灵还是真实的人物？",
  },
  {
    id: 8, name: "象牙", nameEn: "Ivory Tusk", cat: "象牙器",
    year: "约公元前1200年", height: "最长达163 cm", material: "象牙", weight: "约5kg/根", pit: "各坑均有",
    image: "https://audio.sxd.cn/cloud/images/000680.jpg?imageMogr2/format/webp/ignore-error/1",
    thumb: "https://audio.sxd.cn/cloud/images/000587.jpg?imageMogr2/format/webp/ignore-error/1",
    desc: "三星堆各坑共出土13000余根象牙，均被刻意砸断并焚烧，是规模罕见的毁器祭祀。象牙来源至今成谜。",
    mystery: "如此大量的象牙从何而来？四川盆地古代是否存在大象群？",
    aiQuery: "三星堆出土的象牙从哪里来？为什么要焚烧象牙？",
  },
];

const CAT_COLORS: Record<string, string> = {
  "青铜器": "#7C8D6E",
  "黄金器": "#D4AF37",
  "玉器": "#6BAF8E",
  "象牙器": "#A89060",
};

// ─── TIMELINE DATA ──────────────────────────────────────────────────────────

const TIMELINE = [
  {
    year: "约公元前1700年",
    event: "三星堆文明兴起",
    desc: "古蜀文明在成都平原兴起，以独特的青铜铸造与金器制作技术为核心，与中原商文明同期并存。",
    icon: "✦",
  },
  {
    year: "约公元前1200年",
    event: "祭祀坑埋藏时期",
    desc: "三星堆居民举行大规模燎祭仪式，将大量青铜器、黄金器、玉器与象牙集中毁器入坑，具体原因至今未解。",
    icon: "🔥",
  },
  {
    year: "约公元前1150年",
    event: "三星堆文明衰落",
    desc: "三星堆遗址逐渐被废弃，文明中心转移至金沙遗址（今成都市区），标志着古蜀文明的新阶段。",
    icon: "〃",
  },
  {
    year: "公元前316年",
    event: "古蜀国被秦灭",
    desc: "秦惠王命张仪、司马错灭古蜀国，古蜀文明融入秦汉文化体系，独立文化记忆逐渐消逝。",
    icon: "⚔",
  },
  {
    year: "1929年",
    event: "首次偶然发现",
    desc: "燕道诚父子在农耕时偶然发现一批玉石器，引起考古学界注意，三星堆遗址由此进入现代视野。",
    icon: "👁",
  },
  {
    year: "1986年",
    event: "一号、二号祭祀坑发掘",
    desc: "四川省文物考古研究所发掘两座祭祀坑，出土青铜大立人、神树、纵目面具等重器，震惊世界考古学界。",
    icon: "⛏",
  },
  {
    year: "2019—2022年",
    event: "六座新祭祀坑发掘",
    desc: "三星堆重启大规模发掘，采用多学科联合方式，出土黄金面具、青铜顶尊人像等新器物，发掘面积超1000㎡。",
    icon: "🏛",
  },
  {
    year: "2026年（现在）",
    event: "数字化传播新纪元",
    desc: "以AI技术为核心，三星堆文明进入数字化传播新阶段，本项目旨在以科技之光唤醒三千年前的古蜀神韵。",
    icon: "🤖",
  },
];

const EXCAVATION_PHOTOS = [
  { img: "https://images.unsplash.com/photo-1771692822834-1ae0564af77f?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=800", caption: "三星堆祭祀坑发掘现场", desc: "考古学家使用精密工具在土层中清理文物" },
  { img: "https://images.unsplash.com/photo-1762331876236-fd81f3575534?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=800", caption: "野外考古记录工作", desc: "每件文物出土位置均经过精确测量与记录" },
  { img: "https://images.unsplash.com/photo-1763116987110-0e1bbe16a49e?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=800", caption: "青铜器清理保护", desc: "出土青铜器经专业人员现场进行应急保护处理" },
  { img: "https://images.unsplash.com/photo-1763577593450-8ec808f6eb11?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=800", caption: "遗址夜间全景", desc: "三星堆遗址覆盖面积约12平方公里，目前仅发掘极少部分" },
  { img: "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384367727_JyYCD9wh.png?imageMogr2/format/webp/ignore-error/1", caption: "文物修复实验室", desc: "出土文物在专业实验室经历漫长修复过程" },
  { img: "https://images.unsplash.com/photo-1706552604002-f7efaebebca6?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=800", caption: "博物馆展陈设计", desc: "三星堆博物馆为核心文物设计专属展陈空间" },
];

// ─── TABS CONFIG ─────────────────────────────────────────────────────────────

const TABS = [
  { id: "gallery", label: "精品文物展" },
  { id: "3d", label: "3D 数字模型" },
  { id: "excavation", label: "考古现场" },
  { id: "timeline", label: "文明时间轴" },
];

// ─── GALLERY TAB ─────────────────────────────────────────────────────────────

function GalleryTab() {
  const [filter, setFilter] = useState("全部");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<(typeof ARTIFACTS)[0] | null>(null);
  const { setQuery } = useAssistant();

  const cats = ["全部", "青铜器", "黄金器", "玉器", "象牙器"];
  const filtered = ARTIFACTS.filter(
    (a) =>
      (filter === "全部" || a.cat === filter) &&
      (search === "" || a.name.includes(search) || a.nameEn.toLowerCase().includes(search.toLowerCase()))
  );

  return (
    <div>
      {/* Filter Bar */}
      <div className="flex flex-wrap gap-3 items-center mb-8">
        <div style={{ position: "relative", flex: 1, maxWidth: 280 }}>
          <Search size={14} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "#45A29E" }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索文物名称…"
            style={{
              width: "100%", paddingLeft: 36, paddingRight: 12, paddingTop: 9, paddingBottom: 9,
              background: "rgba(31,40,51,0.6)", border: "1px solid rgba(69,162,158,0.2)",
              borderRadius: 8, color: "#C5C6C7",
              fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, outline: "none",
            }}
          />
        </div>
        <div className="flex gap-2 flex-wrap">
          {cats.map((c) => (
            <button
              key={c}
              onClick={() => setFilter(c)}
              style={{
                padding: "6px 14px", borderRadius: 6, border: "1px solid",
                borderColor: filter === c ? (CAT_COLORS[c] || "#45A29E") : "rgba(255,255,255,0.1)",
                background: filter === c ? `${CAT_COLORS[c] || "#45A29E"}18` : "transparent",
                color: filter === c ? (CAT_COLORS[c] || "#45A29E") : "#8A9BAD",
                fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, cursor: "pointer",
                transition: "all 0.2s",
              }}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      {/* Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 20 }}>
        {filtered.map((artifact, i) => (
          <motion.div
            key={artifact.id}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
            onClick={() => setSelected(artifact)}
            style={{
              background: "#111820", border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: 12, overflow: "hidden", cursor: "pointer",
              transition: "transform 0.2s, border-color 0.2s, box-shadow 0.2s",
            }}
            whileHover={{ y: -4, boxShadow: "0 12px 40px rgba(0,0,0,0.5)", borderColor: "rgba(69,162,158,0.25)" }}
          >
            <div style={{ height: 180, overflow: "hidden", position: "relative" }}>
              <img src={artifact.thumb} alt={artifact.name} style={{ width: "100%", height: "100%", objectFit: "cover", filter: "brightness(0.85) contrast(1.1)", transition: "transform 0.4s" }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.transform = "scale(1.06)")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.transform = "scale(1)")}
              />
              <div style={{ position: "absolute", inset: 0, background: "linear-gradient(to top, rgba(11,12,16,0.8) 0%, transparent 60%)" }} />
              <span style={{
                position: "absolute", top: 10, right: 10,
                background: `${CAT_COLORS[artifact.cat] || "#45A29E"}20`,
                border: `1px solid ${CAT_COLORS[artifact.cat] || "#45A29E"}50`,
                borderRadius: 4, padding: "2px 8px",
                fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: CAT_COLORS[artifact.cat] || "#45A29E",
              }}>
                {artifact.cat}
              </span>
              <div style={{ position: "absolute", top: 10, left: 10, color: "#45A29E", opacity: 0.7 }}>
                <ZoomIn size={14} />
              </div>
            </div>
            <div style={{ padding: "16px 18px 18px" }}>
              <h3 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 16, fontWeight: 700, color: "#EFEFEF", marginBottom: 4 }}>
                {artifact.name}
              </h3>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 1, marginBottom: 8 }}>
                {artifact.nameEn} · {artifact.year}
              </p>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#8A9BAD", lineHeight: 1.7, marginBottom: 12 }}>
                {artifact.desc.substring(0, 60)}…
              </p>
              <button
                onClick={(e) => { e.stopPropagation(); setQuery(artifact.aiQuery); }}
                style={{
                  display: "flex", alignItems: "center", gap: 5,
                  background: "rgba(69,162,158,0.08)", border: "1px solid rgba(69,162,158,0.2)",
                  borderRadius: 5, padding: "5px 12px", color: "#45A29E",
                  fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, cursor: "pointer",
                  transition: "background 0.2s",
                }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "rgba(69,162,158,0.16)")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "rgba(69,162,158,0.08)")}
              >
                <MessageSquare size={11} /> 向智脑提问
              </button>
            </div>
          </motion.div>
        ))}
      </div>

      {/* Lightbox Modal */}
      <AnimatePresence>
        {selected && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={() => setSelected(null)}
            style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.9)", zIndex: 200, display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.9, opacity: 0 }}
              onClick={(e) => e.stopPropagation()}
              style={{
                background: "#0D1117", border: "1px solid rgba(212,175,55,0.2)",
                borderRadius: 16, overflow: "hidden", maxWidth: 800, width: "100%",
                maxHeight: "90vh", overflowY: "auto",
                boxShadow: "0 30px 80px rgba(0,0,0,0.8)",
              }}
            >
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }} className="max-md:block">
                <div style={{ position: "relative", aspectRatio: "4/5" }}>
                  <img src={selected.image} alt={selected.name} style={{ width: "100%", height: "100%", objectFit: "cover", filter: "brightness(0.9) contrast(1.1) sepia(0.15)" }} />
                  <div style={{ position: "absolute", inset: 0, background: "linear-gradient(to right, transparent 60%, rgba(13,17,23,1))" }} />
                </div>
                <div style={{ padding: "32px 28px", display: "flex", flexDirection: "column" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
                    <div>
                      <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#45A29E", letterSpacing: 3, marginBottom: 4 }}>数字档案</p>
                      <h2 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 26, fontWeight: 900, color: "#D4AF37", letterSpacing: 1 }}>{selected.name}</h2>
                    </div>
                    <button onClick={() => setSelected(null)} style={{ background: "none", border: "none", cursor: "pointer", color: "#556372" }}><X size={18} /></button>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px 20px", marginBottom: 20 }}>
                    {[
                      ["年代", selected.year], ["类别", selected.cat],
                      ["尺寸", selected.height], ["重量", selected.weight],
                      ["材质", selected.material], ["出土坑位", selected.pit],
                    ].map(([k, v]) => (
                      <div key={k}>
                        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#3a4550", marginBottom: 2 }}>{k}</p>
                        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: "#C5C6C7" }}>{v}</p>
                      </div>
                    ))}
                  </div>
                  <div style={{ flex: 1, marginBottom: 16 }}>
                    <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: "#8A9BAD", lineHeight: 1.8, marginBottom: 16 }}>{selected.desc}</p>
                    <div style={{ background: "rgba(212,175,55,0.06)", border: "1px solid rgba(212,175,55,0.15)", borderRadius: 8, padding: "12px 14px" }}>
                      <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#D4AF37", letterSpacing: 1, marginBottom: 4 }}>◆ 历史谜团</p>
                      <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#A89060", lineHeight: 1.7 }}>{selected.mystery}</p>
                    </div>
                  </div>
                  <button
                    onClick={() => { setQuery(selected.aiQuery); setSelected(null); }}
                    style={{
                      display: "flex", alignItems: "center", gap: 8, justifyContent: "center",
                      background: "linear-gradient(135deg, rgba(212,175,55,0.15), rgba(212,175,55,0.05))",
                      border: "1px solid rgba(212,175,55,0.3)", borderRadius: 8, padding: "11px",
                      color: "#D4AF37", fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, cursor: "pointer",
                    }}
                  >
                    <MessageSquare size={14} /> 向古蜀智脑提问此文物
                  </button>
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ─── 3D MODEL TAB ────────────────────────────────────────────────────────────

const MODEL_ARTIFACTS = ARTIFACTS.slice(0, 5);

function Model3DTab() {
  const [selectedArtifact, setSelectedArtifact] = useState(MODEL_ARTIFACTS[0]);
  const [rotation, setRotation] = useState(0);
  const [autoRotate, setAutoRotate] = useState(true);
  const [isDragging, setIsDragging] = useState(false);
  const [startX, setStartX] = useState(0);
  // 垂直俯仰角目前是固定值：拖拽只接管了水平旋转。
  // 原先写成 useState(5) 但从未调用 setter，属于「看起来可调、实际上不可调」的死状态，
  // 与其留着不如直说；要支持垂直拖拽时再换回 state 即可。
  const elevation = 5;
  const animRef = useRef<number | null>(null);
  const { setQuery } = useAssistant();

  useEffect(() => {
    if (!autoRotate || isDragging) return;
    // 用 requestAnimationFrame 而不是 setInterval(16)：
    // rAF 与显示器刷新率对齐（不会因回调堆积产生漂移），且标签页切到后台时
    // 浏览器会自动暂停它 —— 原先每 16ms 一次 setState，整棵 3D 子树跟着重渲染。
    const step = () => {
      setRotation((r) => (r + 0.4) % 360);
      animRef.current = requestAnimationFrame(step);
    };
    animRef.current = requestAnimationFrame(step);
    return () => {
      if (animRef.current !== null) cancelAnimationFrame(animRef.current);
    };
  }, [autoRotate, isDragging]);

  const lightAngle = (rotation % 360) * (Math.PI / 180);
  const lightIntensity = Math.abs(Math.cos(lightAngle));
  const brightness = 0.65 + lightIntensity * 0.5;
  const shadowOpacity = 0.3 + lightIntensity * 0.3;

  const handleMouseDown = (e: React.MouseEvent) => {
    setIsDragging(true);
    setStartX(e.clientX);
    setAutoRotate(false);
  };
  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging) return;
    const delta = e.clientX - startX;
    setRotation((r) => (r + delta * 0.5) % 360);
    setStartX(e.clientX);
  };
  const handleMouseUp = () => setIsDragging(false);

  // Multiple view thumbnails (every 45°)
  const viewAngles = [0, 45, 90, 135, 180, 225, 270, 315];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", gap: 24 }} className="max-md:block max-md:space-y-6">
      {/* 3D Stage */}
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {/* Main viewer */}
        <div
          style={{
            background: "radial-gradient(ellipse at center, #1F2833 0%, #0B0C10 70%)",
            border: "1px solid rgba(212,175,55,0.15)",
            borderRadius: 16, padding: 24, position: "relative",
            perspective: 1200, cursor: isDragging ? "grabbing" : "grab",
            minHeight: 420, display: "flex", alignItems: "center", justifyContent: "center",
            userSelect: "none",
          }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          {/* Grid floor */}
          <div style={{
            position: "absolute", bottom: 20, left: "50%", transform: "translateX(-50%) rotateX(70deg)",
            width: 260, height: 160,
            backgroundImage: "linear-gradient(rgba(69,162,158,0.08) 1px, transparent 1px), linear-gradient(90deg, rgba(69,162,158,0.08) 1px, transparent 1px)",
            backgroundSize: "32px 32px",
            borderRadius: 4, transformOrigin: "bottom center",
          }} />

          {/* Artifact rotating in 3D */}
          <div
            style={{
              transform: `perspective(900px) rotateY(${rotation}deg) rotateX(${elevation}deg)`,
              transition: isDragging ? "none" : "transform 0.05s linear",
              position: "relative", zIndex: 2,
            }}
          >
            <div style={{ position: "relative", width: 240, height: 320 }}>
              <img
                src={selectedArtifact.image}
                alt={selectedArtifact.name}
                draggable={false}
                style={{
                  width: "100%", height: "100%", objectFit: "cover",
                  borderRadius: 8, display: "block",
                  filter: `brightness(${brightness}) contrast(1.15) sepia(0.15)`,
                  boxShadow: `0 ${30 * lightIntensity}px ${60 * lightIntensity}px rgba(0,0,0,${shadowOpacity}), 0 0 40px rgba(212,175,55,${lightIntensity * 0.2})`,
                }}
              />
              {/* Lighting sheen overlay */}
              <div
                style={{
                  position: "absolute", inset: 0, borderRadius: 8,
                  background: `linear-gradient(${135 + rotation}deg, rgba(212,175,55,${lightIntensity * 0.15}) 0%, transparent 50%, rgba(0,0,0,${(1 - lightIntensity) * 0.3}) 100%)`,
                  pointerEvents: "none",
                }}
              />
            </div>
          </div>

          {/* Shadow on floor */}
          <div
            style={{
              position: "absolute", bottom: 28, left: "50%",
              transform: `translateX(-50%) scaleX(${0.5 + lightIntensity * 0.2})`,
              width: 180, height: 20, borderRadius: "50%",
              background: `radial-gradient(ellipse, rgba(0,0,0,${shadowOpacity * 0.8}), transparent)`,
            }}
          />

          {/* Controls overlay */}
          <div style={{ position: "absolute", top: 16, right: 16, display: "flex", gap: 8 }}>
            <button
              onClick={() => setAutoRotate(!autoRotate)}
              style={{
                width: 32, height: 32, borderRadius: "50%",
                background: autoRotate ? "rgba(212,175,55,0.2)" : "rgba(255,255,255,0.05)",
                border: `1px solid ${autoRotate ? "rgba(212,175,55,0.4)" : "rgba(255,255,255,0.1)"}`,
                color: autoRotate ? "#D4AF37" : "#556372",
                display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer",
              }}
              title={autoRotate ? "停止旋转" : "自动旋转"}
            >
              {autoRotate ? <Pause size={13} /> : <Play size={13} />}
            </button>
            <button
              onClick={() => { setRotation(0); setAutoRotate(false); }}
              style={{
                width: 32, height: 32, borderRadius: "50%",
                background: "rgba(255,255,255,0.05)",
                border: "1px solid rgba(255,255,255,0.1)",
                color: "#556372",
                display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer",
              }}
              title="重置角度"
            >
              <RotateCcw size={13} />
            </button>
          </div>

          {/* Instructions */}
          <div style={{ position: "absolute", bottom: 16, left: "50%", transform: "translateX(-50%)" }}>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#3a4550", letterSpacing: 2, whiteSpace: "nowrap" }}>
              拖动旋转 · 点击控制按钮
            </p>
          </div>

          {/* Rotation indicator ring */}
          <div style={{
            position: "absolute", bottom: 48, left: "50%", transform: "translateX(-50%)",
            width: 100, height: 8,
            background: "rgba(255,255,255,0.03)",
            border: "1px solid rgba(69,162,158,0.15)", borderRadius: 4,
          }}>
            <div style={{
              width: `${((rotation % 360) / 360) * 100}%`,
              height: "100%",
              background: "linear-gradient(90deg, #45A29E, #D4AF37)",
              borderRadius: 4,
              transition: "width 0.1s",
            }} />
          </div>
        </div>

        {/* View angle thumbnails */}
        <div style={{ display: "flex", gap: 8, overflowX: "auto", paddingBottom: 4 }}>
          {viewAngles.map((angle) => (
            <button
              key={angle}
              onClick={() => { setRotation(angle); setAutoRotate(false); }}
              style={{
                flexShrink: 0, width: 64, height: 64, borderRadius: 8, overflow: "hidden",
                border: `1px solid ${Math.abs(rotation - angle) < 20 ? "rgba(212,175,55,0.5)" : "rgba(255,255,255,0.08)"}`,
                cursor: "pointer", padding: 0, background: "#0D1117",
              }}
              title={`${angle}°视角`}
            >
              <img
                src={selectedArtifact.thumb}
                alt={`${angle}°`}
                style={{
                  width: "100%", height: "100%", objectFit: "cover",
                  filter: `brightness(0.7) hue-rotate(${angle / 4}deg) sepia(0.2)`,
                }}
              />
              <div style={{ position: "relative", marginTop: -18, textAlign: "center" }}>
                <span style={{ fontFamily: "monospace", fontSize: 9, color: "#3a4550" }}>{angle}°</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Artifact Selector Panel */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <h3 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 16, fontWeight: 700, color: "#D4AF37", marginBottom: 4, letterSpacing: 1 }}>
          选择文物
        </h3>
        {MODEL_ARTIFACTS.map((a) => (
          <button
            key={a.id}
            onClick={() => { setSelectedArtifact(a); setRotation(0); }}
            style={{
              display: "flex", gap: 12, alignItems: "center",
              background: selectedArtifact.id === a.id ? "rgba(69,162,158,0.1)" : "rgba(17,24,32,0.8)",
              border: `1px solid ${selectedArtifact.id === a.id ? "rgba(69,162,158,0.4)" : "rgba(255,255,255,0.06)"}`,
              borderRadius: 10, padding: "10px 14px", cursor: "pointer",
              textAlign: "left", transition: "all 0.2s",
            }}
            onMouseEnter={(e) => { if (selectedArtifact.id !== a.id) (e.currentTarget as HTMLElement).style.borderColor = "rgba(69,162,158,0.2)"; }}
            onMouseLeave={(e) => { if (selectedArtifact.id !== a.id) (e.currentTarget as HTMLElement).style.borderColor = "rgba(255,255,255,0.06)"; }}
          >
            <img src={a.thumb} alt={a.name} style={{ width: 48, height: 48, objectFit: "cover", borderRadius: 6, filter: "brightness(0.9)" }} />
            <div>
              <p style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 14, fontWeight: 700, color: selectedArtifact.id === a.id ? "#45A29E" : "#C5C6C7" }}>
                {a.name}
              </p>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#556372" }}>
                {a.year} · {a.material}
              </p>
            </div>
          </button>
        ))}

        {/* Info panel for selected */}
        <div style={{ background: "rgba(11,12,16,0.8)", border: "1px solid rgba(212,175,55,0.1)", borderRadius: 10, padding: 16, marginTop: 4 }}>
          <p style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 15, fontWeight: 700, color: "#D4AF37", marginBottom: 8 }}>{selectedArtifact.name}</p>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#8A9BAD", lineHeight: 1.7, marginBottom: 12 }}>
            {selectedArtifact.desc}
          </p>
          <button
            onClick={() => setQuery(selectedArtifact.aiQuery)}
            style={{
              display: "flex", alignItems: "center", gap: 6, justifyContent: "center",
              width: "100%", background: "rgba(69,162,158,0.08)",
              border: "1px solid rgba(69,162,158,0.2)", borderRadius: 6, padding: "8px",
              color: "#45A29E", fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, cursor: "pointer",
            }}
          >
            <MessageSquare size={12} /> 向智脑提问
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── EXCAVATION TAB ──────────────────────────────────────────────────────────

function ExcavationTab() {
  const [selected, setSelected] = useState(0);

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24, marginBottom: 32 }} className="max-md:block max-md:space-y-4">
        {/* Main photo */}
        <div>
          <motion.div
            key={selected}
            initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            style={{
              borderRadius: 12, overflow: "hidden", aspectRatio: "4/3",
              border: "1px solid rgba(212,175,55,0.15)",
            }}
          >
            <img src={EXCAVATION_PHOTOS[selected].img} alt={EXCAVATION_PHOTOS[selected].caption}
              style={{ width: "100%", height: "100%", objectFit: "cover", filter: "brightness(0.9) contrast(1.1)" }} />
          </motion.div>
          <div style={{ padding: "16px 0" }}>
            <h3 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 18, fontWeight: 700, color: "#D4AF37", marginBottom: 6 }}>
              {EXCAVATION_PHOTOS[selected].caption}
            </h3>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: "#8A9BAD", lineHeight: 1.7 }}>
              {EXCAVATION_PHOTOS[selected].desc}
            </p>
          </div>
        </div>

        {/* Thumbnail grid */}
        <div>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 3, marginBottom: 16 }}>
            考古现场实录
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            {EXCAVATION_PHOTOS.map((p, i) => (
              <div
                key={i}
                onClick={() => setSelected(i)}
                style={{
                  borderRadius: 8, overflow: "hidden", aspectRatio: "4/3",
                  border: `2px solid ${i === selected ? "rgba(212,175,55,0.5)" : "transparent"}`,
                  cursor: "pointer", transition: "border-color 0.2s",
                }}
              >
                <img src={p.img} alt={p.caption}
                  style={{ width: "100%", height: "100%", objectFit: "cover", filter: `brightness(${i === selected ? 1 : 0.65})`, transition: "filter 0.2s" }} />
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 16 }}>
        {[
          { num: "12 km²", label: "遗址总面积" },
          { num: "8", label: "已发掘祭祀坑" },
          { num: "13,000+", label: "出土象牙（根）" },
          { num: "50,000+", label: "出土文物总数" },
          { num: "60+", label: "考古发掘年数" },
          { num: "< 5%", label: "已发掘面积占比" },
        ].map((s, i) => (
          <div key={i} style={{
            background: "rgba(17,24,32,0.8)", border: "1px solid rgba(255,255,255,0.06)",
            borderRadius: 10, padding: "18px 20px", textAlign: "center",
          }}>
            <p style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 22, fontWeight: 900, color: "#D4AF37", marginBottom: 4 }}>{s.num}</p>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#556372" }}>{s.label}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── TIMELINE TAB ────────────────────────────────────────────────────────────

function TimelineTab() {
  return (
    <div style={{ maxWidth: 780, margin: "0 auto", position: "relative" }}>
      {/* Center line */}
      <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "linear-gradient(to bottom, transparent, rgba(212,175,55,0.3) 5%, rgba(212,175,55,0.3) 95%, transparent)", transform: "translateX(-50%)" }} />

      {TIMELINE.map((item, i) => (
        <motion.div
          key={i}
          initial={{ opacity: 0, x: i % 2 === 0 ? -30 : 30 }}
          whileInView={{ opacity: 1, x: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6, delay: i * 0.05 }}
          style={{
            display: "flex",
            justifyContent: i % 2 === 0 ? "flex-end" : "flex-start",
            paddingLeft: i % 2 === 0 ? 0 : "52%",
            paddingRight: i % 2 === 0 ? "52%" : 0,
            marginBottom: 32,
            position: "relative",
          }}
        >
          {/* Center dot */}
          <div style={{
            position: "absolute", left: "50%", top: 20, transform: "translateX(-50%)",
            width: 28, height: 28, borderRadius: "50%",
            background: i >= 6 ? "rgba(69,162,158,0.2)" : "rgba(212,175,55,0.2)",
            border: `2px solid ${i >= 6 ? "#45A29E" : "#D4AF37"}`,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 12, zIndex: 2, flexShrink: 0,
          }}>
            {item.icon}
          </div>

          <div style={{
            background: "#111820", border: `1px solid ${i >= 6 ? "rgba(69,162,158,0.2)" : "rgba(212,175,55,0.12)"}`,
            borderRadius: 12, padding: "16px 20px", maxWidth: "90%",
          }}>
            <p style={{ fontFamily: "monospace", fontSize: 12, color: i >= 6 ? "#45A29E" : "#D4AF37", marginBottom: 4, letterSpacing: 1 }}>
              {item.year}
            </p>
            <h3 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 15, fontWeight: 700, color: "#EFEFEF", marginBottom: 6 }}>
              {item.event}
            </h3>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#8A9BAD", lineHeight: 1.7 }}>
              {item.desc}
            </p>
          </div>
        </motion.div>
      ))}
    </div>
  );
}

// ─── MAIN PAGE ───────────────────────────────────────────────────────────────

export function MuseumPage() {
  const { tab } = useParams<{ tab: string }>();
  const navigate = useNavigate();
  const activeTab = tab || "gallery";

  return (
    <div style={{ paddingTop: 64, minHeight: "100vh" }}>
      {/* Page header */}
      <div style={{
        background: "linear-gradient(180deg, rgba(31,40,51,0.5) 0%, transparent 100%)",
        borderBottom: "1px solid rgba(212,175,55,0.08)",
        padding: "40px 0 0",
      }}>
        <div className="max-w-7xl mx-auto px-6">
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 5, marginBottom: 8 }}>
              DIGITAL MUSEUM / 沉浸式数字展厅
            </p>
            <h1 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: "clamp(24px,4vw,44px)", fontWeight: 900, color: "#EFEFEF", marginBottom: 4 }}>
              古蜀文明<span style={{ color: "#D4AF37" }}>数字展厅</span>
            </h1>
          </motion.div>

          {/* Tab bar */}
          <div style={{ display: "flex", gap: 0, marginTop: 32, borderBottom: "1px solid rgba(255,255,255,0.06)", overflowX: "auto" }}>
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => navigate(`/museum/${t.id}`)}
                style={{
                  padding: "12px 24px", background: "none", border: "none",
                  borderBottom: activeTab === t.id ? "2px solid #D4AF37" : "2px solid transparent",
                  color: activeTab === t.id ? "#D4AF37" : "#8A9BAD",
                  fontFamily: "'Noto Sans SC', sans-serif", fontSize: 14, cursor: "pointer",
                  transition: "color 0.2s", whiteSpace: "nowrap",
                  marginBottom: -1,
                }}
                onMouseEnter={(e) => { if (activeTab !== t.id) (e.currentTarget as HTMLElement).style.color = "#C5C6C7"; }}
                onMouseLeave={(e) => { if (activeTab !== t.id) (e.currentTarget as HTMLElement).style.color = "#8A9BAD"; }}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-7xl mx-auto px-6" style={{ paddingTop: 40, paddingBottom: 80 }}>
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.3 }}
          >
            {activeTab === "gallery" && <GalleryTab />}
            {activeTab === "3d" && <Model3DTab />}
            {activeTab === "excavation" && <ExcavationTab />}
            {activeTab === "timeline" && <TimelineTab />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}
