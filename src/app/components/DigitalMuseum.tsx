import { useState } from "react";
import { motion } from "motion/react";
import { MessageSquare, ChevronLeft, ChevronRight } from "lucide-react";

const ARTIFACTS = [
  {
    id: 1,
    name: "青铜大立人",
    nameEn: "Bronze Standing Figure",
    image: "https://images.unsplash.com/photo-1695902046953-1bf8caee5ac3?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=600",
    year: "约公元前1200年",
    height: "262 厘米（含底座）",
    material: "青铜",
    location: "四号祭祀坑",
    weight: "约180公斤",
    description: "三星堆最具代表性的文物，目前发现的同时代世界上最大、最完整的青铜立人雕像。双手呈环握状抬起，推测原本把握某种神圣器物，体现了古蜀王或大祭司的神圣地位。",
    mystery: "双手究竟握持何物至今成谜——有学者认为是象牙，也有人认为是权杖或祭祀玉器。其站姿与服饰暗示极高的社会地位，被认为是古蜀祭祀活动的核心人物形象。",
    aiQuery: "青铜大立人双手姿势有什么特殊含义？他可能是谁？",
    color: "#7C6D4E",
    glow: "rgba(124,109,78,0.4)",
  },
  {
    id: 2,
    name: "黄金面具",
    nameEn: "Gold Mask",
    image: "https://images.unsplash.com/photo-1775729841536-8335a857c94d?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=600",
    year: "约公元前1300年",
    height: "23.5 厘米",
    material: "金箔（含金量约85%）",
    location: "五号祭祀坑",
    weight: "约280克",
    description: "2021年新出土，是目前同时期国内最重的黄金面具，以金箔捶揲成型。面部特征抽象夸张，宽鼻大耳、方形面孔，是研究古蜀审美观念与神灵崇拜的重要实物。",
    mystery: "面具背面设有固定孔，推测原本附着于大型青铜头像上使用。其含金量高达85%，所用黄金可能来自古代蜀地的沙金淘洗，反映了古蜀对黄金的崇拜。",
    aiQuery: "黄金面具含金量有多高？它是如何制作出来的？",
    color: "#D4AF37",
    glow: "rgba(212,175,55,0.5)",
  },
  {
    id: 3,
    name: "青铜神树",
    nameEn: "Bronze Sacred Tree",
    image: "https://images.unsplash.com/photo-1761472651471-839cb6a57177?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=600",
    year: "约公元前1200年",
    height: "396 厘米",
    material: "青铜（分铸法）",
    location: "一号祭祀坑",
    weight: "约84公斤",
    description: "现存最高的三星堆青铜器，共三层树枝，每层三根共九根，每根树枝上各有花果，树枝末端各有一鸟。树干一侧有龙盘绕而下，代表天地沟通的宇宙树。",
    mystery: "神树与《山海经》中的「建木」高度吻合——建木是连接天界与人间的神圣之树，天帝与群神由此上下往来。树顶铸有神鸟，可能是远古太阳崇拜的具象体现。",
    aiQuery: "青铜神树与《山海经》有什么关系？它象征什么？",
    color: "#45A29E",
    glow: "rgba(69,162,158,0.4)",
  },
  {
    id: 4,
    name: "玉璋",
    nameEn: "Jade Zhang",
    image: "https://images.unsplash.com/photo-1744631244707-09d5d777034e?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=600",
    year: "约公元前1500年",
    height: "54.2 厘米",
    material: "透闪石玉",
    location: "二号祭祀坑",
    weight: "约320克",
    description: "玉璋是古代祭祀礼器「六器」之一，用于礼南方。三星堆出土的玉璋数量庞大，部分刻有精美纹饰，体现了古蜀与中原玉文化的深层交流与独特创造。",
    mystery: "部分玉璋上发现有祭祀人物图案，刻画了古蜀祭司跪拜的姿态，这是目前已知最早的祭祀场景图像之一，极为罕见。",
    aiQuery: "玉璋在古代祭祀中有什么用途？为什么三星堆出土了大量玉璋？",
    color: "#6BAF8E",
    glow: "rgba(107,175,142,0.4)",
  },
  {
    id: 5,
    name: "青铜纵目人面像",
    nameEn: "Bronze Protruding Eye Mask",
    image: "https://images.unsplash.com/photo-1763116987110-0e1bbe16a49e?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=600",
    year: "约公元前1100年",
    height: "65 厘米",
    material: "青铜",
    location: "二号祭祀坑",
    weight: "约3.5公斤",
    description: "三星堆标志性文物，眼球向外突出长达16厘米，耳廓极度外张，鼻梁高挺，造型超乎现实。这种「千里眼、顺风耳」的形象在古蜀神话中有明确记载。",
    mystery: "纵目的造型远超人类生理极限，考古学界认为这是对神灵「千里眼」的具象化表达。有研究者联系到蚕丛王「其目纵」的文献记载，认为这是蚕丛神的形象。",
    aiQuery: "青铜纵目面具的「纵目」造型有什么文化含义？",
    color: "#8B7355",
    glow: "rgba(139,115,85,0.4)",
  },
  {
    id: 6,
    name: "金杖",
    nameEn: "Gold Staff",
    image: "https://images.unsplash.com/photo-1634036891026-1a3a7571f82d?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=600",
    year: "约公元前1200年",
    height: "142 厘米",
    material: "金箔（含金量约94%）",
    location: "一号祭祀坑",
    weight: "约463克",
    description: "以金箔包裹木芯制成，是目前国内同时期最大的金器，代表最高政治与神权权威。杖身刻有精美图案：鱼、鸟、箭、人头，构成了一个完整的图语系统。",
    mystery: "金杖上的图案被认为是古蜀最高统治者（「鱼凫王」）的图腾符号。「鱼」与「凫（野鸭）」的组合印证了古蜀王朝的图腾崇拜，或为已失传的文字系统前身。",
    aiQuery: "金杖上的图案有什么含义？它是古蜀文字的起源吗？",
    color: "#D4AF37",
    glow: "rgba(212,175,55,0.4)",
  },
];

interface DigitalMuseumProps {
  onAskAssistant: (query: string) => void;
}

function ArtifactCard({
  artifact,
  onAskAssistant,
}: {
  artifact: (typeof ARTIFACTS)[0];
  onAskAssistant: (q: string) => void;
}) {
  const [flipped, setFlipped] = useState(false);

  return (
    <div
      style={{
        width: 300,
        height: 440,
        flexShrink: 0,
        perspective: 1200,
        cursor: "pointer",
      }}
      onClick={() => setFlipped(!flipped)}
    >
      <motion.div
        animate={{ rotateY: flipped ? 180 : 0 }}
        transition={{ duration: 0.65, ease: "easeInOut" }}
        style={{
          width: "100%",
          height: "100%",
          position: "relative",
          transformStyle: "preserve-3d",
        }}
      >
        {/* Front */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            backfaceVisibility: "hidden",
            WebkitBackfaceVisibility: "hidden",
            borderRadius: 12,
            overflow: "hidden",
            background: "#1F2833",
            border: `1px solid rgba(${artifact.color === "#D4AF37" ? "212,175,55" : "69,162,158"},0.2)`,
            boxShadow: `0 4px 30px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.03)`,
          }}
        >
          {/* Image */}
          <div style={{ height: 240, overflow: "hidden", position: "relative" }}>
            <img
              src={artifact.image}
              alt={artifact.name}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                filter: "brightness(0.85) contrast(1.1) sepia(0.2)",
                transition: "transform 0.4s",
              }}
              onMouseEnter={(e) => ((e.currentTarget as HTMLImageElement).style.transform = "scale(1.06)")}
              onMouseLeave={(e) => ((e.currentTarget as HTMLImageElement).style.transform = "scale(1)")}
            />
            {/* overlay gradient */}
            <div
              style={{
                position: "absolute",
                bottom: 0,
                left: 0,
                right: 0,
                height: 80,
                background: "linear-gradient(to top, #1F2833, transparent)",
              }}
            />
            {/* "CLICK TO FLIP" badge */}
            <div
              style={{
                position: "absolute",
                top: 12,
                right: 12,
                background: "rgba(11,12,16,0.7)",
                border: "1px solid rgba(212,175,55,0.3)",
                borderRadius: 4,
                padding: "3px 8px",
                color: "#D4AF37",
                fontSize: 10,
                fontFamily: "'Noto Sans SC', sans-serif",
                letterSpacing: 1,
              }}
            >
              点击翻转
            </div>
          </div>

          {/* Info */}
          <div style={{ padding: "20px 20px 16px" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
              <span
                style={{
                  fontFamily: "'Noto Serif SC', serif",
                  fontSize: 20,
                  fontWeight: 900,
                  color: "#EFEFEF",
                  letterSpacing: 1,
                }}
              >
                {artifact.name}
              </span>
            </div>
            <p
              style={{
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 11,
                fontWeight: 300,
                color: "#45A29E",
                letterSpacing: 2,
                marginBottom: 12,
              }}
            >
              {artifact.nameEn}
            </p>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
              {[
                { label: artifact.year, bg: "rgba(212,175,55,0.08)", color: "#D4AF37" },
                { label: artifact.material, bg: "rgba(69,162,158,0.08)", color: "#45A29E" },
              ].map((tag, i) => (
                <span
                  key={i}
                  style={{
                    background: tag.bg,
                    border: `1px solid ${tag.color}33`,
                    borderRadius: 4,
                    padding: "2px 8px",
                    fontSize: 11,
                    color: tag.color,
                    fontFamily: "'Noto Sans SC', sans-serif",
                  }}
                >
                  {tag.label}
                </span>
              ))}
            </div>
            {/* Ask AI button */}
            <button
              onClick={(e) => {
                e.stopPropagation();
                onAskAssistant(artifact.aiQuery);
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                background: "rgba(69,162,158,0.1)",
                border: "1px solid rgba(69,162,158,0.3)",
                borderRadius: 6,
                padding: "7px 14px",
                color: "#45A29E",
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 12,
                cursor: "pointer",
                width: "100%",
                justifyContent: "center",
                transition: "background 0.2s",
              }}
              onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "rgba(69,162,158,0.2)")}
              onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "rgba(69,162,158,0.1)")}
            >
              <MessageSquare size={13} />
              向智脑提问
            </button>
          </div>
        </div>

        {/* Back */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            backfaceVisibility: "hidden",
            WebkitBackfaceVisibility: "hidden",
            transform: "rotateY(180deg)",
            borderRadius: 12,
            overflow: "hidden",
            background: "#0B0C10",
            border: "1px solid rgba(212,175,55,0.25)",
            boxShadow: `0 0 60px ${artifact.glow}`,
            padding: 24,
            display: "flex",
            flexDirection: "column",
          }}
        >
          {/* Back header */}
          <div
            style={{
              borderBottom: "1px solid rgba(212,175,55,0.15)",
              paddingBottom: 16,
              marginBottom: 16,
            }}
          >
            <p
              style={{
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 10,
                color: "#45A29E",
                letterSpacing: 3,
                marginBottom: 4,
              }}
            >
              数字档案 / DIGITAL ARCHIVE
            </p>
            <h3
              style={{
                fontFamily: "'Noto Serif SC', serif",
                fontSize: 22,
                fontWeight: 900,
                color: "#D4AF37",
                letterSpacing: 1,
              }}
            >
              {artifact.name}
            </h3>
          </div>

          {/* Details grid */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 16px", marginBottom: 16 }}>
            {[
              { label: "年代", value: artifact.year },
              { label: "高度", value: artifact.height },
              { label: "材质", value: artifact.material },
              { label: "重量", value: artifact.weight },
              { label: "出土地点", value: artifact.location },
            ].map((item, i) => (
              <div key={i} style={i === 4 ? { gridColumn: "1/-1" } : {}}>
                <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#556372", marginBottom: 2 }}>
                  {item.label}
                </p>
                <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#C5C6C7", fontWeight: 400 }}>
                  {item.value}
                </p>
              </div>
            ))}
          </div>

          <p
            style={{
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 12,
              color: "#8A9BAD",
              lineHeight: 1.8,
              marginBottom: 12,
              flex: 1,
              overflow: "hidden",
            }}
          >
            {artifact.description}
          </p>

          {/* Mystery */}
          <div
            style={{
              background: "rgba(212,175,55,0.06)",
              border: "1px solid rgba(212,175,55,0.15)",
              borderRadius: 6,
              padding: "10px 12px",
              marginBottom: 14,
            }}
          >
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#D4AF37", marginBottom: 4, letterSpacing: 1 }}>
              ◆ 历史谜团
            </p>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#A89060", lineHeight: 1.7 }}>
              {artifact.mystery.substring(0, 80)}…
            </p>
          </div>

          <button
            onClick={(e) => {
              e.stopPropagation();
              onAskAssistant(artifact.aiQuery);
            }}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: "linear-gradient(135deg, rgba(212,175,55,0.15), rgba(212,175,55,0.05))",
              border: "1px solid rgba(212,175,55,0.3)",
              borderRadius: 6,
              padding: "9px 14px",
              color: "#D4AF37",
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 12,
              cursor: "pointer",
              justifyContent: "center",
              transition: "background 0.2s",
            }}
            onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "rgba(212,175,55,0.2)")}
            onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "rgba(212,175,55,0.1)")}
          >
            <MessageSquare size={13} />
            向智脑提问此文物
          </button>
        </div>
      </motion.div>
    </div>
  );
}

export function DigitalMuseum({ onAskAssistant }: DigitalMuseumProps) {
  const [startIdx, setStartIdx] = useState(0);
  const visibleCount = 3;

  const prev = () => setStartIdx((i) => Math.max(0, i - 1));
  const next = () => setStartIdx((i) => Math.min(ARTIFACTS.length - visibleCount, i + 1));

  return (
    <section
      id="museum"
      style={{
        background: "linear-gradient(180deg, #0B0C10 0%, #111820 50%, #0B0C10 100%)",
        padding: "100px 0 80px",
        position: "relative",
        overflow: "hidden",
      }}
    >
      {/* Background decorative line */}
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

      {/* Section header */}
      <div className="max-w-7xl mx-auto px-6" style={{ marginBottom: 60 }}>
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8 }}
        >
          <p
            style={{
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 11,
              fontWeight: 300,
              color: "#45A29E",
              letterSpacing: 6,
              marginBottom: 12,
              textTransform: "uppercase",
            }}
          >
            Digital Museum / 沉浸式数字展厅
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
            古蜀文明<span style={{ color: "#D4AF37" }}>核心文物</span>
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
            点击卡片查看数字档案，了解每件文物背后的历史谜团，并可直接向古蜀智脑提问。
          </p>
        </motion.div>
      </div>

      {/* Cards carousel */}
      <div style={{ position: "relative", overflow: "hidden", padding: "20px 0" }}>
        <motion.div
          animate={{ x: -(startIdx * 316) }}
          transition={{ duration: 0.5, ease: "easeInOut" }}
          style={{
            display: "flex",
            gap: 16,
            paddingLeft: "max(24px, calc((100vw - 964px)/2))",
            paddingRight: 24,
          }}
        >
          {ARTIFACTS.map((artifact, idx) => (
            <motion.div
              key={artifact.id}
              initial={{ opacity: 0, y: 40 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.6, delay: (idx % 3) * 0.1 }}
            >
              <ArtifactCard artifact={artifact} onAskAssistant={onAskAssistant} />
            </motion.div>
          ))}
        </motion.div>
      </div>

      {/* Navigation */}
      <div className="max-w-7xl mx-auto px-6" style={{ marginTop: 40, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <p
          style={{
            fontFamily: "'Noto Sans SC', sans-serif",
            fontSize: 13,
            color: "#556372",
          }}
        >
          共 {ARTIFACTS.length} 件文物 · 点击卡片翻转查看详情
        </p>
        <div style={{ display: "flex", gap: 10 }}>
          <button
            onClick={prev}
            disabled={startIdx === 0}
            style={{
              width: 40,
              height: 40,
              borderRadius: "50%",
              background: "rgba(69,162,158,0.1)",
              border: "1px solid rgba(69,162,158,0.3)",
              color: startIdx === 0 ? "#3a4550" : "#45A29E",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              cursor: startIdx === 0 ? "not-allowed" : "pointer",
              transition: "background 0.2s",
            }}
          >
            <ChevronLeft size={18} />
          </button>
          <button
            onClick={next}
            disabled={startIdx >= ARTIFACTS.length - visibleCount}
            style={{
              width: 40,
              height: 40,
              borderRadius: "50%",
              background: "rgba(69,162,158,0.1)",
              border: "1px solid rgba(69,162,158,0.3)",
              color: startIdx >= ARTIFACTS.length - visibleCount ? "#3a4550" : "#45A29E",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              cursor: startIdx >= ARTIFACTS.length - visibleCount ? "not-allowed" : "pointer",
              transition: "background 0.2s",
            }}
          >
            <ChevronRight size={18} />
          </button>
        </div>
      </div>
    </section>
  );
}