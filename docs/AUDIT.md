# 三星堆项目 · 生产就绪度审计报告

> 审计时间：2026-09-20
> 审计范围：`backend/`（FastAPI + LangGraph 多智能体）、`frontend/`（React + Vite + TS）、项目根配置与脚本
> 审计方式：全量代码阅读 + 量化统计实测 + 前后端契约核对（只读，未修改任何文件）

---

## 一、一句话结论

这是一个**「半生产 / 半 demo」的混合体**，而且分界线非常清晰——你的直觉是对的，但根因和你猜的略有不同：

| 区域 | 性质 | 判断依据 |
|---|---|---|
| `/ai`（多智能体复原） | **接近生产级** | 真 SSE 事件流、诚实降级（不伪造进度）、可解释引用、有单测 |
| `/developer` | **接近生产级** | 直读 `/openapi.json` 渲染 API 文档、SQL 聚合指标 |
| `/`（首页 Hero） | **纯 demo 皮肤** | 19MB 视频首屏自动播放、外链 Unsplash、鼠标视差 |
| `/museum` | **纯 demo** | 8 件文物数据全硬编码在前端、图片全外链第三方图床 |
| `/culture` | **纯 demo + 可信度风险** | 知识库问答硬编码且附「出处」，绕过 RAG 后端 |

**「不像生产级」的最大拦路虎不是代码风格，而是三类：**

1. **安全**：生产静态服务器仍保留会自动注入 API Key 的代理（与代码注释里声称「已删除」直接矛盾）
2. **数据**：RAG 语料只有 156 条且含脏数据，默认配置还指向「不能当作证据」的旧语料
3. **可信度**：前端硬编码的、带「出处」的百科内容绕过 RAG——对一个考古/文博项目，这比 UI 瑕疵严重得多

---

## 二、量化总览（实测数据）

| 指标 | 数值 | 评价 |
|---|---|---|
| 前端静态资源 | **4 个文件 / 37.93 MB** | 数量少但体积失控（2 个 mp4 占 99.97%） |
| 其中单个视频 | **18.96 MB × 2 份** | 源文件在 `public/`，构建产物 `dist/` 里还有一份 |
| 语料库 v2（在用） | **156 条 / 约 350 KB** | 内容太少，覆盖不了「三星堆」知识域 |
| 语料库 v1（默认指向） | 56 条，**全部无 `source_type`** | 一旦加载即被判为「不可作证据」，问答全拒答 |
| 运行产物 `var/outputs` | 206 文件 / 11.25 MB | 无清理策略，持续增长 |
| traces | 2509 文件 / 20.47 MB | 无轮转/过期策略 |
| 后端代码 | 64 个 `.py`（测试 15 个） | 测试/代码比偏低但后端质量较好 |
| 前端代码 | 79 个 `.ts/.tsx` | 41 个测试用例，覆盖约 2 成 |
| 最大前端文件 | `DeveloperPage.tsx` 46.8 KB（1209 行） | 三个页面均超 800 行 |
| 最大后端文件 | `conversation/agent.py` 59.4 KB | 偏大但尚可 |

---

## 三、你的两个关切（专门回答）

### 关切 1：「静态文件太多，感觉就是个 demo」

**实测：文件不多（4 个），但体积失控（37.93 MB）。** 根因不是「数量多」，而是**资源未治理**：

| 文件 | 大小 | 问题 |
|---|---|---|
| `frontend/public/videos/287384434.mp4` | 18.96 MB | 首屏 `autoPlay` 自动播放；文件名是纯数字无语义 |
| `frontend/dist/videos/287384434.mp4` | 18.96 MB | **构建产物未清理**，被复制了一份（会被误提交/误部署） |

连带问题（这让它更像 demo）：

- `HeroSection.tsx:197-222`：`<video autoPlay muted loop playsInline preload="metadata">`——首屏强制拉 18MB
- `frontend/server.mjs:14-26`：`MIME_TYPES` 表**缺 `.mp4` / `.webm`** → 即使部署了也会以 `application/octet-stream` 返回，浏览器直接下载而非播放
- `HeroSection.tsx:6` 常量 + `MuseumPage.tsx:144-151`：大量外链 `images.unsplash.com` 当「氛围图」，与三星堆实物无关

**结论：是 demo 特征，但根因是「资源未治理 + 未走 CDN」，不是文件数量。**

### 关切 2：「RAG 不完整，内容太少」

**实测确认：v2 语料只有 156 条（wikipedia 56 + wikisource 100），约 350 KB。** 这对于一个要做「带引用的领域问答」的系统，内容量确实严重不足。具体如下：

| 问题 | 实测/位置 | 影响 |
|---|---|---|
| **内容量太少** | 156 条 / 350 KB | 覆盖率低，大量问题检索不到 → 触发「语料中无可靠记载」 |
| **默认配置指向废语料** | `config.py:144` `corpus_dir="data/corpus"`（v1）<br>v1 的 56 条全部 `<MISSING> source_type` | 一旦 `.env` 丢失（容器/新机器），加载 v1 → 被 `NON_EVIDENTIAL_TYPES` 排除 → **所有问答返回拒答**。当前能用完全依赖 `.env:15` 的 `CORPUS_DIR=data/corpus/v2` |
| **校勘注未剥离** | `data/corpus/v2/wikisource.jsonl` 中 `〈…〉` 大量残留；`"note"` 字段 0 命中 | 代码承诺「加工说明随引用展示」（`corpus.py:160-167`），实际 `note=""`，而引文里塞满校勘讨论文字 |
| **分块破坏语义** | `collect_corpus.py:401-431` 按字符硬切；实测块长 **600~900 字**（超出声明的 150~400）；`guji-b6c85410-01` 结尾停在 `…〈舊誤作眊，從目。` | 从句子和校勘注中间切断，BM25 与向量双通道同时受噪 |
| **元数据缺失** | `wikisource.jsonl`：`object=""`、`tags=[]`、`category` 一律填「出土记录」（古籍被错标） | 150 条中约 97 条无法按器物/标签过滤，分面检索形同虚设 |
| **证据阈值失效** | `answerer.py:78` `MIN_EVIDENCE_SCORE = 0.02` | 融合分被压到 (0,1] 后加权，0.02 等于「全部接受」，弱相关片段会被当依据引用 |
| **向量缓存满则零向量** | `embedder.py:152-161` | 缓存满时未命中文本直接得全零向量 → 余弦 0 → 被 `score>0` 过滤**静默丢弃**，不报错 |

**结论：RAG 是「链路能跑通，但数据层不达标」** ——156 条太少，且有脏数据（校勘注）、错误元数据、次优 alpha 组合。

---

## 四、问题清单（按严重程度）

### P0 · 严重（上线前必须处理）

#### 后端安全

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| S1 | **路径穿越 / 任意文件写入** | `core/tracing.py:182-193` 用未校验的 `task_id` 拼路径<br>`schemas.py:92` `task_id` 无任何约束<br>`chat_routes.py:50-52` `session_id` 同样 | `task_id="../../../../tmp/x"` 可在 `var/traces` 外创建/覆盖文件 |
| S2 | **全站无鉴权 + 无限流** | `main.py:182-183`（无 `Depends` 鉴权）<br>全局 `Semaphore`/`rate_limit` **0 命中** | 匿名可无限并发打 `POST /api/restore`（每次真实出图计费 + VLM + 回炉） |
| S3 | **未鉴权的成本型端点 `/api/eval/run`** | `routes.py:344-356`，`limit` 上限 60 | 一行 curl 发起 60 次完整出图链路 → 高额账单 |
| S4 | **`/api/health` 原样泄露代理配置** | `routes.py:173` → `core/http.py:61-74` | 输出 `HTTP_PROXY` 原文（常含 `user:password@`）→ 内网凭据泄露 |
| S5 | **明文 API Key / DB 口令落盘** | `backend/.env:12`（真实可用 Key）、`:8`（DB 口令）<br>`config.py:49` 用 `str` 而非 `SecretStr` | 打包/镜像构建即带出；traceback 会打印 Key |
| S6 | **CORS 通配 + 凭据放行** | `main.py:173-180`：`allow_origins=... or ["*"]` + `allow_credentials=True` | 不安全组合；默认放开全部 methods/headers |
| S7 | **无边界的 Data URI / 图片下载** | `imagegen/base.py:140-161` 无长度上限<br>`schemas.py:72-76` 仅限列表长度≤3，单元素无 `max_length` | 单请求可提交数十 MB base64 → 内存放大 + 上游计费放大 |

#### 前端 / 部署

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| S8 | **生产服务器仍保留自动注入 Key 的代理** | `frontend/server.mjs:54-67`（`/dashscope` 带 `authEnv: DASHSCOPE_API_KEY`、`/dify` 带 `CHAT_APP_API_KEY`）<br>`server.mjs:115-117` `headers.authorization = Bearer ${apiKey}` | `npm start` 后任何人访问 `/dashscope/*` 即用你的 Key 代发请求。**对照 `vite.config.ts:66-73` 注释明确说这两条已删除——dev 侧已修、prod 侧未修** |
| S9 | **根目录存在过期且更危险的 vite 配置** | 根 `vite.config.ts:23-24, 65-94`（同样带 Key 注入代理），`:27` 默认端口写成 `8000`（应为 8123） | 配置分叉；在根目录跑 vite 会用旧配置（接口全 404 + 密钥敞口） |
| S10 | **代理响应一律 `Access-Control-Allow-Origin: *`** | `server.mjs:139`、`:183`（OPTIONS 预检同样 `*`） | 任意站点可跨域调用 `/agent/*`（含耗额度接口）→ CSRF/额度盗用 |
| S11 | **`/culture` 知识库答案与「出处」硬编码** | `CulturePage.tsx:140-216`（11 条 Q&A，出处如《三星堆黄金器物科技分析》（2021）） | 绕过 RAG 后端，直接呈现无法核实的「文献引用」——**对考古项目是最伤可信度的一处** |

### P1 · 中等（影响维护成本与长期健康）

#### RAG 与数据

| # | 问题 | 位置 | 建议 |
|---|---|---|---|
| M1 | 默认 `corpus_dir` 指向不能作证据的 v1；`glob("*.jsonl")` 非递归 | `config.py:144`、`rag/corpus.py:331` | 默认值改 `data/corpus/v2`；改递归 glob |
| M2 | 校勘注未剥离 + `note` 全空 | `data/corpus/v2/wikisource.jsonl` | 重跑 `collect_corpus.py`；装载期断言含 `〈` 必须有 `note` |
| M3 | 分块无句子边界、无重叠、超长 | `collect_corpus.py:401-431` | 用已有的 `split_sentences` + 1~2 句重叠 |
| M4 | 元数据缺失（object/tags 空，category 错标） | `wikisource.jsonl` | 采集阶段补齐；category 加枚举校验 |
| M5 | 向量缓存满 → 静默零向量 | `rag/embedder.py:152-161` | 改 LRU 淘汰；零向量改抛错或显式标记 |
| M6 | alpha=0.20 与 rerank 组合处「已知次优」档 | `config.py:153-174`（注释自承「启用 rerank 后请重扫」） | 重跑 alpha 扫描 |
| M7 | `MIN_EVIDENCE_SCORE=0.02` 形同虚设 | `qa/answerer.py:78` | 改可配置；或用相对阈值（top1 的 30%） |

#### 架构与性能

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| M8 | **事件循环被 CPU 密集调用阻塞** | `quality/style_guard.py:133-155` 在 `async def` 内直调同步 `objective.evaluate`（PIL 解码 + numpy 全图统计）<br>`imagegen/local.py:109-186` 逐行 `draw.line` | 单次质检阻塞事件循环数十~数百 ms，期间**所有 SSE 流与其它请求停顿** |
| M9 | HTTP 客户端与 Reranker 关停时泄漏 | `imagegen/base.py:125-137`（两个模块级 client 无关停钩子）<br>`rag/rerank.py:115-132`（`_reranker` 从不关） | 优雅关停产生 `Unclosed client` 与 fd 泄漏 |
| M10 | 全局单例无锁 | `rag/store.py:458-463` `reset_retriever` 在锁外置 None | `/api/corpus/reload` 与并发检索交错可能拿到已关闭的 retriever |
| M11 | **DB 写入静默吞异常 → 丢数据** | `storage/repository.py` 5 处 `except Exception` | 「钱花了、图出了，库里 0 行」；`sxd_db_error_total` 未聚合进 `/api/metrics` |
| M12 | 启动自动跑 Alembic 迁移 | `config.py:193-195` 默认 `True` | 多副本并发滚动发布时锁冲突/半迁移 |
| M13 | 向量残留清理爆炸半径过大 | `storage/vectors.py:238-282` 每次启动 `DELETE ... NOT IN(当前语料)` | 不同 `CORPUS_DIR` 指向同库会**静默清空**另一份语料的全部向量 |
| M14 | 付费外部调用无并发上限 | `embedder.py:109-132`、`rerank.py:51-79`、`imagegen/service.py:90-129`(300s) | 无背压、无排队，易触发上游 429 后全局降级 |
| M15 | 生产默认开启第三方免鉴权出图 | `config.py:103` `freeimage_enabled=True`（注释自承「生产建议关闭」） | 提示词外发不可控第三方 |
| M16 | 内部异常原文直出浏览器 | `api/routes.py:92-94`、`chat_routes.py:101-104` | 异常消息含上游响应体/SQL 片段/内部路径 |
| M17 | 日志/指标可观测性不足 | `core/logging.py:91-115` 只能配置一次；`core/metrics.py:123-131` 仅关停落盘（崩溃即丢）；`:53-59` 直方图满 5000 随机覆盖 | p95 失真，崩溃丢数据 |
| M18 | DB 连接池偏小 + 30 秒静默降级窗口 | `config.py:190-191` pool=5；`storage/db.py:42` `RETRY_COOLDOWN=30s` | DB 抖动一次 → 30 秒内所有写入静默 no-op |

#### 前端工程化

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| M19 | **48 个 shadcn/ui 组件零引用** | `components/ui/`（48 文件）<br>全仓 import 0 命中 | `npm install` 体积膨胀、`npm audit` 告警面放大几十倍；CSS 侧反而真被污染（见 M20） |
| M20 | Tailwind 把死组件目录也扫进 CSS | `styles/tailwind.css:1-4` `@source '../**/*.{js,ts,jsx,tsx}'` | 生成大量永不使用的 utility class |
| M21 | `DigitalMuseum.tsx` 是完全死代码 | 569 行，无 import；与 `MuseumPage` 数据不一致 | 启用会出现「两个展厅说法不一样」 |
| M22 | **部署与 CI 完全缺失** | 无 `Dockerfile`/`docker-compose`/`.github/`/ESLint/Prettier（用 glob 全量确认） | 无构建门禁；代码里 `// eslint-disable-next-line` 实际无人执行 |
| M23 | **18MB 视频入库 + 首屏自动播放** | `public/videos/287384434.mp4` | 首屏 LCP 与流量成本不可接受 |
| M24 | 单文件过大 + 页面间结构重复 | `DeveloperPage` 1209 行、`CulturePage` 822 行、`MuseumPage` 807 行<br>三页 Tab 骨架/页头近乎逐字相同 | 维护成本高 |
| M25 | **内联样式 650+ 处，主题化能力为零** | `style={{...}}`：`CulturePage` 103、`MuseumPage` 97、`DeveloperPage` 97…<br>Tailwind v4 + `theme.css` 已就位却几乎没用 | 两套样式体系并存；无法换肤；每次 render 新建 style 对象 |
| M26 | **hover 靠 `onMouseEnter` 直接改 DOM** | `Navbar.tsx:149-150/205-206`、`HeroSection.tsx:438-445` 等 | 绕过 React 状态，键盘导航与触摸设备无效 |
| M27 | **3D Tab 用 16ms `setInterval` 驱动整棵子树重渲染** | `MuseumPage.tsx:367-376` | 约 62 次/秒 setState，重渲染 250 行 JSX + 上百 style 对象 |
| M28 | `mousemove` 无节流/rAF | `HeroSection.tsx:26-36` | 每次移动都 setState 并重渲染 537 行 Hero |
| M29 | 粒子背景常驻全站 + 未乘 dpr | `ParticleBackground.tsx:38,52-74`；`RootLayout.tsx:24` 所有路由都渲染 | 高分屏模糊；切后台仍计算；不响应 `prefers-reduced-motion` |
| M30 | **无代码分割 / 无错误边界 / 无 StrictMode** | `routes.tsx:9-25` 四页面全静态 import<br>全仓 `lazy(`/`ErrorBoundary`/`StrictMode` 0 命中 | 访问 `/ai` 也要下载三个 demo 页全部代码；任一组件抛错整站白屏 |
| M31 | **测试覆盖：41 用例仅覆盖 2 成** | 仅 `stream/agentApi/useAgentRestoration/DeveloperPage` 有测试<br>`MuseumPage`/`CulturePage`/`AIPage`/`ChatPanel`/`FloatingAssistant`/`useAgentChat` **零测试** | 后端 354 测试 vs 前端 41 测试，门禁严重不对称 |
| M32 | **a11y 基本未做** | `Navbar.tsx`（293 行）aria 命中 **0**；纯图标按钮无 `aria-label`；textarea 无关联 label；导航下拉仅 `onMouseEnter` 触发（键盘打不开） | 键盘用户无法使用 |
| M33 | 导航存在未实现入口 | `Navbar.tsx:48-53` 声明 `/developer/resources`、`/contribute`<br>`DeveloperPage.tsx:57-62` TABS 只有 4 个；`:1105` 未知 tab 静默回落 | 点了「参与贡献」跳到「运行数据」Tab 且高亮失焦 |
| M34 | 根目录 `.env.example` 已过期且诱导风险 | 根 `.env.example:8` 端口写 8000；`:13-18` 含 `VITE_DASHSCOPE_API_KEY` | 诱导把 Key 写成 `VITE_` 前缀（会打包进浏览器产物） |
| M35 | Google Fonts 走 CSS `@import` + 字体名硬编码上百处 | `styles/fonts.css:1`；`fontFamily:"'Noto Sans SC'..."` 散落各处 | 渲染阻塞；国内访问 `fonts.googleapis.com` 不稳定 |
| M36 | figma 残留（解析到不存在的目录） | `vite.config.ts:9-19` 解析 `figma:asset/*` → `./src/assets`（该目录不存在）<br>`package.json:2-4` 包名仍是 `@zf/my-make-file` | 确认项目源自 Figma/Make 导出脚手架 |

### P2 · 轻微

| # | 位置 | 问题 |
|---|---|---|
| L1 | `qa/answerer.py:78`、`restoration_worker.py:43`、`storage/db.py:42`、`rag/rerank.py:94` 等 | 硬编码常量散落，调参必须改代码 |
| L2 | `_append()` 在 4 个 agent 文件逐字复制；SSE 样板在 `routes.py` 与 `chat_routes.py` 重复整段 | 重复代码 |
| L3 | 全仓约 56 处 `except Exception` | 异常过宽，编程错误（`TypeError`/`ValueError`）也被降级逻辑吞掉 |
| L4 | `rag/store.py:289`、`rag/rerank.py:115` 等 | 类型注解缺失 |
| L5 | `qa_worker.py:293-306` | blob 未命中即重下载，无负缓存；并发下可能重复下载 |
| L6 | `main.py:192-197` | 无鉴权挂载 `var/outputs`，无缓存头 |
| L7 | `storage/repository.py:269` | `ilike(f"%{item}%")` 未转义 `%`/`_`（无注入风险，已参数化） |
| L8 | `main.py:179` | `expose_headers=["X-Task-Id"]` 但无端点设置该头（死配置） |
| L9 | `AssistantContext.tsx:13` | value 每次渲染新建对象，未 `useMemo` |
| L10 | `AIPage.tsx:110-157` | `useMemo` 依赖整个 `restore` 对象（每次 SSE 事件都变）→ 出图过程中整条消息流重渲染 |
| L11 | `ChatPanel.tsx:196-369` | 消息流无虚拟化 |
| L12 | `index.html:1-12` | 无 favicon / meta description / og:* / `<noscript>` |
| L13 | `package.json:97-101` | `pnpm.overrides` 与 npm lock 混用，override 实际不生效 |
| L14 | `vite.config.ts:36-39` | `server.host:'0.0.0.0'` + `allowedHosts:['zfsxd.cpolar.top']`，dev server 暴露局域网 |
| L15 | `FloatingAssistant.tsx:314` | 错误文案写死「后端在 **8000 端口**」，与全项目 8123 不一致 |

---

## 五、前后端契约核对

**结论：现有契约一致，无已发生漂移；但存在结构性风险。**

| 前端类型 | 后端定义 | 结果 |
|---|---|---|
| `RestoreRequest` | `schemas.py:43-97` | ✅ 字段一一对应（含新加的 `fast`/`model_image`） |
| `PromptOverride` | `schemas.py:18-40` | ✅ 完全一致 |
| `AskResult` / `AskCitation` | `schemas.py:166-209` | ✅ 一致（含 `refused/caveats/authority/locator/note`） |
| `AgentHealth` / `StorageHealth` | `schemas.py:140-152` | ✅ 一致 |
| `AGENT_ENDPOINTS` | `routes.py` + `chat_routes.py` | ✅ 全部存在，无悬空端点 |
| SSE 帧类型 | `chat_routes.py:151-152` + `agent.py` | ✅ 一致 |
| `QaVerdict.skipped/raw_score/capped_by` 语义 | `useAgentRestoration.ts:273-309` | ✅ 正确区分「跳过质检」与「不达标」，没用 0 冒充 |

**结构性风险：**

1. **前端类型是手写的，没有从后端生成。** 项目很聪明地用 `/openapi.json` 渲染 API 文档避免文档漂移，但**类型定义没享受同样保护**——`RestorationResult` 几十个字段全手抄。后端改字段时前端不报错，只在运行时拿到 `undefined`。
   **建议**：用 `openapi-typescript` 从 `/openapi.json` 生成 `schema.d.ts`。

2. **没有契约测试。** 后端测自己的 schema，前端测自己的 mock，两者之间**无真实联调**。
   **建议**：CI 加一步，起后端跑已有的 `backend/scripts/smoke_test.py` 与 `sse_probe.py`。

---

## 六、做得好的部分（值得保留）

审计不是只挑毛病，以下几点明显优于多数同类项目，重构时应保留：

- **诚实降级**：不伪造进度、不伪造分数。占位图标注 `skipped` 而非用 0 冒充（这是很多项目做不到的）
- **引用不可伪造**：`qa/answerer.py:9-15, 250-261` 用编号映射而非让模型输出 `doc_id`，代码侧校验引用——RAG 里少见的正解
- **降级可观测**：`metrics.record_degradation` + `RetrievalDiagnostics` + `/api/corpus` 暴露真实档位，「降级被看见」做得到位
- **`content_hash` + `embedder` 双条件向量复用**（`storage/vectors.py:345-367`）
- **事务边界**：`(task_id, round_index)` 唯一键 + upsert，逐轮落库而非终态一次写，崩溃可恢复
- **无 SQL 注入**：全部走 SQLAlchemy 参数绑定
- **SSE 分帧健壮**：`consumeSse` 处理了「流结束时最后一帧无空行」，避免「图已生成但界面卡在 68%」

---

## 七、优化路线图（建议优先级）

### 第一阶段：上线前阻断项（P0）

1. 删 `frontend/server.mjs` 的 `/dashscope` + `/dify` 代理（S8）——**这是最危险的，等于把计费 Key 做成公开代理**
2. 删除根目录 `vite.config.ts` / `server.mjs` 副本，统一到 `frontend/`（S9）
3. 去掉通配 CORS（`server.mjs:139,183`）（S10）
4. `task_id`/`session_id` 加白名单约束 + `persist()` 路径相对性断言（S1）
5. 接入鉴权 + 限流；`/api/eval/run` 默认关闭（S2/S3）
6. 轮换并摘除明文 Key，改 `SecretStr`（S5）
7. `/api/health` 代理信息脱敏（S4）
8. `/culture` 知识库改调 `/api/ask` 渲染真实引用（S11）

### 第二阶段：数据层达标（RAG）

9. `corpus_dir` 默认值改 `v2` + 递归 glob（M1）
10. 重跑 `collect_corpus.py`：剥离校勘注、补 `note`、按句分块 + 重叠（M2/M3）
11. 补齐 `object`/`tags`、修正 `category` 枚举（M4）
12. **扩充语料**：156 条 → 目标是至少 500~1000 条（这是「内容太少」的根本解）
13. 修向量缓存满时的静默零向量（M5）
14. 重跑 alpha 扫描并写回（M6）；`MIN_EVIDENCE_SCORE` 改成可配置并标定（M7）

### 第三阶段：稳定性与性能

15. 质检 CPU 调用移线程池 `asyncio.to_thread`（M8）
16. 补全局 `aclose()` 钩子（M9）；`reset_retriever` 加锁（M10）
17. DB 写入区分「不可用降级」与「约束违约告警」（M11）
18. 生产关闭 `db_auto_migrate`（M12）；向量清理按 fingerprint 分桶（M13）
19. 加 Semaphore 限并发 + 429 熔断（M14）；`freeimage_enabled` 默认 `False`（M15）
20. 异常对外只回 `error_type` + 通用文案（M16）
21. 指标周期落盘；直方图改 t-digest（M17）

### 第四阶段：前端治理（去 demo 化）

22. 18MB 视频移出仓库 → 对象存储/CDN + 压缩到 2~3MB + 进视口才加载（M23）
23. 删 48 个死 `ui/` 组件及其几十个依赖（M19）；删 `DigitalMuseum.tsx`（M21）；清 figma 残留（M36）
24. 收窄 Tailwind `@source`（M20）
25. 拆三个巨型页面 + 抽 `<PageHeader>`/`<TabBar>`；颜色统一走 `theme.css` 变量（M24/M25）
26. hover 改 `hover:` 伪类（M26）；`setInterval 16ms` 改 CSS animation/rAF（M27）；`mousemove` 节流（M28）
27. 粒子背景按 dpr + 响应 `prefers-reduced-motion` + 仅首页渲染（M29）
28. 四页面 `React.lazy` + `<ErrorBoundary>` + `<StrictMode>`；`manualChunks` 拆包（M30）
29. 补 `/ai` 链路与页面级测试；CI 设覆盖率下限（M31）
30. 系统补 a11y：图标按钮 `aria-label`、表单 `<label htmlFor>`、下拉键盘可达（M32）
31. 字体自托管 + 字体名提 CSS 变量（M35）
32. 补 `Dockerfile` + `docker-compose` + GitHub Actions + ESLint/Prettier（M22）

---

## 八、关于「静态文件」与「demo 感」的最后判断

你的直觉方向是对的，但**准确的诊断是**：

- 不是「文件太多」——只有 4 个静态文件。
- 是**单个视频 19MB 且被复制两份**（`public/` + `dist/`），加首屏自动播放，加 `MIME_TYPES` 缺 `.mp4` 导致部署后也播不了。
- 叠加**文物/时间轴/考古图数据全硬编码在前端**（`MuseumPage.tsx:9-151`、`DigitalMuseum.tsx:5-102`），以及**图片全外链第三方图床**——这两点才是「像 demo」的真正来源：**内容不可运营**（改文案要改代码重新构建）、**资源不可控**（对方删图即全线破图）。

把「数据下沉后端 + 资源迁 CDN/对象存储 + 删死代码」这三件事做完，`/museum`、`/culture` 就从 demo 变成可运营的产品页面了。
