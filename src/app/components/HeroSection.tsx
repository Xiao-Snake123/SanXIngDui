import { useRef, useEffect, useState } from "react";
import { motion } from "motion/react";
import { ChevronDown, Maximize2, Pause, Play } from "lucide-react";

const HERO_BG = "https://images.unsplash.com/photo-1706552604002-f7efaebebca6?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=1920";
const HERO_ARTIFACT_VIDEO = "/videos/287384434.mp4";

interface HeroSectionProps {
  onScrollTo: (id: string) => void;
}

export function HeroSection({ onScrollTo }: HeroSectionProps) {
  const sectionRef = useRef<HTMLDivElement>(null);
  const artifactFrameRef = useRef<HTMLDivElement>(null);
  const artifactVideoRef = useRef<HTMLVideoElement>(null);
  const [mouse, setMouse] = useState({ x: 0, y: 0 });
  const [loaded, setLoaded] = useState(false);
  const [artifactReady, setArtifactReady] = useState(false);
  const [artifactHovered, setArtifactHovered] = useState(false);
  const [artifactPlaying, setArtifactPlaying] = useState(true);
  const [artifactProgress, setArtifactProgress] = useState(0);

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

  const updateArtifactProgress = () => {
    const video = artifactVideoRef.current;
    if (!video?.duration) {
      setArtifactProgress(0);
      return;
    }

    setArtifactProgress((video.currentTime / video.duration) * 100);
  };

  const toggleArtifactPlayback = () => {
    const video = artifactVideoRef.current;
    if (!video) {
      return;
    }

    if (video.paused) {
      void video.play();
    } else {
      video.pause();
    }
  };

  const seekArtifactVideo = (value: number) => {
    const video = artifactVideoRef.current;
    if (!video?.duration) {
      return;
    }

    video.currentTime = (value / 100) * video.duration;
    setArtifactProgress(value);
  };

  const openArtifactFullscreen = () => {
    const frame = artifactFrameRef.current;
    if (!frame) {
      return;
    }

    const fullscreenTarget = frame as HTMLDivElement & {
      webkitRequestFullscreen?: () => Promise<void>;
      msRequestFullscreen?: () => Promise<void>;
    };

    if (fullscreenTarget.requestFullscreen) {
      void fullscreenTarget.requestFullscreen();
      return;
    }

    if (fullscreenTarget.webkitRequestFullscreen) {
      void fullscreenTarget.webkitRequestFullscreen();
      return;
    }

    if (fullscreenTarget.msRequestFullscreen) {
      void fullscreenTarget.msRequestFullscreen();
    }
  };

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
      <style>{`
        .hero-artifact-frame:fullscreen {
          width: 100vw !important;
          height: 100vh !important;
          aspect-ratio: auto !important;
          background: #050607;
        }

        .hero-artifact-frame:fullscreen video {
          object-fit: contain !important;
        }
      `}</style>

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

      {/* Artifact video layer – mid parallax */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          paddingRight: "clamp(180px, 10vw, 180px)",
          zIndex: 2,
          pointerEvents: "none",
          transform: `translate(${px * 18}px, ${py * 12}px)`,
          transition: "transform 0.12s linear",
        }}
      >
        <div
          className="hero-artifact-frame"
          ref={artifactFrameRef}
          onMouseEnter={() => setArtifactHovered(true)}
          onMouseLeave={() => setArtifactHovered(false)}
          style={{
            width: "min(580px, 40vw)",
            aspectRatio: "16/9",
            position: "relative",
            opacity: loaded && artifactReady ? 0.66 : 0,
            transition: "opacity 1.2s ease",
            pointerEvents: "auto",
          }}
        >
          <video
            ref={artifactVideoRef}
            autoPlay
            muted
            loop
            playsInline
            preload="metadata"
            src={HERO_ARTIFACT_VIDEO}
            onCanPlay={() => setArtifactReady(true)}
            onTimeUpdate={updateArtifactProgress}
            onLoadedMetadata={updateArtifactProgress}
            onPlay={() => setArtifactPlaying(true)}
            onPause={() => setArtifactPlaying(false)}
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              objectFit: "cover",
              objectPosition: "center",
              borderRadius: 4,
              filter: "brightness(0.82) contrast(1.18) saturate(0.95) sepia(0.18)",
              maskImage: "linear-gradient(to bottom, rgba(0,0,0,0) 0%, rgba(0,0,0,0.95) 12%, rgba(0,0,0,0.95) 82%, rgba(0,0,0,0) 100%)",
              WebkitMaskImage: "linear-gradient(to bottom, rgba(0,0,0,0) 0%, rgba(0,0,0,0.95) 12%, rgba(0,0,0,0.95) 82%, rgba(0,0,0,0) 100%)",
            }}
          />
          <div
            style={{
              position: "absolute",
              inset: 0,
              borderRadius: 4,
              background:
                "linear-gradient(135deg, rgba(212,175,55,0.22), rgba(69,162,158,0.08) 42%, rgba(11,12,16,0.22)), radial-gradient(circle at 18% 20%, rgba(212,175,55,0.18), transparent 34%)",
              mixBlendMode: "screen",
              pointerEvents: "none",
            }}
          />
          <motion.div
            initial={false}
            animate={{ opacity: artifactHovered ? 1 : 0, y: artifactHovered ? 0 : 8 }}
            transition={{ duration: 0.2 }}
            style={{
              position: "absolute",
              left: 12,
              right: 12,
              bottom: 12,
              zIndex: 3,
              display: "grid",
              gridTemplateColumns: "34px 1fr 34px",
              alignItems: "center",
              gap: 10,
              padding: "9px 10px",
              borderRadius: 6,
              background: "linear-gradient(180deg, rgba(11,12,16,0.68), rgba(11,12,16,0.9))",
              border: "1px solid rgba(212,175,55,0.22)",
              boxShadow: "0 10px 28px rgba(0,0,0,0.35)",
              backdropFilter: "blur(10px)",
              pointerEvents: artifactHovered ? "auto" : "none",
            }}
          >
            <button
              type="button"
              title={artifactPlaying ? "暂停" : "播放"}
              onClick={toggleArtifactPlayback}
              style={{
                width: 34,
                height: 34,
                display: "grid",
                placeItems: "center",
                borderRadius: 5,
                border: "1px solid rgba(212,175,55,0.35)",
                background: "rgba(212,175,55,0.12)",
                color: "#D4AF37",
                cursor: "pointer",
              }}
            >
              {artifactPlaying ? <Pause size={15} /> : <Play size={15} />}
            </button>

            <input
              type="range"
              min={0}
              max={100}
              step={0.1}
              aria-label="视频进度"
              value={artifactProgress}
              onChange={(event) => seekArtifactVideo(Number(event.currentTarget.value))}
              style={{
                width: "100%",
                height: 4,
                margin: 0,
                accentColor: "#D4AF37",
                cursor: "pointer",
              }}
            />

            <button
              type="button"
              title="全屏"
              onClick={openArtifactFullscreen}
              style={{
                width: 34,
                height: 34,
                display: "grid",
                placeItems: "center",
                borderRadius: 5,
                border: "1px solid rgba(69,162,158,0.35)",
                background: "rgba(69,162,158,0.12)",
                color: "#45A29E",
                cursor: "pointer",
              }}
            >
              <Maximize2 size={15} />
            </button>
          </motion.div>
          <div
            style={{
              position: "absolute",
              inset: "-12%",
              borderRadius: 8,
              background: "radial-gradient(ellipse at center, rgba(212,175,55,0.16), transparent 62%)",
              filter: "blur(18px)",
              zIndex: -1,
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
          position: "absolute",
          left: "clamp(120px, 16vw, 320px)",
          top: "52%",
          zIndex: 3,
          textAlign: "left",
          width: "min(700px, 44vw)",
          transform: `translate3d(${px * -8}px, calc(-50% + ${py * -6}px), 0)`,
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
            margin: "0 0 24px",
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
            letterSpacing: 7,
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
            fontSize: "clamp(36px, 4.2vw, 68px)",
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
          style={{ display: "flex", gap: 16, justifyContent: "flex-start", flexWrap: "wrap" }}
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
