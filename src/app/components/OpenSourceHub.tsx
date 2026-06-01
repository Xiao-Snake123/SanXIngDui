import { motion } from "motion/react";
import { Github, Download, FileText, Database, Code2, ExternalLink, Star, GitFork } from "lucide-react";

const RESOURCES = [
  {
    icon: <Code2 size={18} />,
    category: "模型权重",
    title: "SanxingduiLoRA-v1.2",
    description: "基于 SDXL 微调的古蜀风格 LoRA 权重，包含青铜器、黄金面具、神树等核心文物训练数据集，支持 ComfyUI / A1111 部署。",
    size: "2.4 GB",
    format: "safetensors",
    stars: 1847,
    forks: 312,
    tag: "Model",
    tagColor: "#45A29E",
  },
  {
    icon: <Database size={18} />,
    category: "数据集",
    title: "AncientShu-Dataset-10K",
    description: "包含 10,000+ 张经专业考古学者标注的三星堆及古蜀文物图像数据集，附带 BLIP2 自动打标结果与人工修正标注。",
    size: "8.7 GB",
    format: "parquet + jpg",
    stars: 923,
    forks: 187,
    tag: "Dataset",
    tagColor: "#D4AF37",
  },
  {
    icon: <FileText size={18} />,
    category: "研究报告",
    title: "古蜀文明 AI 传播架构白皮书",
    description: "详述本项目技术路线、RAG 知识库构建方案、多智能体设计原理，以及文化遗产数字化传播的 UX 方法论研究。",
    size: "4.2 MB",
    format: "PDF",
    stars: 2341,
    forks: 456,
    tag: "Paper",
    tagColor: "#8B7355",
  },
];

const API_DOCS = [
  {
    method: "POST",
    endpoint: "/api/v1/generate",
    desc: "根据结构化提示词生成古蜀复原图",
    color: "#45A29E",
  },
  {
    method: "POST",
    endpoint: "/api/v1/rag/query",
    desc: "查询 RAG 知识库，返回考古文献答案",
    color: "#D4AF37",
  },
  {
    method: "GET",
    endpoint: "/api/v1/artifacts",
    desc: "获取所有数字档案文物元数据",
    color: "#6BAF8E",
  },
  {
    method: "GET",
    endpoint: "/api/v1/artifacts/:id",
    desc: "获取指定文物的详细数字档案",
    color: "#6BAF8E",
  },
  {
    method: "POST",
    endpoint: "/api/v1/poster",
    desc: "生成考古纪念海报（含 LLM 文案）",
    color: "#45A29E",
  },
];

export function OpenSourceHub() {
  return (
    <section
      id="open-source"
      style={{
        background: "#0B0C10",
        padding: "100px 0 80px",
        borderTop: "1px solid rgba(69,162,158,0.08)",
        position: "relative",
      }}
    >
      {/* Top decor line */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: "50%",
          transform: "translateX(-50%)",
          width: 1,
          height: 60,
          background: "linear-gradient(to bottom, transparent, rgba(69,162,158,0.4))",
        }}
      />

      <div className="max-w-7xl mx-auto px-6">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          style={{ marginBottom: 60, display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 24 }}
        >
          <div>
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
              Open Source Hub / 开源与开发者社区
            </p>
            <h2
              style={{
                fontFamily: "'Noto Serif SC', serif",
                fontSize: "clamp(28px, 4vw, 52px)",
                fontWeight: 900,
                color: "#EFEFEF",
                lineHeight: 1.2,
              }}
            >
              共建<span style={{ color: "#45A29E" }}>古蜀数字</span>生态
            </h2>
          </div>
          <a
            href="https://github.com"
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 10,
              padding: "12px 20px",
              color: "#C5C6C7",
              textDecoration: "none",
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 14,
              transition: "border-color 0.2s, background 0.2s",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLElement).style.borderColor = "rgba(69,162,158,0.4)";
              (e.currentTarget as HTMLElement).style.background = "rgba(69,162,158,0.05)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.borderColor = "rgba(255,255,255,0.1)";
              (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.04)";
            }}
          >
            <Github size={18} />
            View on GitHub
            <ExternalLink size={13} color="#45A29E" />
          </a>
        </motion.div>

        {/* Resources grid */}
        <div
          style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 20, marginBottom: 60 }}
        >
          {RESOURCES.map((res, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.1 }}
              style={{
                background: "#111820",
                border: "1px solid rgba(255,255,255,0.06)",
                borderRadius: 12,
                padding: 24,
                transition: "border-color 0.2s, box-shadow 0.2s",
                cursor: "default",
              }}
              whileHover={{
                boxShadow: "0 4px 30px rgba(69,162,158,0.1)",
                borderColor: "rgba(69,162,158,0.2)",
              }}
            >
              {/* Card header */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    color: res.tagColor,
                  }}
                >
                  {res.icon}
                  <span
                    style={{
                      fontFamily: "'Noto Sans SC', sans-serif",
                      fontSize: 11,
                      letterSpacing: 2,
                    }}
                  >
                    {res.category}
                  </span>
                </div>
                <span
                  style={{
                    background: `${res.tagColor}15`,
                    border: `1px solid ${res.tagColor}33`,
                    borderRadius: 4,
                    padding: "2px 8px",
                    fontFamily: "monospace",
                    fontSize: 11,
                    color: res.tagColor,
                  }}
                >
                  {res.tag}
                </span>
              </div>

              <h3
                style={{
                  fontFamily: "monospace",
                  fontSize: 15,
                  color: "#EFEFEF",
                  marginBottom: 10,
                  letterSpacing: 0.5,
                }}
              >
                {res.title}
              </h3>
              <p
                style={{
                  fontFamily: "'Noto Sans SC', sans-serif",
                  fontSize: 13,
                  color: "#556372",
                  lineHeight: 1.7,
                  marginBottom: 20,
                }}
              >
                {res.description}
              </p>

              {/* Meta info */}
              <div style={{ display: "flex", gap: 16, marginBottom: 20 }}>
                {[
                  { label: "大小", value: res.size },
                  { label: "格式", value: res.format },
                ].map((meta, j) => (
                  <div key={j}>
                    <p style={{ fontFamily: "monospace", fontSize: 10, color: "#3a4550", marginBottom: 2 }}>{meta.label}</p>
                    <p style={{ fontFamily: "monospace", fontSize: 12, color: "#8A9BAD" }}>{meta.value}</p>
                  </div>
                ))}
              </div>

              {/* Stats + actions */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", gap: 14 }}>
                  <span style={{ display: "flex", alignItems: "center", gap: 4, fontFamily: "monospace", fontSize: 12, color: "#556372" }}>
                    <Star size={12} />
                    {res.stars.toLocaleString()}
                  </span>
                  <span style={{ display: "flex", alignItems: "center", gap: 4, fontFamily: "monospace", fontSize: 12, color: "#556372" }}>
                    <GitFork size={12} />
                    {res.forks}
                  </span>
                </div>
                <button
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    background: "rgba(69,162,158,0.1)",
                    border: "1px solid rgba(69,162,158,0.25)",
                    borderRadius: 6,
                    padding: "6px 14px",
                    color: "#45A29E",
                    fontFamily: "monospace",
                    fontSize: 12,
                    cursor: "pointer",
                    transition: "background 0.2s",
                  }}
                  onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "rgba(69,162,158,0.2)")}
                  onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "rgba(69,162,158,0.1)")}
                >
                  <Download size={12} />
                  下载
                </button>
              </div>
            </motion.div>
          ))}
        </div>

        {/* API Documentation */}
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          style={{
            background: "#0D1117",
            border: "1px solid rgba(255,255,255,0.06)",
            borderRadius: 16,
            overflow: "hidden",
          }}
        >
          {/* API header */}
          <div
            style={{
              padding: "20px 28px",
              borderBottom: "1px solid rgba(255,255,255,0.06)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Code2 size={16} color="#45A29E" />
              <span
                style={{
                  fontFamily: "monospace",
                  fontSize: 14,
                  color: "#C5C6C7",
                  letterSpacing: 1,
                }}
              >
                API Reference
              </span>
              <span
                style={{
                  background: "rgba(69,162,158,0.1)",
                  border: "1px solid rgba(69,162,158,0.2)",
                  borderRadius: 4,
                  padding: "1px 8px",
                  fontFamily: "monospace",
                  fontSize: 10,
                  color: "#45A29E",
                }}
              >
                v1.0
              </span>
            </div>
            <span
              style={{
                fontFamily: "monospace",
                fontSize: 12,
                color: "#3a4550",
              }}
            >
              Base URL: https://api.sanxingdui.ai
            </span>
          </div>

          {/* Endpoints */}
          <div style={{ padding: 4 }}>
            {API_DOCS.map((api, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 16,
                  padding: "14px 24px",
                  borderBottom: i < API_DOCS.length - 1 ? "1px solid rgba(255,255,255,0.04)" : "none",
                  transition: "background 0.15s",
                  cursor: "default",
                }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.02)")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "transparent")}
              >
                <span
                  style={{
                    fontFamily: "monospace",
                    fontSize: 11,
                    color: api.color,
                    background: `${api.color}12`,
                    border: `1px solid ${api.color}30`,
                    borderRadius: 4,
                    padding: "2px 8px",
                    minWidth: 48,
                    textAlign: "center",
                    letterSpacing: 1,
                  }}
                >
                  {api.method}
                </span>
                <code
                  style={{
                    fontFamily: "monospace",
                    fontSize: 13,
                    color: "#8A9BAD",
                    minWidth: 220,
                  }}
                >
                  {api.endpoint}
                </code>
                <span
                  style={{
                    fontFamily: "'Noto Sans SC', sans-serif",
                    fontSize: 13,
                    color: "#556372",
                    flex: 1,
                  }}
                >
                  {api.desc}
                </span>
                <ExternalLink size={13} color="#3a4550" />
              </div>
            ))}
          </div>
        </motion.div>

        {/* Footer */}
        <div style={{ marginTop: 80, paddingTop: 40, borderTop: "1px solid rgba(255,255,255,0.06)", textAlign: "center" }}>
          <p
            style={{
              fontFamily: "'Noto Serif SC', serif",
              fontSize: 22,
              fontWeight: 700,
              color: "#D4AF37",
              letterSpacing: 3,
              marginBottom: 8,
            }}
          >
            三星堆 · 古蜀神韵
          </p>
          <p
            style={{
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 12,
              color: "#3a4550",
              letterSpacing: 2,
              marginBottom: 24,
            }}
          >
            Sanxingdui Digital Heritage Project · AI-Powered Cultural Transmission
          </p>
          <div style={{ display: "flex", justifyContent: "center", gap: 24, flexWrap: "wrap" }}>
            {["关于我们", "考古合规声明", "数据授权协议", "联系我们", "GitHub"].map((link, i) => (
              <a
                key={i}
                href="#"
                style={{
                  fontFamily: "'Noto Sans SC', sans-serif",
                  fontSize: 12,
                  color: "#3a4550",
                  textDecoration: "none",
                  transition: "color 0.2s",
                }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.color = "#45A29E")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.color = "#3a4550")}
              >
                {link}
              </a>
            ))}
          </div>
          <p
            style={{
              fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 11,
              color: "#2a3540",
              marginTop: 24,
            }}
          >
            © 2026 三星堆数字传播计划 · 本项目仅供学术研究与文化推广，AI 生成内容不代表历史事实
          </p>
        </div>
      </div>
    </section>
  );
}
