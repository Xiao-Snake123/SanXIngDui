import { useRef, useEffect, useState } from "react";
import { motion } from "motion/react";
import { ChevronDown } from "lucide-react";

const HERO_BG = "https://images.unsplash.com/photo-1706552604002-f7efaebebca6?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=1920";
const HERO_ARTIFACT = "https://sxd-tx-1315371622.cos.ap-nanjing.myqcloud.com/cloud/policy/1688384343223_KHMb6w7J.png?imageMogr2/format/webp/ignore-error/1";

interface HeroSectionProps {
  onScrollTo: (id: string) => void;
}

export function HeroSection({ onScrollTo }: HeroSectionProps) {
  const sectionRef = useRef<HTMLDivElement>(null);
  const [mouse, setMouse] = useState({ x: 0, y: 0 });
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setTimeout(() => setLoaded(true), 200);

    const handleMouse = (e: MouseEvent) => {
      const cx = window.innerWidth / 2;
      const cy = window.innerHeight / 2;
      setMouse({
        x: (e.clientX - cx) / cx,
        y: (e.clientY - cy) / cy,
      });
    };
    window.addEventListener("mousemove", handleMouse);
    return () => window.removeEventListener("mousemove", handleMouse);
  }, []);

  const px = mouse.x;
  const py = mouse.y;

  return (
    <section
      id="hero"
      ref={sectionRef}
      style={{
        position: "relative",
        width: "100%",
        height: "100vh",
        overflow: "hidden",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0B0C10",
      }}
    >
      {/* Background layer – slowest parallax */}
      <div
        style={{
          position: "absolute",
          inset: "-6%",
          backgroundImage: `url(${HERO_BG})`,
          backgroundSize: "cover",
          backgroundPosition: "center",
          transform: `translate(${px * -10}px, ${py * -10}px)`,
          transition: "transform 0.1s linear",
          opacity: 0.18,
          filter: "blur(1px)",
        }}
      />

      {/* Dark gradient overlay */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse at 50% 60%, rgba(11,12,16,0.3) 0%, rgba(11,12,16,0.85) 70%)",
          zIndex: 1,
        }}
      />

      {/* Bronze texture strips */}
      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          right: 0,
          height: 3,
          background: "linear-gradient(90deg, transparent, #D4AF37, transparent)",
          zIndex: 2,
          opacity: 0.6,
        }}
      />

      {/* Artifact image layer – mid parallax */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          paddingRight: "8%",
          zIndex: 2,
          pointerEvents: "none",
          transform: `translate(${px * 18}px, ${py * 12}px)`,
          transition: "transform 0.12s linear",
        }}
      >
        <div
          style={{
            width: "min(380px, 38vw)",
            aspectRatio: "3/4",
            position: "relative",
            opacity: loaded ? 0.55 : 0,
            transition: "opacity 1.2s ease",
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: 0,
              backgroundImage: `url(${HERO_ARTIFACT})`,
              backgroundSize: "cover",
              backgroundPosition: "center top",
              borderRadius: 4,
              maskImage: "linear-gradient(to bottom, rgba(0,0,0,0) 0%, rgba(0,0,0,0.9) 15%, rgba(0,0,0,0.9) 75%, rgba(0,0,0,0) 100%)",
              WebkitMaskImage: "linear-gradient(to bottom, rgba(0,0,0,0) 0%, rgba(0,0,0,0.9) 15%, rgba(0,0,0,0.9) 75%, rgba(0,0,0,0) 100%)",
            }}
          />
          {/* Gold border glow */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              border: "1px solid rgba(212,175,55,0.25)",
              borderRadius: 4,
              boxShadow: "0 0 60px rgba(212,175,55,0.1), inset 0 0 40px rgba(69,162,158,0.05)",
            }}
          />
        </div>
      </div>

      {/* Main content – fastest parallax */}
      <div
        style={{
          position: "relative",
          zIndex: 3,
          textAlign: "center",
          padding: "0 24px",
          maxWidth: 800,
          transform: `translate(${px * -8}px, ${py * -6}px)`,
          transition: "transform 0.15s linear",
        }}
      >
        {/* Decorative top line */}
        <motion.div
          initial={{ opacity: 0, scaleX: 0 }}
          animate={{ opacity: 1, scaleX: 1 }}
          transition={{ duration: 1, delay: 0.3 }}
          style={{
            width: 60,
            height: 2,
            background: "#D4AF37",
            margin: "0 auto 24px",
          }}
        />

        <motion.p
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.5 }}
          style={{
            fontFamily: "'Noto Sans SC', sans-serif",
            fontSize: 13,
            fontWeight: 300,
            color: "#45A29E",
            letterSpacing: 8,
            marginBottom: 20,
            textTransform: "uppercase",
          }}
        >
          Sanxingdui Digital Heritage Project
        </motion.p>

        <motion.h1
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 1, delay: 0.7 }}
          style={{
            fontFamily: "'Noto Serif SC', serif",
            fontSize: "clamp(32px, 5vw, 72px)",
            fontWeight: 900,
            color: "#EFEFEF",
            lineHeight: 1.2,
            marginBottom: 16,
            textShadow: "0 2px 40px rgba(212,175,55,0.3)",
          }}
        >
          用 AI 唤醒沉睡
          <br />
          <span style={{ color: "#D4AF37" }}>三千年</span>的古蜀神韵
        </motion.h1>

        <motion.p
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 1.0 }}
          style={{
            fontFamily: "'Noto Sans SC', sans-serif",
            fontSize: "clamp(14px, 1.5vw, 18px)",
            fontWeight: 300,
            color: "#8A9BAD",
            letterSpacing: 2,
            marginBottom: 48,
            lineHeight: 1.8,
          }}
        >
          以数字技术为媒，以考古发现为本
          <br />
          重现古蜀文明的神秘与辉煌
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 1.2 }}
          style={{ display: "flex", gap: 16, justifyContent: "center", flexWrap: "wrap" }}
        >
          <button
            onClick={() => onScrollTo("museum")}
            style={{
              background: "linear-gradient(135deg, #D4AF37, #a8861e)",
              border: "none",
              borderRadius: 8,
              padding: "14px 36px",
              color: "#0B0C10",
              fontFamily: "'Noto Serif SC', serif",
              fontSize: 16,
              fontWeight: 700,
              cursor: "pointer",
              letterSpacing: 3,
              boxShadow: "0 0 30px rgba(212,175,55,0.4)",
              transition: "transform 0.2s, box-shadow 0.2s",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLElement).style.transform = "translateY(-2px)";
              (e.currentTarget as HTMLElement).style.boxShadow = "0 8px 40px rgba(212,175,55,0.6)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.transform = "translateY(0)";
              (e.currentTarget as HTMLElement).style.boxShadow = "0 0 30px rgba(212,175,55,0.4)";
            }}
          >
            ✦ 开启时光大门
          </button>

          <button
            onClick={() => onScrollTo("ai-restore")}
            style={{
              background: "transparent",
              border: "1px solid rgba(69,162,158,0.5)",
              borderRadius: 8,
              padding: "14px 36px",
              color: "#45A29E",
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 15,
              fontWeight: 400,
              cursor: "pointer",
              letterSpacing: 2,
              transition: "background 0.2s, border-color 0.2s",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLElement).style.background = "rgba(69,162,158,0.1)";
              (e.currentTarget as HTMLElement).style.borderColor = "rgba(69,162,158,0.8)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.background = "transparent";
              (e.currentTarget as HTMLElement).style.borderColor = "rgba(69,162,158,0.5)";
            }}
          >
            AI 复原引擎
          </button>
        </motion.div>
      </div>

      {/* Scroll indicator */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 2 }}
        style={{
          position: "absolute",
          bottom: 32,
          left: "50%",
          transform: "translateX(-50%)",
          zIndex: 4,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 6,
          cursor: "pointer",
          color: "#45A29E",
        }}
        onClick={() => onScrollTo("museum")}
      >
        <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, letterSpacing: 4, color: "#8A9BAD" }}>
          SCROLL
        </span>
        <motion.div
          animate={{ y: [0, 6, 0] }}
          transition={{ duration: 1.5, repeat: Infinity }}
        >
          <ChevronDown size={20} />
        </motion.div>
      </motion.div>

      {/* Corner decorations */}
      {[
        { top: 80, left: 24, borderTop: true, borderLeft: true },
        { top: 80, right: 24, borderTop: true, borderRight: true },
        { bottom: 48, left: 24, borderBottom: true, borderLeft: true },
        { bottom: 48, right: 24, borderBottom: true, borderRight: true },
      ].map((corner, i) => (
        <div
          key={i}
          style={{
            position: "absolute",
            width: 24,
            height: 24,
            borderTop: corner.borderTop ? "1px solid rgba(212,175,55,0.4)" : "none",
            borderBottom: corner.borderBottom ? "1px solid rgba(212,175,55,0.4)" : "none",
            borderLeft: corner.borderLeft ? "1px solid rgba(212,175,55,0.4)" : "none",
            borderRight: corner.borderRight ? "1px solid rgba(212,175,55,0.4)" : "none",
            top: corner.top,
            bottom: corner.bottom,
            left: corner.left,
            right: corner.right,
            zIndex: 4,
          }}
        />
      ))}
    </section>
  );
}
