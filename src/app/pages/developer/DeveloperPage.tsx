import { useParams, useNavigate } from "react-router";
import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Github, Download, Star, GitFork, Copy, Check, ExternalLink, Code2, Database, FileText, Terminal, Package, Users, MessageSquare } from "lucide-react";

const TABS = [
  { id: "resources", label: "开源资源" },
  { id: "api", label: "API 文档" },
  { id: "models", label: "模型广场" },
  { id: "contribute", label: "参与贡献" },
];

// ─── RESOURCES TAB ───────────────────────────────────────────────────────────
const RESOURCES = [
  {
    icon: Code2, category: "模型权重", title: "SanxingduiLoRA-v1.2",
    desc: "基于 SDXL 微调的古蜀风格 LoRA 权重，包含青铜器、黄金面具、神树等核心文物训练数据，支持 ComfyUI / A1111 / InvokeAI 部署。",
    size: "2.4 GB", format: "safetensors", stars: 1847, forks: 312, tag: "Model", tagColor: "#45A29E",
    updated: "2026-03-14",
  },
  {
    icon: Database, category: "数据集", title: "AncientShu-Dataset-10K",
    desc: "10,000+ 张经考古学者标注的三星堆文物图像，附带 BLIP2 自动打标与人工修正标注，包含 caption、材质、年代等元数据。",
    size: "8.7 GB", format: "parquet + jpg", stars: 923, forks: 187, tag: "Dataset", tagColor: "#D4AF37",
    updated: "2026-02-28",
  },
  {
    icon: FileText, category: "研究报告", title: "古蜀文明 AI 传播白皮书",
    desc: "详述本项目技术路线、RAG 知识库构建方案、多智能体设计原理，以及文化遗产数字化传播的 UX 方法论研究。v2.1 版新增多模态方案。",
    size: "4.2 MB", format: "PDF", stars: 2341, forks: 456, tag: "Paper", tagColor: "#8B7355",
    updated: "2026-04-01",
  },
  {
    icon: Package, category: "评估基准", title: "SanxingduiBench-v1.0",
    desc: "古蜀文化图像生成质量评估基准集，包含300道专家标注评测题、CLIP Score 评估脚本，可用于模型质量对比与排行榜。",
    size: "1.1 GB", format: "json + png", stars: 634, forks: 98, tag: "Benchmark", tagColor: "#6BAF8E",
    updated: "2026-03-22",
  },
];

function ResourcesTab() {
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);

  const copyCmd = (cmd: string, idx: number) => {
    navigator.clipboard.writeText(cmd);
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 2000);
  };

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 20, marginBottom: 40 }}>
        {RESOURCES.map((res, i) => {
          const Icon = res.icon;
          return (
            <motion.div key={i}
              initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: i * 0.08 }}
              style={{ background: "#111820", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 14, padding: 24, transition: "all 0.2s" }}
              whileHover={{ boxShadow: "0 6px 30px rgba(69,162,158,0.1)", borderColor: "rgba(69,162,158,0.2)" }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: res.tagColor }}>
                  <Icon size={16} />
                  <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, letterSpacing: 2 }}>{res.category}</span>
                </div>
                <span style={{ background: `${res.tagColor}15`, border: `1px solid ${res.tagColor}35`, borderRadius: 4, padding: "2px 8px", fontFamily: "monospace", fontSize: 10, color: res.tagColor }}>{res.tag}</span>
              </div>
              <h3 style={{ fontFamily: "monospace", fontSize: 14, color: "#EFEFEF", marginBottom: 8 }}>{res.title}</h3>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#556372", lineHeight: 1.7, marginBottom: 16 }}>{res.desc}</p>
              <div style={{ display: "flex", gap: 16, marginBottom: 4 }}>
                {[["大小", res.size], ["格式", res.format], ["更新", res.updated]].map(([k, v]) => (
                  <div key={k}>
                    <p style={{ fontFamily: "monospace", fontSize: 9, color: "#3a4550", marginBottom: 1 }}>{k}</p>
                    <p style={{ fontFamily: "monospace", fontSize: 11, color: "#8A9BAD" }}>{v}</p>
                  </div>
                ))}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 16 }}>
                <div style={{ display: "flex", gap: 12 }}>
                  <span style={{ display: "flex", alignItems: "center", gap: 4, fontFamily: "monospace", fontSize: 11, color: "#556372" }}><Star size={11} />{res.stars.toLocaleString()}</span>
                  <span style={{ display: "flex", alignItems: "center", gap: 4, fontFamily: "monospace", fontSize: 11, color: "#556372" }}><GitFork size={11} />{res.forks}</span>
                </div>
                <button
                  onClick={() => copyCmd(`huggingface-cli download sanxingdui/${res.title}`, i)}
                  style={{
                    display: "flex", alignItems: "center", gap: 5,
                    background: "rgba(69,162,158,0.08)", border: "1px solid rgba(69,162,158,0.2)", borderRadius: 6, padding: "6px 12px",
                    color: "#45A29E", fontFamily: "monospace", fontSize: 11, cursor: "pointer",
                  }}>
                  {copiedIdx === i ? <><Check size={11} />已复制</> : <><Download size={11} />下载</>}
                </button>
              </div>
            </motion.div>
          );
        })}
      </div>

      {/* Quick commands */}
      <div style={{ background: "#0D1117", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 14, overflow: "hidden" }}>
        <div style={{ padding: "16px 24px", borderBottom: "1px solid rgba(255,255,255,0.04)", display: "flex", alignItems: "center", gap: 10 }}>
          <Terminal size={14} color="#45A29E" />
          <span style={{ fontFamily: "monospace", fontSize: 13, color: "#C5C6C7" }}>快速开始命令</span>
        </div>
        {[
          { comment: "# 安装依赖", cmd: "pip install sanxingdui-sdk diffusers transformers" },
          { comment: "# 下载 LoRA 权重", cmd: "huggingface-cli download sanxingdui/SanxingduiLoRA-v1.2" },
          { comment: "# 运行快速测试", cmd: "python -c \"import sanxingdui; sanxingdui.generate('青铜大立人，史诗风格')\"" },
        ].map((line, i) => (
          <div key={i} style={{ padding: "12px 24px", borderBottom: i < 2 ? "1px solid rgba(255,255,255,0.03)" : "none", fontFamily: "monospace", fontSize: 13, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>
              <span style={{ color: "#3a4550" }}>{line.comment}<br /></span>
              <span style={{ color: "#45A29E" }}>$ </span>
              <span style={{ color: "#C5C6C7" }}>{line.cmd}</span>
            </span>
            <button onClick={() => copyCmd(line.cmd, i + 10)}
              style={{ background: "none", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 5, padding: "4px 8px", color: "#556372", cursor: "pointer", fontFamily: "monospace", fontSize: 11, flexShrink: 0, marginLeft: 12 }}>
              {copiedIdx === i + 10 ? "✓" : <Copy size={10} />}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── API DOCS TAB ────────────────────────────────────────────────────────────
const API_ENDPOINTS = [
  {
    method: "POST", endpoint: "/v1/generate", color: "#45A29E",
    desc: "根据结构化提示词生成古蜀复原图像",
    params: [
      { name: "identity", type: "string", req: true, desc: "人物身份（如「大祭司」）" },
      { name: "scene", type: "string", req: true, desc: "场景地点（如「青铜神树祭坛」）" },
      { name: "item", type: "string", req: false, desc: "手持器物（如「金杖」）" },
      { name: "style", type: "string", req: false, desc: "艺术风格（默认「史诗油画风」）" },
      { name: "width", type: "integer", req: false, desc: "图像宽度（默认 1024）" },
      { name: "height", type: "integer", req: false, desc: "图像高度（默认 1024）" },
    ],
    example: `{
  "identity": "大祭司",
  "scene": "青铜神树祭坛",
  "item": "金杖",
  "style": "史诗油画风"
}`,
    response: `{
  "id": "gen_abc123",
  "status": "completed",
  "image_url": "https://cdn.sanxingdui.ai/...",
  "width": 1024,
  "height": 1024,
  "prompt": "...",
  "created_at": "2026-05-04T10:30:00Z"
}`,
  },
  {
    method: "POST", endpoint: "/v1/rag/query", color: "#D4AF37",
    desc: "查询三星堆 RAG 知识库，返回考古文献支撑的答案",
    params: [
      { name: "question", type: "string", req: true, desc: "自然语言问题" },
      { name: "max_refs", type: "integer", req: false, desc: "最多返回的参考文献数（默认 3）" },
      { name: "lang", type: "string", req: false, desc: "回答语言（zh / en，默认 zh）" },
    ],
    example: `{
  "question": "金杖上的图案有什么含义？",
  "max_refs": 3,
  "lang": "zh"
}`,
    response: `{
  "answer": "金杖上刻有鱼、鸟、箭头和人头组成的图语...",
  "references": [
    {
      "title": "三星堆：古蜀文明的瑰宝",
      "author": "四川省文物考古研究院",
      "year": "2021"
    }
  ],
  "confidence": 0.94
}`,
  },
  {
    method: "GET", endpoint: "/v1/artifacts", color: "#6BAF8E",
    desc: "获取所有已收录文物的数字档案元数据列表",
    params: [
      { name: "category", type: "string", req: false, desc: "筛选类别（bronze / gold / jade / ivory）" },
      { name: "page", type: "integer", req: false, desc: "页码（默认 1）" },
      { name: "limit", type: "integer", req: false, desc: "每页数量（默认 20，最大 100）" },
    ],
    example: `GET /v1/artifacts?category=gold&limit=10`,
    response: `{
  "total": 42,
  "page": 1,
  "items": [
    {
      "id": "artifact_001",
      "name": "黄金面具",
      "category": "gold",
      "year": "约公元前1300年",
      "pit": "五号坑"
    }
  ]
}`,
  },
];

function APITab() {
  const [activeEndpoint, setActiveEndpoint] = useState(0);
  const [copied, setCopied] = useState<string | null>(null);
  const ep = API_ENDPOINTS[activeEndpoint];

  const copy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 2000);
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "280px 1fr", gap: 24 }} className="max-md:block max-md:space-y-4">
      {/* Sidebar */}
      <div>
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#45A29E", letterSpacing: 3, marginBottom: 12 }}>ENDPOINTS</p>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {API_ENDPOINTS.map((e, i) => (
            <button key={i} onClick={() => setActiveEndpoint(i)}
              style={{
                display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
                background: activeEndpoint === i ? "rgba(69,162,158,0.08)" : "none",
                border: `1px solid ${activeEndpoint === i ? "rgba(69,162,158,0.25)" : "transparent"}`,
                borderRadius: 8, cursor: "pointer", textAlign: "left",
              }}>
              <span style={{ background: `${e.color}15`, border: `1px solid ${e.color}40`, borderRadius: 4, padding: "1px 6px", fontFamily: "monospace", fontSize: 10, color: e.color, flexShrink: 0 }}>{e.method}</span>
              <span style={{ fontFamily: "monospace", fontSize: 12, color: activeEndpoint === i ? "#C5C6C7" : "#556372", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.endpoint}</span>
            </button>
          ))}
        </div>

        <div style={{ marginTop: 24, padding: "14px 16px", background: "rgba(17,24,32,0.8)", border: "1px solid rgba(255,255,255,0.05)", borderRadius: 10 }}>
          <p style={{ fontFamily: "monospace", fontSize: 11, color: "#3a4550", marginBottom: 6 }}>Base URL</p>
          <p style={{ fontFamily: "monospace", fontSize: 12, color: "#45A29E" }}>https://api.sanxingdui.ai</p>
          <p style={{ fontFamily: "monospace", fontSize: 11, color: "#3a4550", marginTop: 10, marginBottom: 6 }}>Auth</p>
          <p style={{ fontFamily: "monospace", fontSize: 11, color: "#8A9BAD" }}>Bearer Token</p>
        </div>
      </div>

      {/* Main */}
      <div>
        {/* Endpoint header */}
        <div style={{ background: "#111820", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 14, padding: "20px 24px", marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
            <span style={{ background: `${ep.color}18`, border: `1px solid ${ep.color}50`, borderRadius: 6, padding: "4px 12px", fontFamily: "monospace", fontSize: 13, color: ep.color }}>{ep.method}</span>
            <code style={{ fontFamily: "monospace", fontSize: 14, color: "#C5C6C7" }}>https://api.sanxingdui.ai{ep.endpoint}</code>
          </div>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: "#8A9BAD" }}>{ep.desc}</p>
        </div>

        {/* Parameters */}
        <div style={{ background: "#111820", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 14, overflow: "hidden", marginBottom: 16 }}>
          <div style={{ padding: "14px 20px", borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
            <p style={{ fontFamily: "monospace", fontSize: 12, color: "#45A29E" }}>Parameters</p>
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                {["参数名", "类型", "必填", "说明"].map((h) => (
                  <th key={h} style={{ padding: "10px 20px", textAlign: "left", fontFamily: "monospace", fontSize: 11, color: "#3a4550", fontWeight: "normal" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ep.params.map((p, i) => (
                <tr key={i} style={{ borderBottom: i < ep.params.length - 1 ? "1px solid rgba(255,255,255,0.03)" : "none" }}>
                  <td style={{ padding: "10px 20px" }}><code style={{ fontFamily: "monospace", fontSize: 12, color: "#45A29E" }}>{p.name}</code></td>
                  <td style={{ padding: "10px 20px" }}><code style={{ fontFamily: "monospace", fontSize: 11, color: "#8A9BAD" }}>{p.type}</code></td>
                  <td style={{ padding: "10px 20px" }}><span style={{ color: p.req ? "#D44545" : "#3a4550", fontFamily: "monospace", fontSize: 11 }}>{p.req ? "是" : "否"}</span></td>
                  <td style={{ padding: "10px 20px", fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#556372" }}>{p.desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Code examples */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          {[["Request", ep.example, "req"], ["Response", ep.response, "res"]].map(([label, code, key]) => (
            <div key={key} style={{ background: "#0D1117", border: "1px solid rgba(255,255,255,0.05)", borderRadius: 10, overflow: "hidden" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 16px", borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                <span style={{ fontFamily: "monospace", fontSize: 11, color: "#556372" }}>{label}</span>
                <button onClick={() => copy(code as string, key as string)}
                  style={{ background: "none", border: "none", cursor: "pointer", color: copied === key ? "#45A29E" : "#3a4550", display: "flex", alignItems: "center", gap: 4, fontFamily: "monospace", fontSize: 10 }}>
                  {copied === key ? <><Check size={10} />已复制</> : <><Copy size={10} />复制</>}
                </button>
              </div>
              <pre style={{ padding: "14px 16px", margin: 0, fontFamily: "monospace", fontSize: 11, color: "#8A9BAD", lineHeight: 1.6, overflowX: "auto" }}>{code}</pre>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── MODELS HUB TAB ──────────────────────────────────────────────────────────
const COMMUNITY_MODELS = [
  { name: "ShuStyle-MKv2", author: "AncientShu_Dev", desc: "专注古蜀青铜器纹样的 LoRA，对纵目面具效果极佳", stars: 342, downloads: "1.2k", tag: "青铜器", image: "https://images.unsplash.com/photo-1763116987110-0e1bbe16a49e?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=300" },
  { name: "GoldMask-LoRA", author: "Artifacts_Lab", desc: "黄金面具专项微调，适合复原黄金器物及人物金面造型", stars: 289, downloads: "876", tag: "黄金器", image: "https://images.unsplash.com/photo-1775729841536-8335a857c94d?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=300" },
  { name: "SacredTree-v1", author: "heritage_ai", desc: "神树与神话场景生成专项权重，附带神话文本引导脚本", stars: 217, downloads: "654", tag: "神话场景", image: "https://images.unsplash.com/photo-1761472651471-839cb6a57177?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=300" },
  { name: "AncientRitual", author: "CulturalAI_CN", desc: "古蜀祭祀场景生成模型，包含燎祭、贡品等仪式元素", stars: 198, downloads: "521", tag: "仪式场景", image: "https://images.unsplash.com/photo-1743952198529-e68b8a9ea972?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=300" },
  { name: "Shu-Portrait-v3", author: "portrait_lab", desc: "古蜀人物面部还原专项，基于大量考古图像微调，写实风格最佳", stars: 156, downloads: "423", tag: "人物", image: "https://images.unsplash.com/photo-1695902046953-1bf8caee5ac3?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=300" },
  { name: "JadeYu-Style", author: "jade_culture", desc: "玉器质感与冷光效果专项 LoRA，对玉璋、玉璧等器物效果出色", stars: 134, downloads: "387", tag: "玉器", image: "https://images.unsplash.com/photo-1634036891026-1a3a7571f82d?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=300" },
];

function ModelsTab() {
  const [filter, setFilter] = useState("全部");
  const cats = ["全部", "青铜器", "黄金器", "神话场景", "仪式场景", "人物", "玉器"];
  const filtered = COMMUNITY_MODELS.filter((m) => filter === "全部" || m.tag === filter);

  return (
    <div>
      <div style={{ display: "flex", gap: 8, marginBottom: 28, flexWrap: "wrap" }}>
        {cats.map((c) => (
          <button key={c} onClick={() => setFilter(c)}
            style={{
              padding: "6px 14px", borderRadius: 6,
              border: `1px solid ${filter === c ? "#45A29E" : "rgba(255,255,255,0.1)"}`,
              background: filter === c ? "rgba(69,162,158,0.1)" : "transparent",
              color: filter === c ? "#45A29E" : "#8A9BAD",
              fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, cursor: "pointer", transition: "all 0.2s",
            }}
          >{c}</button>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 18 }}>
        {filtered.map((model, i) => (
          <motion.div key={model.name}
            initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.06 }}
            style={{ background: "#111820", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 12, overflow: "hidden", transition: "all 0.2s" }}
            whileHover={{ borderColor: "rgba(69,162,158,0.2)", boxShadow: "0 4px 20px rgba(0,0,0,0.4)" }}
          >
            <div style={{ height: 120, overflow: "hidden", position: "relative" }}>
              <img src={model.image} alt={model.name} style={{ width: "100%", height: "100%", objectFit: "cover", filter: "brightness(0.7) sepia(0.2)", transition: "transform 0.4s" }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.transform = "scale(1.07)")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.transform = "scale(1)")}
              />
              <span style={{ position: "absolute", top: 8, right: 8, background: "rgba(69,162,158,0.2)", border: "1px solid rgba(69,162,158,0.3)", borderRadius: 4, padding: "2px 7px", fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#45A29E" }}>{model.tag}</span>
            </div>
            <div style={{ padding: "14px 16px" }}>
              <p style={{ fontFamily: "monospace", fontSize: 13, color: "#EFEFEF", marginBottom: 3 }}>{model.name}</p>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", marginBottom: 8 }}>by {model.author}</p>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#556372", lineHeight: 1.6, marginBottom: 12 }}>{model.desc}</p>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", gap: 12 }}>
                  <span style={{ display: "flex", alignItems: "center", gap: 3, fontFamily: "monospace", fontSize: 11, color: "#556372" }}><Star size={10} />{model.stars}</span>
                  <span style={{ display: "flex", alignItems: "center", gap: 3, fontFamily: "monospace", fontSize: 11, color: "#556372" }}><Download size={10} />{model.downloads}</span>
                </div>
                <button style={{
                  background: "rgba(69,162,158,0.08)", border: "1px solid rgba(69,162,158,0.2)", borderRadius: 5,
                  padding: "5px 10px", color: "#45A29E", fontFamily: "monospace", fontSize: 11, cursor: "pointer",
                }}>使用</button>
              </div>
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

// ─── CONTRIBUTE TAB ──────────────────────────────────────────────────────────
const CONTRIBUTE_STEPS = [
  { icon: "🍴", title: "Fork 项目", desc: "前往 GitHub 仓库，Fork 主项目到你的账户。", cmd: "git clone https://github.com/your-account/sanxingdui-ai" },
  { icon: "🌿", title: "创建分支", desc: "为你的改动创建专属功能分支。", cmd: "git checkout -b feat/your-feature-name" },
  { icon: "💻", title: "编写代码", desc: "按照 CONTRIBUTING.md 规范进行开发，并确保通过所有测试。", cmd: "pytest tests/ && npm run lint" },
  { icon: "📤", title: "提交 PR", desc: "将修改 Push 到你的 Fork，并在 GitHub 上发起 Pull Request。", cmd: "git push origin feat/your-feature-name" },
];

const ISSUES = [
  { id: "#142", title: "优化 RAG 知识库的古蜀方言词条覆盖率", label: "enhancement", labelColor: "#45A29E", comments: 8 },
  { id: "#138", title: "添加象牙器类文物的 LoRA 训练数据", label: "help wanted", labelColor: "#D4AF37", comments: 5 },
  { id: "#131", title: "修复风格迁移时的颜色偏移问题", label: "bug", labelColor: "#D44545", comments: 12 },
  { id: "#127", title: "为黄金面具添加高精度 3D 扫描数据集", label: "data", labelColor: "#6BAF8E", comments: 3 },
];

function ContributeTab() {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 32 }} className="max-md:block max-md:space-y-8">
      <div>
        <h3 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 18, fontWeight: 700, color: "#D4AF37", marginBottom: 20 }}>贡献流程</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {CONTRIBUTE_STEPS.map((step, i) => (
            <motion.div key={i}
              initial={{ opacity: 0, x: -20 }} whileInView={{ opacity: 1, x: 0 }} viewport={{ once: true }} transition={{ delay: i * 0.1 }}
              style={{ background: "#111820", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 12, overflow: "hidden" }}
            >
              <div style={{ display: "flex", alignItems: "flex-start", gap: 14, padding: "16px 18px" }}>
                <div style={{ width: 36, height: 36, borderRadius: "50%", background: "rgba(212,175,55,0.1)", border: "1px solid rgba(212,175,55,0.25)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18, flexShrink: 0 }}>
                  {step.icon}
                </div>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <span style={{ fontFamily: "monospace", fontSize: 10, color: "#D4AF37" }}>STEP {i + 1}</span>
                    <h4 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 14, fontWeight: 700, color: "#EFEFEF" }}>{step.title}</h4>
                  </div>
                  <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#8A9BAD", marginBottom: 8 }}>{step.desc}</p>
                  <div style={{ background: "rgba(0,0,0,0.4)", borderRadius: 5, padding: "6px 10px" }}>
                    <code style={{ fontFamily: "monospace", fontSize: 11, color: "#45A29E" }}>{step.cmd}</code>
                  </div>
                </div>
              </div>
            </motion.div>
          ))}
        </div>

        <div style={{ marginTop: 24, display: "flex", gap: 12 }}>
          <a href="https://github.com" target="_blank" rel="noopener noreferrer"
            style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 20px", background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, color: "#C5C6C7", textDecoration: "none", fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13 }}>
            <Github size={15} /> GitHub 仓库 <ExternalLink size={11} color="#45A29E" />
          </a>
          <a href="#" style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 20px", background: "rgba(69,162,158,0.08)", border: "1px solid rgba(69,162,158,0.2)", borderRadius: 8, color: "#45A29E", textDecoration: "none", fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13 }}>
            <FileText size={15} /> 贡献指南
          </a>
        </div>
      </div>

      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
          <h3 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 18, fontWeight: 700, color: "#D4AF37" }}>Good First Issues</h3>
          <span style={{ fontFamily: "monospace", fontSize: 11, color: "#3a4550" }}>4 open</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 28 }}>
          {ISSUES.map((issue) => (
            <div key={issue.id} style={{ background: "#111820", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 10, padding: "14px 18px", cursor: "pointer", transition: "border-color 0.2s" }}
              onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.borderColor = "rgba(69,162,158,0.2)")}
              onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.borderColor = "rgba(255,255,255,0.06)")}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
                <span style={{ fontFamily: "monospace", fontSize: 11, color: "#3a4550" }}>{issue.id}</span>
                <div style={{ display: "flex", alignItems: "center", gap: 4, color: "#3a4550", fontFamily: "monospace", fontSize: 11 }}>
                  <MessageSquare size={10} />{issue.comments}
                </div>
              </div>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: "#C5C6C7", marginBottom: 8 }}>{issue.title}</p>
              <span style={{ background: `${issue.labelColor}15`, border: `1px solid ${issue.labelColor}35`, borderRadius: 10, padding: "2px 8px", fontFamily: "monospace", fontSize: 10, color: issue.labelColor }}>{issue.label}</span>
            </div>
          ))}
        </div>

        {/* Community stats */}
        <div style={{ background: "#111820", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 12, padding: 20 }}>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 2, marginBottom: 16 }}>社区数据</p>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {[["⭐ Stars", "5,134"], ["🍴 Forks", "892"], ["👥 贡献者", "47"], ["💬 Issues", "23 open"]].map(([k, v]) => (
              <div key={k} style={{ textAlign: "center", padding: "12px", background: "rgba(255,255,255,0.02)", borderRadius: 8 }}>
                <p style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 18, fontWeight: 700, color: "#D4AF37", marginBottom: 2 }}>{v}</p>
                <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#3a4550" }}>{k}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── MAIN PAGE ───────────────────────────────────────────────────────────────
export function DeveloperPage() {
  const { tab } = useParams<{ tab: string }>();
  const navigate = useNavigate();
  const activeTab = tab || "resources";

  return (
    <div style={{ paddingTop: 64, minHeight: "100vh" }}>
      <div style={{ background: "linear-gradient(180deg, rgba(31,40,51,0.5) 0%, transparent 100%)", borderBottom: "1px solid rgba(69,162,158,0.08)", padding: "40px 0 0" }}>
        <div className="max-w-7xl mx-auto px-6">
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 5, marginBottom: 8 }}>OPEN SOURCE HUB / 开发者社区</p>
            <h1 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: "clamp(24px,4vw,44px)", fontWeight: 900, color: "#EFEFEF", marginBottom: 4 }}>
              共建<span style={{ color: "#45A29E" }}>开源生态</span>
            </h1>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 14, color: "#8A9BAD", marginTop: 8 }}>
              开放模型、数据集与 API，与全球开发者共同传承古蜀文明
            </p>
          </motion.div>
          <div style={{ display: "flex", gap: 0, marginTop: 32, borderBottom: "1px solid rgba(255,255,255,0.06)", overflowX: "auto" }}>
            {TABS.map((t) => (
              <button key={t.id} onClick={() => navigate(`/developer/${t.id}`)}
                style={{
                  padding: "12px 24px", background: "none", border: "none",
                  borderBottom: activeTab === t.id ? "2px solid #45A29E" : "2px solid transparent",
                  color: activeTab === t.id ? "#45A29E" : "#8A9BAD",
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
            {activeTab === "resources" && <ResourcesTab />}
            {activeTab === "api" && <APITab />}
            {activeTab === "models" && <ModelsTab />}
            {activeTab === "contribute" && <ContributeTab />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}
