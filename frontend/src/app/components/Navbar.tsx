import { useState, useEffect, useRef } from "react";
import { useNavigate, useLocation } from "react-router";
import { Menu, X, ChevronDown } from "lucide-react";

const NAV_ITEMS = [
  {
    label: "首页",
    path: "/",
    children: [],
  },
  {
    label: "数字展厅",
    path: "/museum",
    icon: "🏛",
    children: [
      { label: "精品文物展", path: "/museum/gallery", desc: "核心文物高清展览" },
      { label: "3D 数字模型", path: "/museum/3d", desc: "交互式三维文物观览" },
      { label: "考古现场", path: "/museum/excavation", desc: "发掘现场实录与复原" },
      { label: "文明时间轴", path: "/museum/timeline", desc: "三星堆历史脉络梳理" },
    ],
  },
  {
    label: "AI 复原引擎",
    path: "/ai",
    icon: "⚙",
    children: [
      { label: "场景复原", path: "/ai/scene", desc: "构建古蜀祭祀场景" },
      { label: "人物还原", path: "/ai/figure", desc: "古蜀人物形象生成" },
      { label: "文物修复", path: "/ai/artifact", desc: "残损文物智能复原" },
      { label: "风格迁移", path: "/ai/style", desc: "古蜀艺术风格转换" },
    ],
  },
  {
    label: "文化沁润",
    path: "/culture",
    icon: "📜",
    children: [
      { label: "文化精粹", path: "/culture/essence", desc: "古蜀核心文化概念" },
      { label: "知识图谱", path: "/culture/knowledge", desc: "考古知识库检索" },
      { label: "互动问答", path: "/culture/quiz", desc: "三星堆知识竞答" },
      { label: "神话故事", path: "/culture/myths", desc: "古蜀神话与传说" },
    ],
  },
  {
    label: "开发者社区",
    path: "/developer",
    icon: "💻",
    children: [
      { label: "开源资源", path: "/developer/resources", desc: "模型权重与数据集" },
      { label: "API 文档", path: "/developer/api", desc: "接口规范与调用示例" },
      { label: "模型广场", path: "/developer/models", desc: "社区 LoRA 模型合集" },
      { label: "参与贡献", path: "/developer/contribute", desc: "共建项目指引" },
    ],
  },
];

export function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [activeDropdown, setActiveDropdown] = useState<string | null>(null);
  const [mobileExpanded, setMobileExpanded] = useState<string | null>(null);
  const dropdownTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const navigate = useNavigate();
  const { pathname } = useLocation();

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const handleMouseEnter = (label: string) => {
    if (dropdownTimeout.current) clearTimeout(dropdownTimeout.current);
    setActiveDropdown(label);
  };

  const handleMouseLeave = () => {
    dropdownTimeout.current = setTimeout(() => setActiveDropdown(null), 150);
  };

  const isActive = (path: string) =>
    path === "/" ? pathname === "/" : pathname.startsWith(path);

  return (
    <nav
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        zIndex: 100,
        transition: "background 0.4s, box-shadow 0.4s",
        background: scrolled ? "rgba(11,12,16,0.96)" : "rgba(11,12,16,0.7)",
        boxShadow: scrolled ? "0 2px 30px rgba(0,0,0,0.7)" : "none",
        backdropFilter: "blur(16px)",
        borderBottom: "1px solid rgba(212,175,55,0.1)",
      }}
    >
      <div className="max-w-7xl mx-auto px-6 flex items-center justify-between" style={{ height: 64 }}>
        {/* Logo */}
        <button
          onClick={() => navigate("/")}
          style={{
            display: "flex", alignItems: "center", gap: 10,
            background: "none", border: "none", cursor: "pointer", padding: 0,
          }}
        >
          <div style={{
            width: 32, height: 32, borderRadius: "50%",
            background: "linear-gradient(135deg, rgba(212,175,55,0.3), rgba(69,162,158,0.2))",
            border: "1px solid rgba(212,175,55,0.4)",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 14,
          }}>✦</div>
          <div>
            <span style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 18, fontWeight: 900, color: "#D4AF37", letterSpacing: 2 }}>三星堆</span>
            <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, fontWeight: 300, color: "#45A29E", letterSpacing: 3, paddingLeft: 6 }}>古蜀神韵</span>
          </div>
        </button>

        {/* Desktop nav */}
        <div className="hidden md:flex items-center gap-1">
          {NAV_ITEMS.map((item) => (
            <div
              key={item.label}
              style={{ position: "relative" }}
              onMouseEnter={() => item.children.length > 0 && handleMouseEnter(item.label)}
              onMouseLeave={handleMouseLeave}
            >
              <button
                onClick={() => {
                  if (item.children.length === 0) navigate(item.path);
                  else navigate(item.children[0].path);
                }}
                style={{
                  display: "flex", alignItems: "center", gap: 4,
                  background: isActive(item.path) && item.path !== "/" || (item.path === "/" && pathname === "/")
                    ? "rgba(69,162,158,0.1)" : "none",
                  border: "none",
                  borderRadius: 6,
                  padding: "7px 12px",
                  cursor: "pointer",
                  fontFamily: "'Noto Sans SC', sans-serif",
                  fontSize: 14, fontWeight: 400,
                  color: isActive(item.path) ? "#45A29E" : "#C5C6C7",
                  transition: "color 0.2s, background 0.2s",
                  letterSpacing: 0.5,
                }}
                onMouseEnter={(e) => { if (!isActive(item.path)) (e.currentTarget as HTMLElement).style.color = "#45A29E"; }}
                onMouseLeave={(e) => { if (!isActive(item.path)) (e.currentTarget as HTMLElement).style.color = "#C5C6C7"; }}
              >
                {item.label}
                {item.children.length > 0 && (
                  <ChevronDown
                    size={12}
                    style={{
                      transition: "transform 0.2s",
                      transform: activeDropdown === item.label ? "rotate(180deg)" : "none",
                      opacity: 0.6,
                    }}
                  />
                )}
              </button>

              {/* Dropdown */}
              {item.children.length > 0 && activeDropdown === item.label && (
                <div
                  style={{
                    position: "absolute",
                    top: "calc(100% + 8px)",
                    left: "50%",
                    transform: "translateX(-50%)",
                    background: "rgba(11,12,16,0.98)",
                    border: "1px solid rgba(212,175,55,0.15)",
                    borderRadius: 12,
                    padding: "8px",
                    minWidth: 220,
                    boxShadow: "0 20px 60px rgba(0,0,0,0.8), 0 0 0 1px rgba(255,255,255,0.02)",
                    backdropFilter: "blur(20px)",
                  }}
                  onMouseEnter={() => { if (dropdownTimeout.current) clearTimeout(dropdownTimeout.current); setActiveDropdown(item.label); }}
                  onMouseLeave={handleMouseLeave}
                >
                  {/* Dropdown header */}
                  <div style={{ padding: "6px 12px 10px", borderBottom: "1px solid rgba(255,255,255,0.06)", marginBottom: 6 }}>
                    <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#45A29E", letterSpacing: 2 }}>
                      {item.icon} {item.label}
                    </span>
                  </div>
                  {item.children.map((child) => (
                    <button
                      key={child.path}
                      onClick={() => { navigate(child.path); setActiveDropdown(null); }}
                      style={{
                        display: "block",
                        width: "100%",
                        textAlign: "left",
                        background: pathname === child.path ? "rgba(69,162,158,0.1)" : "none",
                        border: "none",
                        borderRadius: 8,
                        padding: "9px 12px",
                        cursor: "pointer",
                        transition: "background 0.15s",
                      }}
                      onMouseEnter={(e) => { if (pathname !== child.path) (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.04)"; }}
                      onMouseLeave={(e) => { if (pathname !== child.path) (e.currentTarget as HTMLElement).style.background = "none"; }}
                    >
                      <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: pathname === child.path ? "#45A29E" : "#C5C6C7", marginBottom: 1 }}>
                        {child.label}
                      </p>
                      <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#3a4550" }}>
                        {child.desc}
                      </p>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}

          <button
            onClick={() => navigate("/ai/scene")}
            style={{
              marginLeft: 8,
              background: "linear-gradient(135deg, #45A29E, #2c7a77)",
              border: "1px solid rgba(69,162,158,0.4)",
              borderRadius: 6, padding: "7px 18px",
              color: "#fff", fontFamily: "'Noto Sans SC', sans-serif",
              fontSize: 13, cursor: "pointer", letterSpacing: 1,
              transition: "opacity 0.2s",
            }}
            onMouseEnter={(e) => ((e.target as HTMLElement).style.opacity = "0.85")}
            onMouseLeave={(e) => ((e.target as HTMLElement).style.opacity = "1")}
          >
            立即体验
          </button>
        </div>

        {/* Mobile toggle */}
        <button
          className="md:hidden"
          onClick={() => setMobileOpen(!mobileOpen)}
          style={{ background: "none", border: "none", cursor: "pointer", color: "#C5C6C7" }}
        >
          {mobileOpen ? <X size={22} /> : <Menu size={22} />}
        </button>
      </div>

      {/* Mobile menu */}
      {mobileOpen && (
        <div style={{ background: "rgba(11,12,16,0.99)", borderTop: "1px solid rgba(212,175,55,0.12)", maxHeight: "80vh", overflowY: "auto" }}>
          {NAV_ITEMS.map((item) => (
            <div key={item.label} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
              <button
                onClick={() => {
                  if (item.children.length === 0) { navigate(item.path); setMobileOpen(false); }
                  else setMobileExpanded(mobileExpanded === item.label ? null : item.label);
                }}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  width: "100%", padding: "14px 24px",
                  background: "none", border: "none", cursor: "pointer",
                  fontFamily: "'Noto Sans SC', sans-serif", fontSize: 15, color: "#C5C6C7",
                }}
              >
                <span>{item.icon ? `${item.icon} ` : ""}{item.label}</span>
                {item.children.length > 0 && (
                  <ChevronDown size={14} style={{ transform: mobileExpanded === item.label ? "rotate(180deg)" : "none", transition: "0.2s" }} />
                )}
              </button>
              {mobileExpanded === item.label && item.children.map((child) => (
                <button
                  key={child.path}
                  onClick={() => { navigate(child.path); setMobileOpen(false); setMobileExpanded(null); }}
                  style={{
                    display: "block", width: "100%", textAlign: "left",
                    padding: "10px 24px 10px 40px",
                    background: pathname === child.path ? "rgba(69,162,158,0.08)" : "none",
                    border: "none", cursor: "pointer",
                    fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13,
                    color: pathname === child.path ? "#45A29E" : "#8A9BAD",
                  }}
                >
                  {child.label}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </nav>
  );
}
