import { useParams, useNavigate } from "react-router";
import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Search, ChevronDown, CheckCircle, XCircle, BookOpen } from "lucide-react";
import { useAssistant } from "../../context/AssistantContext";

// ─── TABS ────────────────────────────────────────────────────────────────────
const TABS = [
  { id: "essence", label: "文化精粹" },
  { id: "knowledge", label: "知识图谱" },
  { id: "quiz", label: "互动问答" },
  { id: "myths", label: "神话故事" },
];

// ─── CULTURAL ESSENCE ────────────────────────────────────────────────────────
const CULTURE_CONCEPTS = [
  {
    icon: "☀️", title: "太阳崇拜", color: "#D4AF37",
    summary: "古蜀人对太阳的崇拜渗透到青铜器的每一个细节。",
    detail: "青铜神树上的神鸟、金杖上的鸟图案，以及大量出土的太阳轮形器，都指向古蜀文明对太阳的高度崇拜。学界认为古蜀可能存在「太阳神祭司」制度，每年举行盛大的祭日仪式。",
    evidence: ["铜神树顶端神鸟", "金杖鸟图案", "青铜太阳轮", "花蕾形器座"],
  },
  {
    icon: "🌳", title: "神树信仰", color: "#45A29E",
    summary: "神树是连接天地人三界的宇宙轴心。",
    detail: "三星堆出土的青铜神树对应《山海经》「建木」神话：「有木，其状如牛，引之有皮，若缨、黄蛇。其叶如罗，其实如栾，其木若蓲，其名曰建木」。神树是天帝与群神上下往来的通道。",
    evidence: ["三件青铜神树", "《山海经》建木记载", "树枝神鸟", "树干龙纹"],
  },
  {
    icon: "🐉", title: "龙蛇崇拜", color: "#8B7355",
    summary: "龙与蛇是古蜀神圣力量的象征符号。",
    detail: "青铜神树上的神龙盘绕而下，象征从天界降临的神力。三星堆还出土了多件铜蛇形器，面部特征夸张，与华夏文明的龙崇拜既有联系又独具特色，体现了古蜀文化的独立性。",
    evidence: ["神树盘绕龙", "铜蛇形器", "龙形纹饰", "人首蛇身像"],
  },
  {
    icon: "✨", title: "黄金权力", color: "#D4AF37",
    summary: "黄金是神权与王权的最高象征。",
    detail: "三星堆出土的金器含金量极高（金杖达94%），代表了古蜀国君或最高祭司的无上权威。与中原地区以玉器彰显权威的文化不同，古蜀以黄金作为神权的核心载体，独树一帜。",
    evidence: ["金杖（含金量94%）", "黄金面具（85%）", "各类金箔覆面", "金花片饰"],
  },
  {
    icon: "🏺", title: "青铜技艺", color: "#7C8D6E",
    summary: "分铸法铸造技术领先同时代文明。",
    detail: "三星堆青铜器采用高度复杂的「分铸法」——先分别铸造各组件，再拼合焊接。青铜神树共由树干、树枝、神鸟等数十件零件组成，每件精度惊人，展现了3000年前工匠超卓的金属加工技术。",
    evidence: ["青铜神树分铸结构", "大立人铸接工艺", "纵目面具铸模", "铜头像铸造痕迹"],
  },
  {
    icon: "🐘", title: "象牙仪式", color: "#A89060",
    summary: "燎祭象牙是古蜀最神圣的仪式之一。",
    detail: "三星堆各祭祀坑共出土13000余根象牙，全部被刻意折断并火烧。这种「毁器入坑」的仪式在世界文明史上极为罕见，被学界认为是一种「奉献给神灵」的终极祭祀行为。",
    evidence: ["各坑均有象牙", "折断火烧痕迹", "出土时层叠摆放", "部分象牙刻有纹饰"],
  },
];

function EssenceTab() {
  const [expanded, setExpanded] = useState<number | null>(null);

  return (
    <div>
      <div style={{ textAlign: "center", marginBottom: 48 }}>
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 14, color: "#8A9BAD", lineHeight: 1.8, maxWidth: 600, margin: "0 auto" }}>
          三星堆文明虽无文字留存，却通过青铜器、黄金器与玉器，构建了一套完整的精神宇宙。以下六大文化核心，是理解古蜀文明的钥匙。
        </p>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 20 }}>
        {CULTURE_CONCEPTS.map((c, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: i * 0.07 }}
            style={{
              background: "#111820",
              border: `1px solid ${expanded === i ? c.color + "40" : "rgba(255,255,255,0.06)"}`,
              borderRadius: 14, padding: "24px",
              cursor: "pointer", transition: "border-color 0.2s, box-shadow 0.2s",
              boxShadow: expanded === i ? `0 0 30px ${c.color}15` : "none",
            }}
            onClick={() => setExpanded(expanded === i ? null : i)}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 12 }}>
              <div style={{
                width: 48, height: 48, borderRadius: "50%",
                background: `${c.color}15`, border: `1px solid ${c.color}40`,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 22, flexShrink: 0,
              }}>
                {c.icon}
              </div>
              <div>
                <h3 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 17, fontWeight: 700, color: c.color, marginBottom: 2 }}>
                  {c.title}
                </h3>
                <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#8A9BAD" }}>
                  {c.summary}
                </p>
              </div>
              <ChevronDown size={16} color="#556372" style={{ marginLeft: "auto", transform: expanded === i ? "rotate(180deg)" : "none", transition: "transform 0.2s", flexShrink: 0 }} />
            </div>

            <AnimatePresence>
              {expanded === i && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  style={{ overflow: "hidden" }}
                >
                  <div style={{ paddingTop: 12, borderTop: `1px solid ${c.color}20` }}>
                    <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: "#8A9BAD", lineHeight: 1.8, marginBottom: 14 }}>
                      {c.detail}
                    </p>
                    <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: c.color, marginBottom: 8, letterSpacing: 1 }}>
                      实物证据
                    </p>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {c.evidence.map((ev, j) => (
                        <span key={j} style={{
                          background: `${c.color}10`, border: `1px solid ${c.color}30`,
                          borderRadius: 4, padding: "2px 8px",
                          fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: c.color,
                        }}>
                          {ev}
                        </span>
                      ))}
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

// ─── KNOWLEDGE BASE ──────────────────────────────────────────────────────────
const KB_CATEGORIES = [
  {
    id: "artifacts", label: "文物解读", color: "#D4AF37",
    items: [
      {
        q: "青铜大立人双手握持的是什么？",
        a: "这是三星堆最著名的未解之谜之一。目前学界有三种主流观点：①握持象牙，因附近出土了大量象牙；②握持权杖或神旗；③握持玉璋等礼器。大立人双手形成的环形握状，尺寸与象牙直径高度匹配。",
        sources: ["四川省文物考古研究院发掘报告（2022）", "《考古》2017年第8期"],
      },
      {
        q: "黄金面具的含金量为何如此之高？",
        a: "黄金面具含金量约85%，金杖更高达94%。古蜀人获取黄金的主要途径是在岷江流域淘取沙金。古代成都平原多沙金矿藏，加之古蜀人可能控制了主要的淘金渠道，从而积累了大量黄金。高纯度黄金代表神圣与权威，被用于最高级别的祭祀礼器。",
        sources: ["《三星堆黄金器物科技分析》（2021）"],
      },
      {
        q: "青铜神树为什么分为三层九枝？",
        a: "「三层九枝」是古代「九」这一神圣数字的体现——九在中国古代文化中代表极致与圆满。九根树枝各托一鸟，对应古代「十日神话」中的九只太阳神鸟（另一只正在天空运行）。这一设计将三星堆的太阳崇拜与宇宙秩序观融为一体。",
        sources: ["《三星堆青铜神树宇宙观研究》（2019）"],
      },
      {
        q: "纵目面具的眼睛为何向外突出16厘米？",
        a: "纵目造型远超人类生理极限，是神灵超自然能力的具象化表达。对应文献：《华阳国志·蜀志》记载「有蜀侯蚕丛，其目纵」，即第一代蜀王蚕丛拥有竖眼/纵目。考古学界认为，这种夸张的造型是将蚕丛的神话特征转化为可视化的青铜艺术。",
        sources: ["《华阳国志》东晋·常璩著", "《纵目面具研究》（2018）"],
      },
    ],
  },
  {
    id: "history", label: "历史文化", color: "#45A29E",
    items: [
      {
        q: "三星堆文明为什么没有留下文字？",
        a: "这是世界考古史上最大的谜团之一。可能的解释包括：①古蜀文字刻于竹木等易腐材料上而未能保存；②古蜀社会由祭司主导，知识以口传方式传承，无需文字记录；③金杖图语系统可能是一种图形文字前身，但尚未能被破译。目前已发现部分刻划符号，仍在研究中。",
        sources: ["《古蜀文字问题研究综述》（2020）"],
      },
      {
        q: "三星堆文明与中原商文明是什么关系？",
        a: "两者是同期并存、有交流但各自独立发展的区域文明。相似之处：都使用青铜铸造技术、都有祭祀坑埋藏制度、部分礼器形制相同（如玉璋）。差异之处：三星堆青铜器造型远比商代更加神秘夸张，没有文字，黄金器比例更高。学界认为双方可能存在商品贸易与文化交流。",
        sources: ["《商文化与三星堆文化比较研究》（2019）"],
      },
      {
        q: "三星堆文明为什么突然消失？",
        a: "目前有多种假说：①地震洪水说——考古证据显示遗址周围曾发生大规模地质变动；②战争说——仪式性毁器入坑可能与战败撤退有关；③宗教改革说——古蜀宗教体系发生重大变革，旧神器被集中销毁；④王朝更迭说——文明中心从三星堆转移至金沙（今成都）。目前尚无定论。",
        sources: ["《三星堆文明衰落原因探讨》（2021）"],
      },
    ],
  },
  {
    id: "tech", label: "考古科技", color: "#6BAF8E",
    items: [
      {
        q: "三星堆是如何被发现的？",
        a: "1929年，四川广汉农民燕道诚在耕地时偶然发现一批玉石器，引发关注。1934年，华西大学博物馆启动首次正式调查。1986年，两座祭祀坑被发现，出土文物震惊世界。2019年起，又发现六座新祭祀坑，采用多学科联合发掘方式，使用碳14测年、微CT扫描、DNA分析等现代技术。",
        sources: ["四川省文物考古研究院官方发掘纪录"],
      },
      {
        q: "如何用AI技术辅助三星堆研究？",
        a: "目前AI在三星堆研究中的应用包括：①图像生成——复原古蜀祭祀场景和人物形象；②知识图谱——将散落文献与文物信息结构化关联；③三维重建——基于照片生成文物3D模型；④图语分析——使用机器学习识别金杖、玉璋上的刻划符号；⑤土壤分析——AI辅助判读地层分布，预测潜在发掘区域。",
        sources: ["本项目技术白皮书（2026）"],
      },
    ],
  },
  {
    id: "mystery", label: "历史谜题", color: "#A89060",
    items: [
      {
        q: "三星堆人究竟是哪个族群的祖先？",
        a: "这是尚无定论的历史悬案。主要观点：①本土起源说——是成都平原土著居民的后裔；②巴蜀先民说——是后来巴蜀文化的直接前身；③外来移民说——部分研究者认为三星堆人与东南亚或更远地区存在关联（基于青铜器风格比较）。2021年新发掘的人骨DNA研究正在进行，或将提供答案。",
        sources: ["《三星堆人群来源研究进展》（2022）"],
      },
      {
        q: "三星堆的象牙从哪里来？",
        a: "13000余根象牙是三星堆最大的谜团之一。古代成都平原的气候较现在温暖湿润，可能确实有野象生存，这是「本地来源说」的依据。但如此大量的象牙令人怀疑是否来自更广泛的交换网络。部分研究者提出，象牙可能通过东南亚-云南的贸易路线输入。碳同位素分析正在进行中。",
        sources: ["《三星堆象牙来源的稳定同位素分析》（2023）"],
      },
    ],
  },
];

function KnowledgeTab() {
  const [search, setSearch] = useState("");
  const [activeCategory, setActiveCategory] = useState("artifacts");
  const [openItem, setOpenItem] = useState<string | null>(null);
  const { setQuery } = useAssistant();

  const activeKB = KB_CATEGORIES.find((c) => c.id === activeCategory)!;
  const filteredItems = search.trim()
    ? KB_CATEGORIES.flatMap((c) => c.items).filter(
        (item) => item.q.includes(search) || item.a.includes(search)
      )
    : activeKB.items;

  return (
    <div>
      {/* Search */}
      <div style={{ position: "relative", maxWidth: 520, marginBottom: 32 }}>
        <Search size={15} style={{ position: "absolute", left: 14, top: "50%", transform: "translateY(-50%)", color: "#45A29E" }} />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索知识库，例如：金杖、蚕丛、象牙…"
          style={{
            width: "100%", paddingLeft: 44, paddingRight: 16, paddingTop: 12, paddingBottom: 12,
            background: "rgba(31,40,51,0.6)", border: "1px solid rgba(69,162,158,0.25)",
            borderRadius: 10, color: "#C5C6C7",
            fontFamily: "'Noto Sans SC', sans-serif", fontSize: 14, outline: "none",
          }}
          onFocus={(e) => ((e.target as HTMLElement).style.borderColor = "rgba(69,162,158,0.5)")}
          onBlur={(e) => ((e.target as HTMLElement).style.borderColor = "rgba(69,162,158,0.25)")}
        />
        {search && (
          <button onClick={() => setSearch("")} style={{ position: "absolute", right: 12, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: "#556372", fontSize: 16 }}>×</button>
        )}
      </div>

      {!search && (
        <div style={{ display: "flex", gap: 10, marginBottom: 28, flexWrap: "wrap" }}>
          {KB_CATEGORIES.map((c) => (
            <button
              key={c.id}
              onClick={() => setActiveCategory(c.id)}
              style={{
                padding: "8px 18px", borderRadius: 8,
                border: `1px solid ${activeCategory === c.id ? c.color : "rgba(255,255,255,0.1)"}`,
                background: activeCategory === c.id ? `${c.color}15` : "transparent",
                color: activeCategory === c.id ? c.color : "#8A9BAD",
                fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, cursor: "pointer",
                transition: "all 0.2s",
              }}
            >
              {c.label}
            </button>
          ))}
        </div>
      )}

      {search && (
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#45A29E", marginBottom: 16 }}>
          找到 {filteredItems.length} 条相关知识
        </p>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {filteredItems.map((item, i) => {
          const key = `${i}-${item.q}`;
          const isOpen = openItem === key;
          return (
            <div
              key={key}
              style={{
                background: "#111820",
                border: `1px solid ${isOpen ? "rgba(69,162,158,0.3)" : "rgba(255,255,255,0.06)"}`,
                borderRadius: 12, overflow: "hidden",
                transition: "border-color 0.2s",
              }}
            >
              <button
                onClick={() => setOpenItem(isOpen ? null : key)}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  width: "100%", padding: "16px 20px", background: "none", border: "none",
                  cursor: "pointer", textAlign: "left",
                }}
              >
                <span style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 15, fontWeight: 700, color: "#EFEFEF", flex: 1 }}>
                  {item.q}
                </span>
                <ChevronDown size={15} color="#45A29E" style={{ transform: isOpen ? "rotate(180deg)" : "none", transition: "transform 0.2s", flexShrink: 0, marginLeft: 12 }} />
              </button>
              <AnimatePresence>
                {isOpen && (
                  <motion.div
                    initial={{ height: 0 }} animate={{ height: "auto" }} exit={{ height: 0 }}
                    style={{ overflow: "hidden" }}
                  >
                    <div style={{ padding: "0 20px 20px", borderTop: "1px solid rgba(255,255,255,0.04)" }}>
                      <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: "#8A9BAD", lineHeight: 1.85, marginTop: 14, marginBottom: 14 }}>
                        {item.a}
                      </p>
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
                        <div>
                          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#45A29E", letterSpacing: 1, marginBottom: 4 }}>
                            <BookOpen size={10} style={{ display: "inline", marginRight: 4 }} />参考来源
                          </p>
                          {item.sources.map((s, j) => (
                            <p key={j} style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#3a4550" }}>[{j + 1}] {s}</p>
                          ))}
                        </div>
                        <button
                          onClick={() => setQuery(item.q)}
                          style={{
                            display: "flex", alignItems: "center", gap: 5,
                            background: "rgba(69,162,158,0.08)", border: "1px solid rgba(69,162,158,0.2)",
                            borderRadius: 6, padding: "6px 12px",
                            color: "#45A29E", fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, cursor: "pointer",
                          }}
                        >
                          向智脑深入问答
                        </button>
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── INTERACTIVE QUIZ ────────────────────────────────────────────────────────
const QUIZ_QUESTIONS = [
  { q: "三星堆遗址位于哪个省份？", opts: ["四川省", "云南省", "贵州省", "陕西省"], ans: 0 },
  { q: "青铜神树的高度约为多少厘米？", opts: ["196 cm", "286 cm", "396 cm", "496 cm"], ans: 2 },
  { q: "黄金面具的含金量约为多少？", opts: ["约65%", "约75%", "约85%", "约95%"], ans: 2 },
  { q: "三星堆文化存在的大致年代是？", opts: ["公元前3000–2500年", "公元前1700–1150年", "公元前800–300年", "公元前2000–1500年"], ans: 1 },
  { q: "青铜大立人的总高度（含底座）约为多少？", opts: ["162 cm", "262 cm", "362 cm", "462 cm"], ans: 1 },
  { q: "三星堆最近一次大规模发掘新发现了几座祭祀坑？", opts: ["2座", "4座", "6座", "8座"], ans: 2 },
  { q: "古蜀「鱼凫王」中「凫」是指什么动物？", opts: ["鱼", "野鸭", "鹰", "鹤"], ans: 1 },
  { q: "金杖全长约为多少厘米？", opts: ["92 cm", "142 cm", "192 cm", "242 cm"], ans: 1 },
  { q: "三星堆文明与中原哪个王朝大致同期？", opts: ["夏代", "商代", "西周", "春秋"], ans: 1 },
  { q: "青铜纵目人面像眼球向外突出约多少厘米？", opts: ["6 cm", "11 cm", "16 cm", "21 cm"], ans: 2 },
  { q: "古蜀传说中第一代蜀王叫什么？", opts: ["鱼凫", "蚕丛", "杜宇", "开明"], ans: 1 },
  { q: "青铜神树对应《山海经》中哪棵神树？", opts: ["扶桑/建木", "昆仑神木", "蟠桃树", "若木"], ans: 0 },
  { q: "三星堆遗址面积约为多少平方公里？", opts: ["2 km²", "6 km²", "12 km²", "20 km²"], ans: 2 },
  { q: "三星堆各坑共出土了多少余根象牙？", opts: ["1,300根", "5,000根", "13,000根", "30,000根"], ans: 2 },
  { q: "金杖上的图案中哪两种动物是「鱼凫王」的图腾？", opts: ["龙与凤", "鱼与鸟（凫）", "虎与蛇", "鹰与鹿"], ans: 1 },
];

function QuizTab() {
  const [phase, setPhase] = useState<"start" | "quiz" | "result">("start");
  const [current, setCurrent] = useState(0);
  const [score, setScore] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [answered, setAnswered] = useState(false);
  const [wrongIndexes, setWrongIndexes] = useState<number[]>([]);

  const q = QUIZ_QUESTIONS[current];

  const handleAnswer = (idx: number) => {
    if (answered) return;
    setSelected(idx);
    setAnswered(true);
    if (idx === q.ans) {
      setScore((s) => s + 1);
    } else {
      setWrongIndexes((w) => [...w, current]);
    }
  };

  const handleNext = () => {
    if (current + 1 >= QUIZ_QUESTIONS.length) {
      setPhase("result");
    } else {
      setCurrent((c) => c + 1);
      setSelected(null);
      setAnswered(false);
    }
  };

  const reset = () => {
    setPhase("start");
    setCurrent(0);
    setScore(0);
    setSelected(null);
    setAnswered(false);
    setWrongIndexes([]);
  };

  const medal =
    score >= 13 ? { icon: "🥇", label: "古蜀大祭司", color: "#D4AF37" } :
    score >= 10 ? { icon: "🥈", label: "资深考古学家", color: "#C0C0C0" } :
    score >= 7 ? { icon: "🥉", label: "考古爱好者", color: "#CD7F32" } :
    { icon: "📜", label: "三星堆初学者", color: "#45A29E" };

  return (
    <div style={{ maxWidth: 680, margin: "0 auto" }}>
      {/* Start Screen */}
      {phase === "start" && (
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
          style={{ textAlign: "center", padding: "40px 24px" }}
        >
          <div style={{ fontSize: 64, marginBottom: 24 }}>🏺</div>
          <h2 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 28, fontWeight: 900, color: "#D4AF37", marginBottom: 16, letterSpacing: 1 }}>
            三星堆知识竞答
          </h2>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 14, color: "#8A9BAD", lineHeight: 1.8, marginBottom: 32, maxWidth: 440, margin: "0 auto 32px" }}>
            共 {QUIZ_QUESTIONS.length} 道题，测试你对三星堆文明的了解程度。答完即可获得等级称号，挑战「古蜀大祭司」最高荣誉！
          </p>
          <div style={{ display: "flex", justifyContent: "center", gap: 16, marginBottom: 40, flexWrap: "wrap" }}>
            {[["🥇", "答对13+题", "#D4AF37", "古蜀大祭司"], ["🥈", "答对10-12题", "#C0C0C0", "资深考古学家"], ["🥉", "答对7-9题", "#CD7F32", "考古爱好者"], ["📜", "答对0-6题", "#45A29E", "三星堆初学者"]].map(([icon, range, color, name]) => (
              <div key={name} style={{
                background: "rgba(17,24,32,0.8)", border: "1px solid rgba(255,255,255,0.06)",
                borderRadius: 10, padding: "12px 16px", textAlign: "center", minWidth: 120,
              }}>
                <div style={{ fontSize: 24, marginBottom: 4 }}>{icon}</div>
                <p style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 12, color: color as string, marginBottom: 2 }}>{name}</p>
                <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#3a4550" }}>{range}</p>
              </div>
            ))}
          </div>
          <button
            onClick={() => setPhase("quiz")}
            style={{
              background: "linear-gradient(135deg, #D4AF37, #a8861e)",
              border: "none", borderRadius: 10, padding: "14px 48px",
              color: "#0B0C10", fontFamily: "'Noto Serif SC', serif",
              fontSize: 17, fontWeight: 700, cursor: "pointer", letterSpacing: 2,
              boxShadow: "0 4px 24px rgba(212,175,55,0.4)",
            }}
          >
            开始挑战
          </button>
        </motion.div>
      )}

      {/* Quiz Screen */}
      {phase === "quiz" && (
        <motion.div key={current} initial={{ opacity: 0, x: 30 }} animate={{ opacity: 1, x: 0 }}>
          {/* Progress */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: "#45A29E" }}>
              第 {current + 1} / {QUIZ_QUESTIONS.length} 题
            </span>
            <span style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 14, color: "#D4AF37" }}>
              当前得分：{score}
            </span>
          </div>
          <div style={{ height: 4, background: "rgba(255,255,255,0.06)", borderRadius: 2, marginBottom: 28 }}>
            <div style={{
              height: "100%", borderRadius: 2,
              width: `${((current + 1) / QUIZ_QUESTIONS.length) * 100}%`,
              background: "linear-gradient(90deg, #45A29E, #D4AF37)",
              transition: "width 0.4s",
            }} />
          </div>

          {/* Question */}
          <div style={{
            background: "rgba(31,40,51,0.5)", border: "1px solid rgba(212,175,55,0.15)",
            borderRadius: 14, padding: "24px 28px", marginBottom: 24,
          }}>
            <p style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 18, fontWeight: 700, color: "#EFEFEF", lineHeight: 1.6 }}>
              {q.q}
            </p>
          </div>

          {/* Options */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 24 }}>
            {q.opts.map((opt, idx) => {
              let bg = "rgba(17,24,32,0.8)";
              let border = "rgba(255,255,255,0.08)";
              let color = "#C5C6C7";
              if (answered) {
                if (idx === q.ans) { bg = "rgba(69,162,158,0.15)"; border = "#45A29E"; color = "#45A29E"; }
                else if (idx === selected && idx !== q.ans) { bg = "rgba(212,69,69,0.12)"; border = "#D44545"; color = "#D44545"; }
              } else if (!answered && selected === idx) {
                bg = "rgba(69,162,158,0.1)"; border = "rgba(69,162,158,0.4)";
              }
              return (
                <button
                  key={idx}
                  onClick={() => handleAnswer(idx)}
                  style={{
                    display: "flex", alignItems: "center", gap: 14,
                    background: bg, border: `1px solid ${border}`,
                    borderRadius: 10, padding: "14px 20px", cursor: answered ? "default" : "pointer",
                    textAlign: "left", transition: "all 0.2s",
                  }}
                >
                  <span style={{
                    width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
                    background: answered && idx === q.ans ? "rgba(69,162,158,0.2)" :
                      answered && idx === selected && idx !== q.ans ? "rgba(212,69,69,0.2)" : "rgba(255,255,255,0.05)",
                    border: `1px solid ${answered && idx === q.ans ? "#45A29E" : answered && idx === selected ? "#D44545" : "rgba(255,255,255,0.1)"}`,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontFamily: "monospace", fontSize: 12, color,
                  }}>
                    {answered && idx === q.ans ? <CheckCircle size={14} /> :
                     answered && idx === selected && idx !== q.ans ? <XCircle size={14} /> :
                     ["A", "B", "C", "D"][idx]}
                  </span>
                  <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 14, color, flex: 1 }}>{opt}</span>
                </button>
              );
            })}
          </div>

          {answered && (
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
              <div style={{
                background: selected === q.ans ? "rgba(69,162,158,0.08)" : "rgba(212,69,69,0.08)",
                border: `1px solid ${selected === q.ans ? "rgba(69,162,158,0.25)" : "rgba(212,69,69,0.25)"}`,
                borderRadius: 10, padding: "14px 18px", marginBottom: 20, display: "flex", gap: 10, alignItems: "flex-start",
              }}>
                {selected === q.ans
                  ? <CheckCircle size={16} color="#45A29E" style={{ flexShrink: 0, marginTop: 2 }} />
                  : <XCircle size={16} color="#D44545" style={{ flexShrink: 0, marginTop: 2 }} />}
                <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: "#8A9BAD", lineHeight: 1.7 }}>
                  {selected === q.ans ? "✓ 回答正确！" : `✗ 正确答案是「${q.opts[q.ans]}」。`}
                  {selected !== q.ans && " 继续加油！"}
                </p>
              </div>
              <button
                onClick={handleNext}
                style={{
                  width: "100%", padding: "13px", background: "linear-gradient(135deg, #45A29E, #2c7a77)",
                  border: "none", borderRadius: 10, color: "#fff",
                  fontFamily: "'Noto Sans SC', sans-serif", fontSize: 15, cursor: "pointer", letterSpacing: 1,
                }}
              >
                {current + 1 >= QUIZ_QUESTIONS.length ? "查看最终结果" : "下一题 →"}
              </button>
            </motion.div>
          )}
        </motion.div>
      )}

      {/* Result Screen */}
      {phase === "result" && (
        <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}
          style={{ textAlign: "center", padding: "40px 24px" }}
        >
          <div style={{ fontSize: 72, marginBottom: 16 }}>{medal.icon}</div>
          <h2 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 28, fontWeight: 900, color: medal.color as string, marginBottom: 8, letterSpacing: 1 }}>
            {medal.label}
          </h2>
          <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 14, color: "#8A9BAD", marginBottom: 24 }}>
            你的最终得分：<span style={{ color: "#D4AF37", fontSize: 28, fontFamily: "'Noto Serif SC', serif", fontWeight: 900 }}>{score}</span> / {QUIZ_QUESTIONS.length}
          </p>

          {/* Score visual */}
          <div style={{ background: "rgba(17,24,32,0.8)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 16, padding: "24px", marginBottom: 28, maxWidth: 400, margin: "0 auto 28px" }}>
            <div style={{ display: "flex", justifyContent: "space-around" }}>
              {[
                { label: "正确", value: score, color: "#45A29E" },
                { label: "错误", value: QUIZ_QUESTIONS.length - score, color: "#D44545" },
                { label: "正确率", value: `${Math.round((score / QUIZ_QUESTIONS.length) * 100)}%`, color: "#D4AF37" },
              ].map((stat) => (
                <div key={stat.label} style={{ textAlign: "center" }}>
                  <p style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 24, fontWeight: 900, color: stat.color as string, marginBottom: 4 }}>{stat.value}</p>
                  <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#556372" }}>{stat.label}</p>
                </div>
              ))}
            </div>
          </div>

          {wrongIndexes.length > 0 && (
            <div style={{ background: "rgba(17,24,32,0.6)", border: "1px solid rgba(212,175,55,0.12)", borderRadius: 12, padding: "16px 20px", marginBottom: 24, textAlign: "left", maxWidth: 480, margin: "0 auto 24px" }}>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#D4AF37", marginBottom: 10, letterSpacing: 1 }}>◆ 建议复习以下知识点</p>
              {wrongIndexes.slice(0, 3).map((idx) => (
                <p key={idx} style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#8A9BAD", marginBottom: 4, paddingLeft: 8, borderLeft: "2px solid rgba(212,175,55,0.3)" }}>
                  · {QUIZ_QUESTIONS[idx].q}（答案：{QUIZ_QUESTIONS[idx].opts[QUIZ_QUESTIONS[idx].ans]}）
                </p>
              ))}
            </div>
          )}

          <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
            <button
              onClick={reset}
              style={{
                padding: "12px 32px", background: "linear-gradient(135deg, #D4AF37, #a8861e)",
                border: "none", borderRadius: 10, color: "#0B0C10",
                fontFamily: "'Noto Serif SC', serif", fontSize: 15, fontWeight: 700, cursor: "pointer", letterSpacing: 1,
              }}
            >
              再次挑战
            </button>
          </div>
        </motion.div>
      )}
    </div>
  );
}

// ─── MYTHS TAB ───────────────────────────────────────────────────────────────
const MYTHS = [
  {
    title: "蚕丛开国",
    subtitle: "古蜀第一王的传说",
    icon: "🐛",
    image: "https://images.unsplash.com/photo-1763116987110-0e1bbe16a49e?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=600",
    story: `上古之时，有神人自岷山而来，纵目向外，异于常人，名曰蚕丛。其人教民养蚕织帛，故号「蚕丛」。

蚕丛率众入蜀，开拓成都平原，建立古蜀国第一王朝。据《华阳国志》记载，「蚕丛纵目」，意即其双眼竖生或向外突出，这被考古学者认为正是三星堆纵目人面像的原型来源。

蚕丛死后，其后人将其形象铸成青铜，立于神庙供奉，以金箔覆面，示其神圣不可侵犯之地位。三星堆出土的数十件青铜头像，或即蚕丛与历代先王的祭祀像。`,
    connection: "三星堆纵目人面像",
  },
  {
    title: "建木天梯",
    subtitle: "连接天地的宇宙之树",
    icon: "🌳",
    image: "https://images.unsplash.com/photo-1761472651471-839cb6a57177?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=600",
    story: `《山海经》载：「有木，其状如牛，引之有皮，若缨、黄蛇……其名曰建木，众帝所自上下，日中无影，呼而无响，盖天地之中也。」

建木，是连接天界与人间的神圣天梯，天帝与诸神由此上下往来，是古代「宇宙轴心」观念的具象体现。

古蜀人将此神话铸造为青铜神树：树高396厘米，三层九枝，枝头各有一神鸟（太阳神的使者），树干一侧神龙盘绕而下（象征神界的力量降临人间）。

每逢大祭，大祭司在神树前手持金杖，通过咏唱与燎祭，「乘建木」上达天听，为古蜀国祈福禳灾。`,
    connection: "青铜神树（一号坑）",
  },
  {
    title: "鱼凫化鸟",
    subtitle: "古蜀第三王朝的神话",
    icon: "🦆",
    image: "https://images.unsplash.com/photo-1743952198529-e68b8a9ea972?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=600",
    story: `古蜀历经蚕丛、柏濩、鱼凫三代王朝。鱼凫王以「鱼」与「凫（野鸭）」为图腾，金杖上的鱼鸟图案正是鱼凫王朝的徽号。

传说鱼凫王率众狩猎于湔山，突遇仙人，飞升而去，化为神鸟，永驻于神树之巅，俯瞰古蜀大地。

后人为铭记鱼凫王的神圣，将金杖雕刻成鱼鸟组合图语，意为「鱼化为凫，凫化为神，永护古蜀」。金杖由此成为古蜀王权与神权合一的终极象征。`,
    connection: "金杖（一号坑）",
  },
  {
    title: "大祭司的燎祭",
    subtitle: "三千年前的神圣仪式",
    icon: "🔥",
    image: "https://images.unsplash.com/photo-1775729841536-8335a857c94d?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&q=80&w=600",
    story: `约公元前1200年，三星堆发生了一场史无前例的燎祭仪式。

古蜀大祭司身着绣龙祭服，手持金杖，率众将数百件青铜礼器、黄金面具与数千根象牙一并砸碎、焚烧，投入祭祀坑中。熊熊烈火将这些价值连城的宝物化为灰烬，以此「奉献给神灵」。

这究竟是定期举行的「毁器祭」，还是某场特殊危机（战争、天灾）的绝命仪式？没有人知道答案。大火熄灭之后，三星堆的辉煌也随之走入历史的沉默。

直到3200年后，一个农民的锄头偶然触碰了地层，这段沉默才被打破。`,
    connection: "各祭祀坑（全部文物）",
  },
];

function MythsTab() {
  const [expanded, setExpanded] = useState<number | null>(0);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 24 }}>
      {MYTHS.map((myth, i) => (
        <motion.div
          key={i}
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ delay: i * 0.1 }}
          style={{
            background: "#111820",
            border: `1px solid ${expanded === i ? "rgba(212,175,55,0.3)" : "rgba(255,255,255,0.06)"}`,
            borderRadius: 16, overflow: "hidden",
            transition: "border-color 0.3s, box-shadow 0.3s",
            boxShadow: expanded === i ? "0 0 40px rgba(212,175,55,0.1)" : "none",
          }}
        >
          {/* Image */}
          <div style={{ height: 180, overflow: "hidden", position: "relative" }}>
            <img src={myth.image} alt={myth.title}
              style={{ width: "100%", height: "100%", objectFit: "cover", filter: "brightness(0.7) sepia(0.3) contrast(1.2)" }}
            />
            <div style={{ position: "absolute", inset: 0, background: "linear-gradient(to top, rgba(17,24,32,1) 0%, rgba(17,24,32,0) 60%)" }} />
            <div style={{ position: "absolute", top: 14, left: 14, fontSize: 28 }}>{myth.icon}</div>
            <div style={{
              position: "absolute", bottom: 0, left: 0, right: 0, padding: "16px 20px",
            }}>
              <h3 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 20, fontWeight: 900, color: "#D4AF37", letterSpacing: 1, marginBottom: 2 }}>
                {myth.title}
              </h3>
              <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#8A9BAD" }}>{myth.subtitle}</p>
            </div>
          </div>

          {/* Story */}
          <div style={{ padding: "0 20px 20px" }}>
            <AnimatePresence>
              {expanded === i ? (
                <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                  <p style={{
                    fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: "#8A9BAD",
                    lineHeight: 2, whiteSpace: "pre-line", marginTop: 16, marginBottom: 16,
                  }}>
                    {myth.story}
                  </p>
                  <div style={{
                    display: "flex", alignItems: "center", gap: 8, padding: "8px 12px",
                    background: "rgba(212,175,55,0.06)", border: "1px solid rgba(212,175,55,0.15)", borderRadius: 6,
                  }}>
                    <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 10, color: "#D4AF37", letterSpacing: 1 }}>
                      实物对应
                    </span>
                    <span style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12, color: "#A89060" }}>
                      {myth.connection}
                    </span>
                  </div>
                </motion.div>
              ) : (
                <motion.p
                  initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                  style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, color: "#556372", marginTop: 12, marginBottom: 12, lineHeight: 1.7 }}
                >
                  {myth.story.split("\n")[0].substring(0, 60)}…
                </motion.p>
              )}
            </AnimatePresence>
            <button
              onClick={() => setExpanded(expanded === i ? null : i)}
              style={{
                display: "flex", alignItems: "center", gap: 6, justifyContent: "center",
                width: "100%", background: expanded === i ? "rgba(212,175,55,0.08)" : "rgba(255,255,255,0.03)",
                border: `1px solid ${expanded === i ? "rgba(212,175,55,0.2)" : "rgba(255,255,255,0.06)"}`,
                borderRadius: 8, padding: "10px",
                color: expanded === i ? "#D4AF37" : "#8A9BAD",
                fontFamily: "'Noto Sans SC', sans-serif", fontSize: 13, cursor: "pointer",
                transition: "all 0.2s",
              }}
            >
              {expanded === i ? "收起故事" : "阅读神话全文"}
              <ChevronDown size={13} style={{ transform: expanded === i ? "rotate(180deg)" : "none", transition: "0.2s" }} />
            </button>
          </div>
        </motion.div>
      ))}
    </div>
  );
}

// ─── MAIN PAGE ───────────────────────────────────────────────────────────────
export function CulturePage() {
  const { tab } = useParams<{ tab: string }>();
  const navigate = useNavigate();
  const activeTab = tab || "essence";

  return (
    <div style={{ paddingTop: 64, minHeight: "100vh" }}>
      {/* Header */}
      <div style={{
        background: "linear-gradient(180deg, rgba(31,40,51,0.5) 0%, transparent 100%)",
        borderBottom: "1px solid rgba(212,175,55,0.08)",
        padding: "40px 0 0",
      }}>
        <div className="max-w-7xl mx-auto px-6">
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#D4AF37", letterSpacing: 5, marginBottom: 8 }}>
              CULTURAL IMMERSION / 文化沁润
            </p>
            <h1 style={{ fontFamily: "'Noto Serif SC', serif", fontSize: "clamp(24px,4vw,44px)", fontWeight: 900, color: "#EFEFEF", marginBottom: 4 }}>
              古蜀<span style={{ color: "#D4AF37" }}>文化沁润</span>
            </h1>
            <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 14, color: "#8A9BAD", marginTop: 8, marginBottom: 0 }}>
              深入理解三千年前古蜀文明的精神宇宙
            </p>
          </motion.div>

          {/* Tab bar */}
          <div style={{ display: "flex", gap: 0, marginTop: 32, borderBottom: "1px solid rgba(255,255,255,0.06)", overflowX: "auto" }}>
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => navigate(`/culture/${t.id}`)}
                style={{
                  padding: "12px 24px", background: "none", border: "none",
                  borderBottom: activeTab === t.id ? "2px solid #D4AF37" : "2px solid transparent",
                  color: activeTab === t.id ? "#D4AF37" : "#8A9BAD",
                  fontFamily: "'Noto Sans SC', sans-serif", fontSize: 14, cursor: "pointer",
                  transition: "color 0.2s", whiteSpace: "nowrap", marginBottom: -1,
                }}
                onMouseEnter={(e) => { if (activeTab !== t.id) (e.currentTarget as HTMLElement).style.color = "#C5C6C7"; }}
                onMouseLeave={(e) => { if (activeTab !== t.id) (e.currentTarget as HTMLElement).style.color = "#8A9BAD"; }}
              >
                {t.id === "quiz" && "🏆 "}{t.label}
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
            {activeTab === "essence" && <EssenceTab />}
            {activeTab === "knowledge" && <KnowledgeTab />}
            {activeTab === "quiz" && <QuizTab />}
            {activeTab === "myths" && <MythsTab />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}
