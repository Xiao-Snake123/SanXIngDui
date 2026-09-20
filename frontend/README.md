# 前端 · 三星堆复原对话界面

React 18 + Vite 6 + TypeScript。界面形态是**单栏对话式**（ChatGPT 式），
不是「左侧表单 + 右侧画布」—— 理由见下文。

## 快速开始

```bash
npm install
cp .env.example .env.local   # 可选；默认已指向 http://127.0.0.1:8123
npm run dev                  # → http://127.0.0.1:5173
```

| 命令 | 作用 |
|---|---|
| `npm run dev` | 开发服务器（含 `/agent` 反向代理与 SSE 透传配置） |
| `npm run typecheck` | 仅做类型检查（`tsc --noEmit`） |
| `npm test` | 组件与接口封装的单元测试（vitest + jsdom，离线） |
| `npm run build` | 先类型检查，再构建到 `dist/` |
| `npm run build:only` | 跳过类型检查直接构建（调试用） |
| `npm start` | 用 `server.mjs` 托管 `dist/` 并代理 `/agent` |

> `npm run dev` 默认用 5173。若被别的进程占用，Vite 会自动换端口（实测遇到过 5173
> 被另一个项目绑在 `::1` 上，而 Windows 的 `localhost` 优先解析到 `::1` ——
> 于是「打开的是别的项目」。这种情况请直接用 `http://127.0.0.1:<实际端口>` 访问。

## 环境变量

前端**不持有任何模型 Key**。所有模型调用都在后端完成，前端只访问 `/agent` 前缀。

| 变量 | 说明 |
|---|---|
| `AGENT_BACKEND_URL` | 后端地址，dev server 与 `server.mjs` 都会把 `/agent` 代理到这里 |

> 需要 API Key 时请写进 `backend/.env`。带 `VITE_` 前缀的变量会被打包进前端产物，
> 等于把 Key 公开发布出去。

## 目录结构

```
src/
├── main.tsx                     入口
├── app/
│   ├── App.tsx / routes.tsx     路由装配
│   ├── layout/RootLayout.tsx    全站外壳（导航、页脚、悬浮助手）
│   ├── pages/                   各功能页（ai / museum / culture / developer）
│   ├── components/
│   │   ├── ChatPanel.tsx        单栏对话：消息流 + 底部胶囊输入框
│   │   ├── PromptProposalCard.tsx  候选提示词方案卡（可展开/编辑）
│   │   ├── ArtifactView.tsx     出图结果作为「对话内附件」内联渲染
│   │   ├── AgentTracePanel.tsx  多智能体执行轨迹与质检卡片
│   │   └── ui/                  基础组件库
│   ├── hooks/
│   │   ├── useAgentChat.ts      对话链路（SSE 逐字填充）
│   │   └── useAgentRestoration.ts  出图链路（SSE 事件 → Agent 时间线）
│   └── context/                 全局上下文
├── services/
│   └── agentApi.ts              后端接口封装、SSE 帧解析、类型定义
└── test/setup.ts                测试环境（jest-dom 断言 + 用例后清理 DOM）
```


## 交互设计决策

**1. 为什么是单栏，而不是「左对话 + 右画布」**

右侧的图完全依赖左侧选定的提示词 —— 分成两个区域后，用户得来回看才能建立因果。
单栏把「说的话 → 给的方案 → 选的方案 → 出的图 → 质检结论」排成一条时间线，因果自明，
也更接近人们已经熟悉的对话式交互。

**2. 出图结果内联，而不是跳到另一个视图**

一次出图和产生它的那次对话是同一件事的两个阶段。`ArtifactView` 挂在触发它的那条
助手消息下（由 `ownerMessageId` 维护归属），折叠策略是「成品展开、过程收起」：
图 / 质检结论 / 文案默认可见，9 个节点的执行轨迹默认折叠。

**3. 不做假进度**

所有进度都来自服务端 SSE 事件。后端不可达就明确显示连接失败，
不会走一个假的进度条再给出一张占位图。占位图也会被醒目地标注为
「这不是生成图，是占位示意图」，并给出失败原因与解决方式。

**4. 类型检查是构建的一部分**

`npm run build` 会先跑 `tsc --noEmit`。TypeScript 的严格模式（含
`noUnusedLocals` / `verbatimModuleSyntax`）在构建阶段拦住问题，
而不是留给运行时。

**5. 开发者页只展示真实数据**

`/developer/*` 展示的是**当前运行的服务**报出来的东西：引擎与出图链、存储三层
（PostgreSQL / pgvector / Redis）的实际档位、按 SQL 聚合的质检指标、落库的历史任务与
逐轮明细。接口清单直接读后端的 `/openapi.json` 渲染，所以
「文档里写着的接口一定真的存在」——手写的清单迟早会漂移。

一条纪律：**拿不到数据就显示拿不到的原因**，不用占位数字把页面填满。
这条纪律有测试守着（见下）。

## 测试

```bash
npm test          # vitest run
```

只有两个约束，但都很实在：

1. **不联网**。所有 `fetch` 都在用例里显式替换掉。测试不该依赖「本机是否起了后端」，
   否则它迟早变成「只在我电脑上绿」。
2. **不 mock 被测组件**。测的是渲染结果与真实数据流，不是「函数被调用过」。

覆盖的两处最容易出问题的地方：

| 文件 | 守的是什么 |
|---|---|
| `src/services/agentApi.test.ts` | `openapi.json` 的展平与排序、`prompt_extend` 默认必须关闭、相对图片地址必须挂到 `/agent` 前缀、网络失败返回 `null` 而不是抛异常 |
| `src/app/pages/developer/DeveloperPage.test.tsx` | 页面渲染的是后端返回的值（而非硬编码常量）；**数据库不可用时显示原始报错，且页面上不出现伪造的 `0.0%`** |

第二条是刻意加的：一个把「连不上数据库」渲染成「通过率 0%」的页面，
比一个写着报错的页面危险得多。

## 与后端的接口约定

所有请求都走 `/agent` 前缀，由 dev server 或 `server.mjs` 代理到后端，
因此浏览器只需要知道一个相对地址，无需感知后端端口与部署形态。

| 用途 | 接口 |
|---|---|
| 对话（SSE） | `POST /agent/api/chat/stream` |
| 出图（SSE） | `POST /agent/api/restore/stream` |
| 健康与模型矩阵 | `GET /agent/api/health`、`GET /agent/api/models` |
| 历史任务与质量统计 | `GET /agent/api/tasks`、`GET /agent/api/tasks/{task_id}`、`GET /agent/api/stats/quality` |
| 接口清单（开发者页） | `GET /agent/openapi.json`（FastAPI 自动生成） |

SSE 要求中间层关闭缓冲与压缩：开发代理与 `server.mjs` 都显式设置了
`Accept-Encoding: identity` 与 `X-Accel-Buffering: no`，并剔除 `content-encoding`。
