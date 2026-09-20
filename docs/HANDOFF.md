# 三星堆 AI 文物复原 · 任务交接文档

> 这份文档有两个用途：
> 1. **交接**：新接手的 AI 读完这一份就知道项目在哪、做到哪、下一步做什么。
> 2. **对话记录**：每轮工作请在文末《工作日志》追加一节，格式已给好。
>
> 最后更新：2026-09-17 · 由 CodeBuddy 完成存储接入与前端收口
>
> **想直接上手点一遍** → 看 [`QA_CHECKLIST.md`](QA_CHECKLIST.md)：
> 环境状态、起停命令、建议自测顺序、以及「看起来像 bug 其实不是」的现象清单。

---

## 0. 一句话现状

前后端已分离为 `frontend/` + `backend/`，ComfyUI 链路已彻底移除，出图改为纯 API（全代码）三级降级。
**「回炉（Self-Correction）实际无效」已修复并实测验证通过**：根因是出图通道有**有效窗口**、
窗口外的文字被静默丢弃，而修正指令原本写在提示词末尾。现已前置到提示词**开头**，
并把预算收紧到实测窗口以下（256 字）。修复前后对比与完整实测数据见 §4。
**当前无已知 P0 阻塞项**，剩余任务见 §9。

**存储层已从「全进程内」换成真实服务**（2026-09-17 本轮）：PostgreSQL 17.6 + pgvector 0.8.6
（任务 / 产物 / 质检记录落库 + 语料向量持久化）、Redis 8（会话存储）。
三者都遵循「不配也能启动、配了连不上必须被看见」，`/api/health.storage` 上报实际档位与失败原因。
设计与实测数据见 [`STORAGE.md`](STORAGE.md)，验证命令 `python scripts/check_storage.py`（38 项）。

**前端也已收口**（同日）：开发者页从「编造的开源社区」改为展示**真实运行数据**
（存储档位 / SQL 聚合指标 / 落库任务与逐轮明细 / 来自 `/openapi.json` 的接口清单），
并补上了此前完全缺失的前端测试设施（`npm test`，16 个用例）。

---

## 1. 项目定位（决定了取舍标准）

- 用途：**Agent 开发工程师求职简历项目**，要能扛住面试深挖。
- 用户明确要求（原话）：**「要生产级，不要 Demo」**（强调过两次）；功能必须**可观测、可评估、可测试**。
- 用户明确要求：**API Key 留空也要能启动 + 自动降级**。
- 用户明确要求：项目要看起来**完全由本人完成**（原仓库痕迹已清理；用户已声明获原作者授权，遇到署名类决策**提示一次即可**，然后按其决定执行）。
- 交流语言：**中文**。
- 选型倾向：优先零/轻依赖，避免难装的重依赖（如 chromadb）。

---

## 2. 目录与技术栈

```
SanXIngDui/
├── backend/          Python 3.13 + FastAPI + LangGraph + SQLAlchemy/asyncpg + pytest
│   ├── app/
│   │   ├── main.py            启动、lifespan、健康检查、代理告警、存储装配
│   │   ├── core/              config / http / errors / logging / metrics
│   │   ├── graph/             builder（图拓扑）+ runner（run_once / arun）
│   │   ├── agents/            planner / supervisor / retrieval_worker /
│   │   │                      restoration_worker / copywriting_worker /
│   │   │                      qa_worker / lexicon（文物与材质档案）
│   │   ├── conversation/      agent / strategies（12 个提示词策略）/ store
│   │   ├── quality/           objective（确定性指标）/ style_guard（加权融合）
│   │   │                      style_profiles（怎么拍）
│   │   ├── imagegen/          base / service（链）/ dashscope / freeimage
│   │   ├── rag/               corpus / embedder / store / rerank
│   │   ├── storage/           db（连接/迁移）/ models（4 张表）/ repository / sessions / vectors
│   │   ├── api/               routes / schemas / sse
│   │   └── training/          本地 Qwen2.5-VL QLoRA（可选，需 GPU）
│   ├── data/corpus/           史料 jsonl（50 条）
│   ├── migrations/            Alembic 迁移（初始 4 张表 + HNSW 向量索引）
│   ├── scripts/               诊断与探针脚本（见 §7）
│   └── tests/                 219 个用例
├── frontend/         React 18 + Vite 6 + TypeScript 5.6 + Tailwind 4 + vitest
│   └── src/app/components/    ChatPanel / ArtifactView / AgentTracePanel /
│                              PromptProposalCard / ...
├── docs/             MODEL_SELECTION.md 等
├── guidelines/
└── README.md
```

---

## 3. 核心架构（面试要能讲的部分）

### 3.1 双编排引擎
`LangGraphOrchestrator` 与 `BuiltinOrchestrator` **共享同一批节点函数**与 `merge_state` 归约语义，
LangGraph 不可用时自动降级，行为一致。图拓扑：

```
planner → supervisor → {retrieval | restoration | copywriting} → supervisor → finalize
                restoration → quality --(未达阈值且未超预算)--> restoration   ← Self-Correction 回路
```

`/api/health` 会返回真实拓扑（`engine.topology`），可自查是否与代码一致。

### 3.2 三个关键的「单一事实来源」
| 概念 | 位置 | 回答的问题 |
|---|---|---|
| `StyleProfile` | `quality/style_profiles.py` | **怎么拍**（机位/光位/色域区间） |
| `MaterialSpec` | `agents/lexicon.py` | **拍什么材质**（青铜/金箔/玉/象牙，各自禁止什么颜色） |
| `ProposalStrategy` | `conversation/strategies.py` | 用户意图 → 4 个可选方案与提示词 |

**材质优先级高于风格**：`effective_family_min()` 里材质要求覆盖风格要求。
理由：风格是审美偏好，材质是物理事实。曾经因为风格档案硬要求 `green>=0.10`，
导致黄金面具被判不通过、回炉指令反而去「补足青铜锈绿」——把金子改成了青铜。

### 3.3 异构质检
- `quality/objective.py`：确定性图像统计（可复现、毫秒级），抓过饱和/镜面高光/糊成塑料/纯色图。
- `style_guard.py`：VLM 裁判，负责统计量做不到的部分（时代错配、形制忠实度）。
- 加权融合；时代错配一票否决 `ANACHRONISM_CEILING = 0.40`。
- 收益递减早停 `MIN_GAIN = 0.02`。

### 3.4 其它约定
- 运行期依赖放 `ContextVar`，**绝不塞进图状态**。
- 图片二进制放进程内 `BlobStore`。
- 出图通道链（`imagegen/service.py`）：`dashscope` → `free-third-party` → `local-placeholder`。
  链路名由 `service.chain()` 真实返回，trace 与日志不得硬编码。

---

## 4. 【已修复】回炉实际无效（保留完整复盘，值得反复读）

> **状态：已修复（2026-09-17）并实测通过。**
>
> - 修复前：两轮出图**逐像素完全相同**、质检分数卡在 0.7162 → 收益递减早停 `give_up`。
> - 修复后：`python scripts\check_revision_effect.py` → **REVISION EFFECT CHECK PASS**
>   第 0→1 轮 99.9% 像素不同（最大像素差 227）、第 1→2 轮 100.0% 不同（最大差 237），
>   分数 0.848 → 0.869 → 0.881 逐轮上升。
>
> 下面保留完整复盘：这类「不报错、只是静默丢字」的上游行为，是本项目里最难查的一类 bug。

### 4.1 现象
前端选方案生成 → 首轮质检不通过（例：0.7162 < 阈值 0.72）→ 触发回炉 →
第二轮出图**逐像素与首轮完全相同**，得分也完全相同（0.7162）→ 收益递减早停 `give_up`。

### 4.2 已排除的可能
| 假设 | 结论 |
|---|---|
| 回炉指令没拼进提示词 | ❌ 已排除。trace 中 `image_request.revised=True`，`_compose_prompt()` 逻辑正确（`restoration_worker.py:298`） |
| 提示词被 `fit_prompt` 截成一样 | ❌ 已排除。两轮文件名哈希不同（`...158ebca71547` vs `...f0729635a6fd`），说明下发的 URL 不同 |
| 回炉指令被尾部截断切掉了 | ❌ 已排除。`fit_prompt()` 已经是「保头 60% + 保尾 40%」 |
| 质检算法对两张图不敏感 | ❌ 已排除。用 `scripts/check_qa_on_two_rounds.py` 离线复算，评估器是确定性的，同一张图永远同样的分 |

### 4.3 真因（已用实验证明）
**上游免费通道会静默丢弃提示词尾部。** 用 `scripts/probe_freeimage_tail.py` 做对照实验：
构造两个提示词，**头部完全相同**，只有结尾一句不同（「整体色调：正红」vs「整体色调：深蓝」）。

| 提示词长度 | 结果 |
|---|---|
| 288 字 | ✅ 尾部生效 —— 81.7% 像素不同 |
| 436 字 | ❌ **尾部被丢弃 —— 最大像素差 0，589824 像素全同**（2026-09-17 补测完成） |
| 584 字 | ❌ 尾部被丢弃 —— 0 个像素不同 |
| 732 字 | ❌ 尾部被丢弃 —— **0 个像素不同**（`max_diff = 0`，589824 像素全同） |

**有效窗口落在 288 字与 436 字之间。**（注意：边界实际按 token 计，
字符数只是近似；见下方 §4.5 ② 的取值理由。）

**推论**：回炉指令按设计写在提示词**最末尾**（`_compose_prompt` 中 `blocks.append(...)` 的顺序），
而生产提示词长度在 1200~2000 字，远超上游有效窗口。
所以**修正指令从来没有到达模型** —— 表现为「回炉了但画面一点没变」。

### 4.4 ⚠️ 一个必须说明的假通过事故
此前 `scripts/check_revision_effect.py` 输出过 `REVISION EFFECT CHECK PASS`，是**假通过**。
原因：它比较的是**文件字节哈希**。上游对同一张图重新编码，JPEG 容器字节不同、像素完全相同，
于是哈希不同 → 脚本误判为「两轮图像不同 → 回炉有效」。
**教训：验证图像是否变化必须比像素，不能比字节。**

### 4.5 修复方案（✅ 已全部完成，2026-09-17）

**① 回炉指令前置**（`backend/app/agents/restoration_worker.py::_compose_prompt`）✅
revision 块从「追加到末尾」改成「插到最前面」。理由：无论上游窗口多小，开头一定在窗口内；
模型对开头指令的注意力权重也更高。
**额外做了一件事**：引导语压到 40 字以内（`REVISION 1 (score 0.85 < 0.95) - apply: ...`）。
因为预算收紧后头部窗口只有 150 字左右，客套的引导语会把真正要执行的修改挤出窗口 ——
模型只看到一句「apply:」却看不到要 apply 什么，等于没修。
第 0 轮 `prompt_locked` 逐字下发的契约保持不变（只影响 `revision > 0` 的轮次）。

**② 收紧免费通道提示词预算**（`backend/app/core/config.py::freeimage_max_prompt_chars`）✅
**1400 → 256**，注释里写明「这个上限是实测出来的，不是猜的」并附探针用法。
取值理由：实测 288 字仍生效、436 字已失效（原计划取的 480 **偏高，会重新制造本 bug**）；
但填充步长约 148 字，真实边界只能定位在 (288, 436] 区间内，且窗口很可能按 token 计，
中文/英文换算不固定 —— 所以不敢贴着 288 取，落到 256（低于实测生效上限约 11%）。
**调大预算不会让模型看到更多**，只会让超窗部分悄悄丢掉。

**③ 修复假通过**（`backend/scripts/check_revision_effect.py`）✅
`_hash()` 改为「解码后对像素数组求哈希」；判定标准从「字节不同」升级为「像素差异 > 0」
（逐通道容差 2，滤掉 JPEG 重编码噪声）；并打印每一轮的最大像素差与差异比例。
另外补了两个检查：
- 回炉轮的下发内容必须**以修正指令开头**，且指令开头 40 字必须落在头部窗口内；
- 落到占位图（没取到真实生成图）时返回 **2（未验证）而不是 0** —— 否则「跳过验证」
  会被自动化当成 PASS，那是另一种假通过。

**④ 补单测**（`backend/tests/`）✅
`tests/test_conversation.py::test_revision_directive_survives_the_truncation_window`：
断言 `revision > 0` 时修正指令出现在 `fit_prompt(prompt, limit)[0]` 的**前 `head_len` 个字符内**。
将来谁把指令挪回尾部，测试立刻红。

**⑤ 回归验证** ✅
```powershell
cd backend
$env:PYTHONPATH = "$PWD"
python -m pytest -q                                   # → 209 passed
python scripts\check_revision_effect.py               # → PASS，两轮像素差异 99.9% / 100.0%
python scripts\probe_freeimage_tail.py short 3 head2  # → 288 尾部生效 / 732 尾部失效 / 584 差异前置生效
```

### 4.6 判别实验已完成：是「窗口」，不是「缓存」

「上游静默丢尾部」有两种解释：**有效窗口**（位置问题）或**按提示词整体缓存**（内容问题）。
可区分的实验是把同一处差异从结尾挪到开头。
2026-09-17 用 `python scripts/probe_freeimage_tail.py head2` 跑完：

| 差异注入位置 | 提示词长度 | 结果 |
|---|---|---|
| 尾部 | 288 字 | ✅ 生效（81.7% 像素不同） |
| 尾部 | 732 字 | ❌ **0 个像素不同** |
| **开头** | 584 字 | ✅ **生效（99.9% 像素不同，最大像素差 223）** |

**结论：窗口问题。** 差异挪到开头就生效 —— 如果是缓存，开头一样不会变。
所以修复①（前置）方向正确。

真实链路佐证：`check_revision_effect.py` 在生产长度提示词（1985 / 2145 / 2146 字）上，
连续两轮回炉都产生了完全不同的画面（99.9% / 100.0% 像素不同）。

---

## 5. 当前已验证通过的状态（不要重复返工）

| 项目 | 状态 | 证据 |
|---|---|---|
| 后端单测 | ✅ 219 passed / ~7.4s | `python -m pytest -q` |
| 前端类型检查 | ✅ 0 error | `npm run typecheck` |
| 前端构建 | ✅ 成功 | `npm run build`（= `tsc --noEmit && vite build`） |
| ComfyUI 移除 | ✅ 彻底 | 全仓 `grep comfyui` 只剩解释性注释 |
| 出图链 | ✅ 如实上报 | `/api/health` → `providers: {free-third-party, local-placeholder}` |
| 材质维度 | ✅ | `python scripts\check_materials.py` → `MATERIAL CHECK PASS` |
| 端到端冒烟 | ✅ | `python scripts\smoke_test.py` → `SMOKE PASS` |
| 对话层 | ✅ | `python scripts\probe_chat.py` → `CHAT PROBE PASS` |
| 前端页面 | ✅ 浏览器实测 | 单列 ChatGPT 式布局、方案卡显示 `W×H · 风格强度 N% · 提示词增强 关`、节点流式、锁定提示词提示 |
| **回炉是否真的改变画面** | ✅ | `python scripts\check_revision_effect.py` → **PASS**；第 0→1 轮 99.9% 像素不同、第 1→2 轮 100.0% 不同；分数 0.848 → 0.869 → 0.881 |
| **出图通道有效窗口** | ✅ 实测 | 288 字尾部生效；436 / 584 / 732 字失效；把差异挪到开头后 584 字生效 |
| **修正指令落在窗口内** | ✅ | `tests/test_conversation.py::test_revision_directive_survives_the_truncation_window` |
| **存储栈（PG+pgvector+Redis）** | ✅ 40 项 | `python scripts\check_storage.py` → **STORAGE CHECK PASS** |
| **语料指纹判定失效** | ✅ | 只改一条史料的正文（`doc_id` 不变）→ `embedded=1 reused=49`；流程见 §9 |
| **前端 SSE 分帧** | ✅ 14 项 | `npm test`；含半帧拼接 / 多帧同包 / ping 注释帧 / 结尾残留帧 |
| **测试能证伪** | ✅ 变异检查 | 注释掉 `consumeSse` 的结尾残留帧处理 → `1 failed / 29 passed`（恰好挂掉对应那条） |
| **pgvector 与进程内余弦一致性** | ✅ | 10 条共同文档，最大偏差 **3.32e-08**；HNSW top10 召回与暴力检索 **100% 重合** |
| **向量持久化复用** | ✅ | 启动日志：`史料语料: 50 条（向量通道: pgvector，维度 1024，复用 50 / 新算 0）` |
| **Redis 跨进程读回** | ✅ | 新建 RedisSessionBackend 实例仍能读到完整 2 轮 + 提案 + 意图，TTL 已挂 |
| **任务落库与接口查询** | ✅ | `POST /api/restore` → `GET /api/tasks`（total=5）→ `GET /api/tasks/{id}` 逐轮明细 → `GET /api/stats/quality` |
| **中文 UTF-8 存储** | ✅ | `length(item)=5 / octet_length=15`（「青铜大立人」5 汉字 15 字节），无双重编码 |
| **前端类型检查** | ✅ 0 error | `npm run typecheck`（测试文件也已纳入 `tsc`） |
| **前端单元测试** | ✅ 16 passed | `npm test`（vitest + jsdom）；含「数据库不可用时不渲染伪造的 0%」这条纪律断言 |
| **前端构建** | ✅ | `npm run build` → `dist/` 549 kB（gzip 174 kB） |
| **开发者页数据通路** | ✅ 经 Vite 代理实测 | `/agent/api/health`(17.6/pgvector/redis)、`/agent/api/stats/quality`、`/agent/api/tasks`、`/agent/api/models`(6 角色)、`/agent/openapi.json`(18 端点) 全部返回真实数据 |

---

## 6. 【需要用户操作】要配置什么

**只需要一件事**：在 `backend/.env`（从 `backend/.env.example` 复制）里填 `DASHSCOPE_API_KEY`。
- 获取：阿里云百炼控制台，有免费额度。
- 这一个 Key 同时解锁 **LLM / VLM / Embedding / Rerank / 出图** 全部层。

推荐配置：
| 配置项 | 建议值 | 理由 |
|---|---|---|
| `MODEL_IMAGE` | `qwen-image-2.0-pro` | 默认；材质还原好。想最强用 `qwen-image-3.0-pro`，想省钱用 `z-image-turbo` |
| `IMAGE_PROMPT_EXTEND` | `false` | **必须关**。开着等于允许模型改写用户的提示词，把「锁定契约」和「质检标尺」一起架空 |
| `FREEIMAGE_ENABLED` | 配好 Key 后设 `false` | 免费通道带水印、受限流、且（见 §4）会丢提示词尾部，会掩盖真实故障 |
| `LORA_ENABLED` | 保持 `false` | 需 GPU，可选 |

域名必须与 Key 归属地一致，否则鉴权失败：北京 `https://dashscope.aliyuncs.com`／新加坡 `https://dashscope-intl.aliyuncs.com`。

可选：`FREEIMAGE_TOKEN`（`auth.pollinations.ai` 免费领）把免费通道配额从 15 秒/张提到 5 秒/张并去水印。

注意：百炼返回的图片 URL 24 小时后失效 —— 本项目已即时下载归档到 `backend/var/outputs/`。

---

## 7. 本机环境踩坑（务必先读，能省几小时）

### 7.1 端口（重要，会浪费时间）

**铁律：本机一律用 `127.0.0.1` 访问，不要用 `localhost`。**

原因：本机还跑着**另一个项目**（服装电商多 Agent 运营中台，前端标题是「服装电商多 Agent 运营中台」／FashionOps）。
它绑在 `::1`，我们的绑在 `0.0.0.0`；而 Windows 上 `localhost` 优先解析到 `::1`，
于是 `http://localhost:5173` 打开的是**别人的项目**。
更麻烦的是 Vite 检测端口占用时会自动跳到 5174，而对方也会跳，**两个应用会同时出现在相邻端口上**：

| 地址 | 实际打开的是 |
|---|---|
| `http://127.0.0.1:5174/` | ✅ 我们的（三星堆） |
| `http://[::1]:5174/` 或 `localhost:5174` | ❌ 别人的（服装电商） |

排查手段（看标题就能区分）：
```powershell
foreach ($u in @("http://127.0.0.1:5174/","http://[::1]:5174/")) {
  $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 8
  "{0} -> {1}" -f $u, ([regex]::Match($r.Content, '<title>(.*?)</title>')).Groups[1].Value
}
```

- **后端端口统一在 `8123`** —— `run.ps1`、`app/core/config.py`、前端代理默认值三处一致。
  **8000 归别的项目**（`fashionops`），不要往那儿启。本机曾出现「8000 上蹲着本项目的
  一个后端实例」的情况（命令行里没有 `--app-dir`，说明是从 `backend/` 目录直接启的），
  它可能是最新代码也可能是僵尸进程，会让前端连上旧代码、表现为莫名其妙的 bug。
  **判断端口上蹲的是谁**（一步到位，别猜）：
  ```powershell
  $p = Get-NetTCPConnection -LocalPort 8123 -State Listen -ErrorAction SilentlyContinue
  if ($p) { (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.OwningProcess)").CommandLine }
  try { (Invoke-WebRequest "http://127.0.0.1:8123/api/health" -UseBasicParsing).Content } catch {}
  ```
  `/api/health` 里 `providers` **不含 `comfyui`** 才说明是清理后的代码。
- **不要同时留着两个后端**（一个 8000 一个 8123）。前端默认只连 8123，
  很容易出现「前端连上了旧代码后端」这种莫名其妙的 bug。
- 前端端口会浮动：5173 被占就跳 5174，再被占就 5175……
  启动后看 Vite 输出，并确认端口上 `0.0.0.0` 那个进程是你自己的：
  `Get-NetTCPConnection -LocalPort <端口> -State Listen | Select LocalAddress,OwningProcess`
- 代理报 `ECONNREFUSED 127.0.0.1:8123` = 你指定的后端死了，不是代理配错了。
  Vite 会把真实错误打在 dev server 终端里，**先看那个终端**。

### 7.2 代理
WinINET 系统代理是 `127.0.0.1:7897`（Clash 类）。`httpx(trust_env=True)` 会把
**localhost 也走代理** → 502 空响应。项目内已用 `app/core/http.py::is_local_url()` +
`make_async_client(trust_env=False)` 处理本机请求；**自己写探针脚本时也必须传 `trust_env=False`**。
公网（百炼、免费通道）用 `trust_env=True`。

### 7.3 PowerShell 5.1
- 控制台是 GBK，`print` 非 ASCII 符号（如 U+26A0 ⚠）会 `UnicodeEncodeError` → 脚本输出用 ASCII。
- `echo "..." >> file` / `Out-File` 默认写 **UTF-16LE**，往 UTF-8 文件追加会产生 NUL 字节，
  git 会判定为二进制。要写文件用：
  `[System.IO.File]::WriteAllText($p, $text, (New-Object System.Text.UTF8Encoding($false)))`
- `Get-Content` 不带 `-Encoding utf8` 时按 GBK 解码 UTF-8 源码 → 行数算少、行会粘连，
  别据此判断「文件被截断」。
- 多行命令里带 `#` 注释容易被截断/回显，**尽量用单行链式命令**。

### 7.4 重启后端
```powershell
$conns = Get-NetTCPConnection -LocalPort 8123 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 900
$env:PYTHONPATH = "c:\Users\Administrator\Desktop\SXD\SanXIngDui\backend"
python -m uvicorn --app-dir "c:\Users\Administrator\Desktop\SXD\SanXIngDui\backend" app.main:app --host 127.0.0.1 --port 8123 --log-level warning
```
启动日志应显示：`出图 provider 链: ['free-third-party', 'local-placeholder']`（**不应出现 comfyui**）。

### 7.5 启动前端
```powershell
# 默认已代理到 8123，无需设环境变量；换端口时才设：
# $env:AGENT_BACKEND_URL = "http://127.0.0.1:8123"
npm --prefix "c:\Users\Administrator\Desktop\SXD\SanXIngDui\frontend" run dev
```

> 两个坑：
> 1. **`AGENT_BACKEND_URL` 用 shell 环境变量是生效的**（`loadEnv(mode, envDir, '')` 的前缀是空串，
>    会带上全部 `process.env`），不需要写 `.env`。已实测确认。
> 2. 某些终端工具会**吞掉开头的 `cd` / `Set-Location`**，导致在仓库根目录执行 `npm run dev`
>    而报 `ENOENT: ...\SanXIngDui\package.json`（根目录根本没 package.json）。
>    **用 `npm --prefix <绝对路径>` 最稳。**

浏览器访问**必须用 `127.0.0.1`**，不要用 `localhost`（原因见 §7.1）。

---

## 8. 脚本清单（`backend/scripts/`）

| 脚本 | 用途 |
|---|---|
| `smoke_test.py` | 检索链路 + **一次**完整复原任务的端到端冒烟（四种 kind 的覆盖在 `tests/test_api.py::TestSyncRestore::test_each_kind_runs`） |
| `probe_chat.py` | 对话层探针（意图路由、方案生成、锁定生成） |
| `check_materials.py` | 材质维度自检 |
| `check_revision_effect.py` | 回炉是否真的改变输入与输出 —— **比像素不比字节**（§4.4 的假通过已修），并检查修正指令是否落在头部窗口内；落到占位图时退出码 **2（未验证）**，不再静默算通过 |
| `diagnose_revision.py` | 强制占位通道复现「回炉无效」 |
| `probe_freeimage_tail.py` | 测出图通道的有效窗口。默认**关掉 `fit_prompt` 截断**（否则量到的是「被我们截断后的字符串」，不是上游窗口）；`headN` 模式把差异放开头，用于区分「窗口」与「缓存」 |
| `check_qa_on_two_rounds.py` | 离线复算两轮图的质检指标，判断质检是否评了同一张图 |
| **`check_storage.py`** | **【本轮新增】真实存储栈端到端自检（38 项）：表/索引/维度、落库与幂等、pgvector 与进程内数值一致性、向量复用、Redis 跨实例读回、外键 CASCADE** |
| **`setup_postgres.py`** | **【本轮新增】本机 PostgreSQL + pgvector 一键初始化（下载/解压/initdb/建库/装扩展，幂等）** |
| **`migrate.py`** | **【本轮新增】Alembic 迁移入口；`--current` 看版本，`--recreate-vectors` 按 VECTOR_DIM 重建向量表并重算** |
| `probe_image_channels.py` | 逐通道连通性探测 |
| `sse_probe.py` | SSE 帧结构检查 |
| `check_gold_mask_path.py` | 黄金面具专项链路 |
| `run.ps1` | 一键启动 |

---

## 9. 剩余任务清单

### P0 · 必修（✅ 已全部完成，2026-09-17）
- [x] **§4.5 ①** 回炉指令前置（`restoration_worker.py::_compose_prompt`）
- [x] **§4.5 ②** 补测 436 字档（结论：已被丢尾）→ `freeimage_max_prompt_chars = 256`
- [x] **§4.5 ③** 修 `check_revision_effect.py` 假通过（比像素不比字节）
- [x] **§4.5 ④** 补单测：修正指令必须落在截断窗口内
- [x] **§4.5 ⑤** 全量回归（209 passed + REVISION EFFECT CHECK PASS）

### P1 · 该做
- [x] 跑 §4.6 的判别实验 → **是窗口问题，不是缓存**（差异前置即生效）
- [x] `backend/README.md`：补「实测有效窗口」一节 + 两条硬约束 + 回归脚本
- [x] `docs/MODEL_SELECTION.md`：补 `probe_freeimage_tail.py` 的实测数据与免费通道真实代价
- [x] **接入真实存储栈**：PostgreSQL 17.6 + pgvector 0.8.6（任务/产物/质检落库 + 语料向量）+ Redis 8（会话）
      —— 设计见 [`STORAGE.md`](STORAGE.md)，验证 `python scripts/check_storage.py`（38 项 PASS）
- [ ] 把提示词预算从「写死 256」升级为**启动期探测**：跑一次探针，按实测值自适应。
      理由：上游窗口会随模型/内容变化，写死的常量迟早再次失真

### P1 · 存储层的已知边界（都不影响当前可用性，但上量/上多副本前必须处理）
- [ ] 多副本部署时把 `DB_AUTO_MIGRATE` 改成 `false`，迁移由发布流程跑一次
      （否则每个副本启动都会尝试迁移，Alembic 之间会互相抢 DDL 锁）
- [ ] 任务表目前**无上限增长**，需要按 `created_at` 分区或定期归档
- [ ] 连接池只暴露了可用性 0/1 指标，缺 in-use / overflow 计数 —— 压测前应补
- [ ] 会话写是「读-改-写」，没有分布式锁。前端一次只发一轮所以实际不并发；
      要支持同一会话并发请求，需要 Redis 锁或 Lua 原子化
- [x] **语料内容指纹已加**（2026-09-17）：`corpus_chunks.content_hash` 记录
      `searchable_text` 的 blake2b-128；复用条件从「doc_id 相同」收紧为
      「doc_id + embedder + 指纹三者都相同」。判定逻辑抽成纯函数
      `entries_needing_embedding()`，因此有离线单测（`tests/test_storage_vectors.py`，8 个用例）
- [ ] 前端只覆盖了开发者页 / API 封装 / SSE 解析；会话主链路的 **hooks**
      （`useAgentChat` / `useAgentRestoration`）仍无测试
- [ ] **异步客户端与事件循环的绑定**（P1，已知且已定位）：
      `httpx.AsyncClient`（`rag/rerank.py`、`rag/embedder.py`、`models/llm.py`、
      `imagegen/{base,freeimage,dashscope}.py`）与 redis 客户端都绑定在**创建它们的事件循环**上。
      同一进程里第二次 `asyncio.run()` 会拿到属于已关闭循环的客户端，
      抛 `RuntimeError: Event loop is closed`，然后**被降级逻辑吞掉** ——
      表现为「出图悄悄变成占位图」。数据库层已修（`Database.ensure_connected`
      检测循环切换后重建池 + 换锁，见 §8 `check_storage.py` 的 2.6 节），
      其余 6 处待统一：建议在 `core/http.py` 加一个「按事件循环缓存客户端」的小工具类，
      再把 6 个调用点换过去。**仓库内没有脚本会触发**（都是单次 `asyncio.run(main())`），
      所以优先级低于前端 hooks 测试

### P2 · 可选
- [x] **编造数据已清理**（2026-09-17）。原「开发者社区页」整页都是虚构内容：假 HF 产物
      （`stars: 1847`）、不存在的接口（`POST /v1/generate`、`Base URL: https://api.sanxingdui.ai`）、
      假 issue 与假社区数据（`Stars 5,134`）。现改为展示**真实运行数据**：
      引擎与出图链、存储三层实际档位、SQL 聚合的质检指标、落库的历史任务与逐轮明细，
      接口清单直接读 `/openapi.json`。另外删除了未被任何地方引用的死组件
      `components/OpenSourceHub.tsx`（同样全是编造内容）。
- [ ] 百炼 `wan2.7-image-pro` 的 `color_palette`（hex + 占比数组）可以把色域质检从
      **事后判分**变成**事前约束**。注意 `wan2.7` 不接受 `negative_prompt`（自动并入正向词），
      且需改 `IMAGE_ENDPOINT_PATH`。当前未启用，需先配 Key 验证真实效果。
- [ ] `backend/var/` 下的历史 trace 里还留着旧 `comfyui` 字符串（运行时产物，非代码）。

---

## 10. 与用户协作的注意事项

1. 用户会**反复确认效果**（尤其是图与三星堆的相关性），改完要给**可复现的验证命令 + 实际输出**，不要只说「已修复」。
2. 用户对**假通过 / 玩具实现**极其敏感。任何验证都必须能证伪。
3. API Key 一律留空，代码必须支持「未配置也能启动 + 自动降级」。
4. 涉及署名/版权类决策，**提示一次**，然后按用户决定执行。

---

## 11. 工作日志

> 追加格式（最新在最上）：

```
### YYYY-MM-DD · <谁> · <做了什么>
- 改了什么（文件:行）
- 验证命令 + 实际输出
- 遗留问题 / 下一步
- 需要用户确认的事项
```

---

### 2026-09-19 · CodeBuddy · 专属 RAG 落地：问答接上自己的语料 + 修掉向量残留

**这一轮把「专属 RAG」从方案推到能用**：后端 `POST /api/ask`、前端引用展示、
57 条评测集、真实 embedding、繁简折叠。详见 [`RAG_PLAN.md`](RAG_PLAN.md) §10。

#### 交付

| 项 | 状态 |
|---|---|
| P1 数据模型 v2（citation 五元组、authority 由来源派生、语料指纹） | ✅ |
| P2 采集 150 条真实可引用卡片（维基 53 + 文库 97） | ✅ 可追溯 100% |
| P2.5 繁简归一化（古籍召回 0 → 3/1/4 条） | ✅ |
| P4 真实 embedding（recall@1 0.719 → 0.860，MRR +0.096） | ✅ |
| P5 golden set 57 条 + 评估器 + CI 阈值 | ✅ |
| P6 `/api/ask` + 前端引用展示 | ✅ |

#### 修掉的 bug 1：切换语料后旧向量没被清理（生产路径）

- **现象**：`check_storage.py` 报「HNSW 召回与暴力检索一致 3/10 (30%)」
  与「两条向量通道的 top3 完全一致 1/3」。
- **根因**：`corpus_chunks` 表里是 **200 行**，而当前语料只有 150 条 ——
  多出的 50 行是 v1 语料的残留。切 `CORPUS_DIR` 不会清理旧行。
  而 `PgVectorIndex._ensure` 的「全部复用就提前返回」分支**排在清理逻辑之前**，
  切换语料后恰好 150 条全部命中复用 → 清理从未执行。
- **影响**：残留行参与 HNSW 召回**挤占 top-k 名额**、参与 **max 归一化改变排序**；
  更根本的是系统可能返回一个**已不在语料里的 doc_id**。
- **修法**：新增 `_purge_stale()`，放在提前返回**之前**；计入 `VectorReport.purged`
  与启动日志（`清理了 N 条不属于当前语料的向量残留`）。
- **验证**：表 200 → 150 行；top10 重合 30% → **100%**；两通道 top3 一致 1/3 → **3/3**。

#### 修掉的 bug 2：前端测试全套失败的真正原因（环境，不是代码）

- **现象**：`npm test` → 3 个文件全部 `Vitest failed to find the current suite`。
- **排查过程**：先怀疑自己的改动 → 写一个**只 import `vitest`、不碰任何项目代码**的
  最小用例，**它同样失败** → 证明与源码无关。再逐一排除：依赖树（npm ls 干净）、
  Node 版本（24.20 满足 engines）、vitest 双实例（无嵌套副本）、vite 缓存（清了无效）。
- **根因**：IDE 插件往环境注入了 `NODE_OPTIONS=--require=".../node-language-shim.cjs"`。
  该 shim 被预加载进**每个** Node 进程（含 vitest worker），干扰模块加载后，
  用例文件 `import` 到的 `vitest` 变成**另一份未初始化的实例**。
- **修法**：跑测试前 `Remove-Item Env:NODE_OPTIONS`。已在 `QA_CHECKLIST.md` §一 写明。
- **教训**：「最小用例也失败」是把「环境问题」与「代码问题」分开的最快一刀。

#### 另外修正的两处不实信息

- `QA_CHECKLIST.md` 写后端在 8000 —— 实际 8000 被另一个项目（`fashionops`）占用，
  本项目后端是 **8123**；（当时）前端必须 `$env:AGENT_BACKEND_URL=http://127.0.0.1:8123`；
  后来前端默认值也已统一为 8123，不再需要手动设。
- 同文档的用例数过时（219/40/30 → **282/45/31**）。

---

### 2026-09-17 · CodeBuddy · 收尾自测：修掉两个「配了库却不落数据」的真 bug

**这一轮不是加功能，是把「接了数据库」这件事查到底**。起因很简单：准备交付自测时随手确认
「跑一次冒烟，库里应该多一行」——结果**一行都没有**，而脚本报的是 `SMOKE PASS`。

#### Bug 1：脚本路径下所有落库静默 no-op

- **现象**：`python scripts/smoke_test.py` → `SMOKE PASS`，但 `restoration_tasks` 里一行都没有。
- **根因**：落库依赖调用方先 `await database.connect()`。FastAPI 走 lifespan 会调，
  但 `scripts/` 下的脚本直接调 `runner.astream()`，**从不经过 lifespan**。
  于是 `db.available` 一直是 False，所有写入被 `if not self.db.available: return False` 静默跳过。
- **为什么危险**：脚本只看「图有没有出来」，照样报 PASS。这是最坏的一类失败 ——
  不抛异常、不报错、只是什么都没写。
- **修法**：连接的建立权收回存储层。新增 `Database.ensure_connected()`
  （幂等 + 带 30s 冷却，避免连不上时每个写入点都试一次 5s 超时），
  `repository` 的 10 处 `if not self.db.available` 全部换成它；
  `HybridRetriever._warm_vectors` 也按需连接（否则脚本会用进程内检索，
  而服务端用 pgvector —— 同一份语料两处走不同通道）。

#### Bug 2：换事件循环后写入失败（外键违约）

- **现象**：同一进程里两次 `asyncio.run(run_once(...))`，第二个任务写质检记录时
  `ForeignKeyViolationError: Key (task_id)=... is not present in restoration_tasks`。
- **根因**：连接池绑定在**创建它的那个事件循环**上。第二次 `asyncio.run` 是新循环，
  却拿到了属于旧循环的连接 → `ensure_task` 写父行失败 → 随后的 verdict 外键违约。
  而任务本身跑得好好的、`ok=True`。也就是「看得见图、看不见数据」。
- **修法**：`ensure_connected` 检测到循环变了就重建池。两个容易漏的细节：
  1. `asyncio.Lock` 也会绑定事件循环，必须**同时换一把新的**，否则下次 connect 直接抛
     "is bound to a different event loop"；
  2. 旧循环已关闭时**不要**走 `dispose()` —— 它会对每个连接调 `terminate()`，
     每个都抛 "Event loop is closed" 并被 SQLAlchemy 打成 ERROR，刷一屏噪音，
     而描述的是件已无法挽回也不必挽回的事。改为直接丢弃引擎引用。

#### 同一根因的第三处（已定位，未修）

出图 / LLM / Embedding 持有的 `httpx.AsyncClient` 也绑定事件循环，
第二次 `asyncio.run` 会让出图**静默降级到占位图**（日志里是
`free-third-party: RuntimeError: Event loop is closed` → `fallback=local-placeholder`）。
**仓库内没有脚本会触发**（都是单次 `asyncio.run(main())`），只有
`for t in tasks: asyncio.run(run_once(t))` 这种 ad-hoc 写法会中招。
已记入 §9 P1，修法是在 `core/http.py` 加「按事件循环缓存客户端」的小工具类后统一 6 处调用点。

#### 验证命令 + 实际输出

- `python -m pytest -q` → **219 passed**
- `python scripts/check_storage.py` → **STORAGE CHECK PASS — 45 项全部通过**
  （新增 2.5「脚本路径的落库」与 2.6「事件循环切换后的落库」两节共 5 项）
- 端到端复现：`python scripts/smoke_test.py` 后库里从 0 行 → **1 行**（该脚本只跑一个任务）
- 原始故障复现（同一进程两次 `asyncio.run`）→ 两个任务都成功落库，不再外键违约
- `npm test` → 30 passed；`npm run typecheck` → 0 error

#### 顺手修掉的不实描述

- `DeveloperPage.tsx` 写「smoke_test 四种任务各跑一遍」—— 实际只跑一次；
  四种 kind 的覆盖在 `tests/test_api.py::TestSyncRestore::test_each_kind_runs`
- 同一处的「211 个用例」已过时（现 219）
- `HANDOFF.md` §8 脚本表同一处错误

#### 交付给用户的自测入口

新增 [`QA_CHECKLIST.md`](QA_CHECKLIST.md)：环境状态、起停命令、按价值排序的自测路径、
「看起来像 bug 其实不是」的现象表、已知限制、以及发现问题时该收集哪些信息。

---

### 2026-09-17 · CodeBuddy · 补 SSE 解析测试 + 修掉向量复用的静默失效

**做了什么**

- **前端 SSE 解析测试**（`src/services/stream.test.ts`，14 个用例）。`consumeSse` 是整个前端
  唯一一处手工实现的分帧逻辑（EventSource 不支持 POST），覆盖四种只在网络抖动时才出现的情况：
  1. 一个帧被 TCP 分片成多次 read（必须靠 buffer 拼接，否则 `JSON.parse` 半截就炸）
  2. 一次 read 里到达多个帧（必须循环切，否则后面的帧卡在 buffer 里）
  3. `: ping` 心跳注释帧（必须忽略）
  4. **流结束时的残留帧**（服务端提前关闭、最后一帧没有结尾空行）
     漏了第 4 条的表现是「图已生成但界面卡在 68%」，极难复现。
  另外覆盖了 `streamRestoration` / `streamChat` 的事件分发、`error` 帧、
  以及 **AbortError 必须向上抛而不是当错误上报**（用户点「停止」不该弹红色错误条）。
- **变异检查**：把 `consumeSse` 结尾的 `if (buffer.trim()) handleFrame(buffer);` 注释掉，
  跑测试得到 `1 failed | 29 passed` —— 恰好只挂掉对应那条。证明测试不是摆设。
- **修掉一个我自己在存储层引入的真实缺陷**：向量复用只比对 `doc_id`，
  而 `doc_id` 是稳定主键（要能追溯回报告页码），所以修史料时人们会保留 id 只改正文 ——
  这种情况下旧向量会被继续复用，**检索静默变差且没有任何报错**。
  - 新增 `corpus_chunks.content_hash`（blake2b-128 of `searchable_text` 的十六进制，32 字符）
  - 复用条件收紧为「doc_id + embedder + 指纹**三者都相同**」
  - 判定逻辑抽成纯函数 `entries_needing_embedding()`：它可以在不连数据库的前提下把所有分支测掉
  - 迁移 `a64944e4659d`；老数据该列为 NULL → 天然不匹配 → 整体重算一次（期望行为）
- `check_storage.py` 增加两条断言：改正文 → `embedded=1 reused=49`；改回 → 同样只重算 1 条。

**验证命令 + 实际输出**
- `python -m pytest -q` → **219 passed**（新增 8 个向量复用判定用例）
- `python scripts\check_storage.py` → **STORAGE CHECK PASS — 40 项全部通过**
  （新增：`[PASS] 正文改动过的史料会被重新向量化 -- embedded=1 reused=49`）
- `npm test` → **3 passed (3) / 30 passed (30)**
- 变异检查：`1 failed | 29 passed (30)`（故意破坏后）

**学到的一点（已写进 STORAGE.md）**
「一次就全绿」不等于测试有效。对最容易漏的那条分支做一次变异检查，成本很低，
但它把「测试能证伪」从一句口号变成了一个可复现的结果。

**遗留**
- 前端 hooks（`useAgentChat` / `useAgentRestoration`）仍无测试；它们把 SSE 回调翻译成界面状态，
  是下一个值得补的地方
- 存储侧剩余边界见 §9「P1 · 存储层的已知边界」

---

### 2026-09-17 · CodeBuddy · 开发者页改为真实运行数据 + 前端补上测试设施

**起因**：用户要求「完成当前任务后继续完善」。两个缺口：
「接入数据库」的成果在前端完全看不到；以及被挂了两次的 P2 编造数据风险项。

**改了什么**
- `frontend/src/services/agentApi.ts`：`AgentHealth` 增加 `storage`（三层实际档位）；
  新增任务/质量统计/模型矩阵/图拓扑/OpenAPI 的客户端与类型
- `frontend/src/app/pages/developer/DeveloperPage.tsx`：**整页重写**。原来的四个 tab
  （开源资源 / API 文档 / 模型广场 / 参与贡献）全是编造内容，现在换成
  **运行数据 / 接口清单 / 模型矩阵 / 本地运行**：
  - 运行数据：存储三层档位 + SQL 聚合质检指标 + 历史任务列表 + 点开看逐轮明细（含实际下发的 prompt）
  - 接口清单：直接读后端 `/openapi.json` 渲染 —— 手写的清单一定会漂移
  - 模型矩阵：从 `/api/models` 渲染每个角色的选型理由与降级链
  - 本地运行：仓库里真实存在的脚本与命令
  - **删除** `components/OpenSourceHub.tsx`：未被任何地方引用的死组件，内含假 GitHub 链接与
    不存在的 `/api/v1/*` 接口
- 新增前端测试设施：`vitest.config.ts` + `src/test/setup.ts` + 两个测试文件（16 个用例）；
  `package.json` 增加 `npm test`；`tsconfig.json` 纳入 `vitest.config.ts`
- `routes.tsx` / `HomePage.tsx`：默认 tab 由 `resources` 改为 `runs`
- `.gitignore`：补 `*.log`（本地 dev server / 测试重定向出来的输出）

**验证命令 + 实际输出**
- `npm run typecheck` → 0 error
- `npm test` → **2 passed (2) / 16 passed (16)**
  其中一条用例专门断言：数据库不可用时页面显示原始报错，且**不出现伪造的 `0.0%`**
- `npm run build` → 成功，`dist/assets/index-*.js` 549.26 kB（gzip 173.57 kB）
- 经 Vite 代理实测开发者页用到的全部接口：
  `health`(PostgreSQL 17.6 / pgvector / redis / corpus 50)、`stats/quality`(pass_rate 1.0)、
  `tasks`(total=1，含真实 task_id)、`models`(6 个角色)、`openapi.json`(18 个端点)
- 浏览器预览：`http://127.0.0.1:5174/developer/runs`（5173 被占用，Vite 自动落到 5174）

**顺带发现并记录**
- 本机 `5173` 被另一个项目绑在 `::1`，Vite 起不来就自动换端口 —— 已写进 frontend/README
- `jsdom` 在 Node 下需要显式安装 `undici`，否则 vitest 报 `Cannot find module 'undici'`
- jest-dom 必须从 `@testing-library/jest-dom/vitest` 引入：只引 `matchers` 运行时能用、
  类型检查会红（典型的「本地没事、CI 才发现」）
- 命令输出重定向到文件再读取，可以绕开工具把长命令误判为 watch 服务的问题

**遗留 / 下一步**
- 前端测试目前只覆盖开发者页与 API 封装；会话主链路（`useAgentChat` /
  `useAgentRestoration` 的 SSE 解析）还没有测试 —— 这是下一个值得补的地方
- 教材类页面（museum / culture）里的图片与文案未逐条核验来源，若对外发布需要再过一遍

---

### 2026-09-17 · CodeBuddy · 接入真实 PostgreSQL + pgvector + Redis

**背景**：用户要求「接入真实的数据库、Redis、向量数据库」，并明确「没有就去下载去配置，
不要 demo」。选定栈：PostgreSQL + pgvector（同栈，少一个服务）+ Redis。

**装了什么（都是真实服务，本机实测通过）**
- 本机无 Docker / 无包管理器 / 无 MSVC，所以走预编译二进制：
  - PostgreSQL **17.6**（EDB 免安装 zip，解压到 `D:\develop\pgsql`，数据目录 `D:\develop\pgdata`，端口 5432）
  - pgvector **0.8.6**（社区预编译包 `andreiramani/pgvector_pgsql_windows`，它实测环境正是 PG 17.6）
  - Redis **8.10.1**（本机本来就在跑，直接复用）
- `CREATE EXTENSION vector` **必须超级用户**：pgvector 的 control 没标 trusted，
  用应用角色会拿到 `permission denied to create extension "vector"`。
  已写进 `scripts/setup_postgres.py` 与 `docs/STORAGE.md`。

**改了什么**
- 新增 `app/storage/`：`db.py`（连接/迁移/health）、`models.py`（4 张表）、`repository.py`（任务/产物/质检/向量写读 + SQL 统计）、`sessions.py`（Memory/Redis 后端 + 门面）、`vectors.py`（pgvector/内存索引）
- 新增 `migrations/`（Alembic，异步 env）+ `alembic.ini`。**alembic.ini 必须纯 ASCII**：
  Alembic 用 `encoding="locale"` 读它，zh-CN Windows 上是 GBK，UTF-8 中文注释会直接 UnicodeDecodeError。
- `app/graph/runner.py`：逐轮落库（出图 / 质检各一行 + 任务终态），失败隔离（写库失败只记指标，不打断任务）
- `app/rag/store.py`：向量通道改为 `VectorIndex` 抽象；`app/rag/embedder.py` 两条通道都按 `VECTOR_DIM` 对齐维度
- `app/conversation/store.py` 只留领域模型；存储搬到 `app/storage/sessions.py`（Redis 字符串 + ZSET 按活跃度淘汰）
- `app/api/routes.py`：`/api/health.storage` 如实上报；新增 `/api/tasks`、`/api/tasks/{id}`、`/api/stats/quality`
- `tests/conftest.py`：新增 `hermetic_storage` fixture，把单测钉在进程内实现（否则会写脏真实库，
  且 CI 上直接红）。真实链路改由 `scripts/check_storage.py` 显式验证
- 新增脚本：`check_storage.py`、`setup_postgres.py`、`migrate.py`
- **顺手修掉的坑**：`.env.example` 里 `FREEIMAGE_MAX_PROMPT_CHARS` 还是 1400（上一轮已把代码默认改成 256）——
  谁复制一次样例文件，上一轮修的「回炉无效」就会原样复活。已改为 256 并写明「这个值是实测出来的」

**验证命令 + 实际输出**
- `python -m pytest -q` → **211 passed**
- `python scripts/check_storage.py` → **STORAGE CHECK PASS — 38 项全部通过**，其中：
  - pgvector 与进程内余弦：10 条共同文档，最大偏差 **3.32e-08**
  - HNSW top10 召回与暴力检索 **100% 重合**；两条通道端到端 top3 **3/3 完全一致**
  - 向量复用：第二次 `reused=50, embedded=0`，**新增 embedding 调用 0 次**
  - Redis：新建后端实例仍能读回完整会话（等价跨进程），TTL 已挂
  - 外键 CASCADE：删任务后产物行 2 → 0
- 服务启动日志：`存储: PostgreSQL=17.6（schema c52d299de6b7）, 会话=redis, 向量=pgvector`
  与 `史料语料: 50 条（向量通道: pgvector，维度 1024，复用 50 / 新算 0）`
- HTTP 端到端：`POST /api/restore`（真实出图 free-third-party，score 0.7936 / accept）
  → `GET /api/tasks` total 可查 → `GET /api/tasks/{id}` 逐轮明细 → `GET /api/stats/quality` SQL 聚合
- 中文完整性：`length(item)=5 / octet_length(item)=15`（「青铜大立人」），无双重编码
- 回归：`smoke_test.py` → SMOKE PASS；`check_materials.py` → MATERIAL CHECK PASS

**一个反直觉但正确的结论（已写进 STORAGE.md）**
50 条语料下 PostgreSQL **不会**选择 HNSW 索引 —— 全表扫更快，规划器是对的。
所以自检断言写成「禁用顺序扫描后能命中 HNSW」，而不是「自然计划必须命中」。
把后者当断言，是一条看起来严格、实际是错的检查。

**遗留**
- 存储层的已知边界（多副本自动迁移、任务表归档、连接池指标、会话并发写、语料内容指纹）
  已列进 §9 的「P1 · 存储层的已知边界」
- 单测不再覆盖真实存储链路（这是刻意的），改动存储实现后**必须跑 `check_storage.py`**

---

### 2026-09-17 · CodeBuddy · 修复「回炉实际无效」+ 完成全部 P0

**改了什么**
- `app/agents/restoration_worker.py::_compose_prompt` —— 修正指令从「追加到末尾」改为
  **插到最前面**，且引导语压到 40 字以内（`REVISION 1 (score 0.85 < 0.95) - apply: ...`）。
  第 0 轮 `prompt_locked` 逐字下发的契约不变。
- `app/core/config.py::freeimage_max_prompt_chars` —— **1400 → 256**，注释写明是实测值。
- `app/imagegen/freeimage.py::fit_prompt` —— docstring 修正：保尾只是兜底，关键指令靠前置，
  「保尾」不能替代「前置」（保住文字 ≠ 模型看得见）。
- `scripts/check_revision_effect.py` —— **比像素不比字节**；新增「指令是否以修正指令开头 +
  开头 40 字是否落在头部窗口内」的检查；落到占位图时返回 **2（未验证）而不是 0**；
  重试放宽到 4 次 / 18s 退避（免费通道常撞 429）；逐轮两两比较像素。
- `scripts/probe_freeimage_tail.py` —— 新增 `headN` 模式（差异放开头，判别窗口 vs 缓存）；
  内部**关闭 fit_prompt 截断**（否则量到的是「我们截断后的字符串」而非上游窗口）；
  结果非单调时显式提示「边界按 token 计、字符数只是近似」；删掉重复的 `__main__` 块。
- `tests/test_conversation.py` —— 新增 `test_revision_directive_survives_the_truncation_window`；
  更新锁定提示词用例（指令现在在开头，断言从 `startswith` 改为「包含」）。
- 文档：`backend/README.md`（新增「实测有效窗口」一节）、`docs/MODEL_SELECTION.md`（补代价表）、
  本文件 §0 / §4 / §5 / §8 / §9。

**验证命令 + 实际输出**
- `python -m pytest -q` → **209 passed** in 6.76s
- `python scripts\check_revision_effect.py` → **REVISION EFFECT CHECK PASS**
  第 0→1 轮：最大像素差 227，99.9% 像素不同；第 1→2 轮：最大像素差 237，100.0% 不同；
  分数 0.848 → 0.869 → 0.881（修复前：两轮逐像素相同、卡在 0.7162）
- `python scripts\probe_freeimage_tail.py short 3 head2` →
  288 字尾部生效 / 732 字尾部被完全忽略（0 像素不同）/ 584 字把差异放开头则生效（99.9% 不同）
- `python scripts\probe_freeimage_tail.py 0 1 2` → 288 生效；436、584 失效

**结论 / 遗留**
- 有效窗口落在 **288 与 436 字之间**；预算取 256（低于实测生效上限约 11%）。
- §4.6 判别实验完成：**是窗口问题，不是缓存问题**。
- 边界按 token 计、随内容浮动，字符数只是近似 —— 已写进代码注释与文档。
- 免费通道间歇性 429：验证脚本已能区分「PASS / FAIL / SKIPPED(未验证)」，不再把跳过当通过。

**需要用户确认**
- §9 P2：开发者社区页仍有编造数据（`stars` / `forks` / `size` / `updated`）。**待确认一次。**

---

### 2026-09-17 · GitHub Copilot · 开发环境排障（端口连环坑）

> 纯环境问题，**未改任何业务代码**。记在这里是因为它极具迷惑性，能坑掉半小时。

**现象**：前端页面能打开，但顶部横幅报「无法连接多智能体后端」。

**排查过程**（按顺序，每步都排除了一个错误假设）：
1. `/agent/api/health` 返回 **500**（不是连接超时）→ 一度怀疑 Vite 代理配错了。
2. 查 `frontend/.env`：**不存在**（只有 `.env.example`）→ 怀疑环境变量没传进去。
3. 实测：`node -e "process.env.AGENT_BACKEND_URL"` 有值，`loadEnv(...)` 也能解析出来
   → **环境变量没问题，这条假设排除**。
4. 打开 Vite dev server 终端，看到真实错误：`connect ECONNREFUSED 127.0.0.1:8123`
   → **我启的 8123 后端已经死了**（终端报 exit code 1，但 shell 退出时 Python 也没了）。
5. 顺手查 `8000`：**上面蹲着本项目的另一个后端实例**，而且是**最新代码**
   （`/api/health` 的 `providers` 只有 `free-third-party` / `local-placeholder`）。

**结论与处置**
- Vite 代理返回 500 且**响应体为空**时，先去看 dev server 终端的行，别去猜代理配置。
- 把前端指向已在运行的 `8000`，不再另起 8123，避免双后端。
- 现状：前端 `http://127.0.0.1:5174`（5173 被另一个项目占了）→ 代理 → 后端 `8000`，
  已验证 `/agent/api/health` 返回 200、`engine=langgraph`。

**新踩到的坑（已写入 §7）**
- 终端工具会吞掉开头的 `cd`，用 `npm --prefix <绝对路径>` 规避。
- `localhost` 会打开另一个项目（`::1` 被占），**一律用 `127.0.0.1`**。

**另外记录一个待处理的代码味道（未修）**
Vite HMR 警告：
```
Could not Fast Refresh ("PROPOSAL_PALETTE" export is incompatible)
```
`frontend/src/app/components/PromptProposalCard.tsx` 同时导出了组件和一个普通常量，
破坏了 React Fast Refresh 的「组件文件只导出组件」约定 —— 改这个文件只能整页刷新。
修法：把 `PROPOSAL_PALETTE` 挪到单独的非组件模块（如 `proposalPalette.ts`）。

---

### 2026-09-17 · GitHub Copilot · 交接前的状态

**做了什么**
- 清除后端目录移动后残留的 `comfyui_enabled` 引用，后端恢复启动（`app/main.py`）。
- 出图链日志改为调用 `image_service.chain()` 真实返回，不再硬编码优先级字符串。
- `probe_chat.py` 去掉已废弃的 `steps/cfg/lora_strength` 字段，改为 `style_strength` + `prompt_extend`。
- 删除 `frontend/public/workflows/`（残留的 ComfyUI 工作流）。
- `backend/README.md`：用例数 161 → 208；替换 ComfyUI 章节为「为什么移除 steps/cfg/LoRA」。
- `docs/MODEL_SELECTION.md`：出图选型改写为全代码 API，补千问图像档位对照表与免费通道代价。
- 新增两个诊断脚本 `probe_freeimage_tail.py`、`check_qa_on_two_rounds.py`。

**验证结果**
- `python -m pytest -q` → **208 passed** in 9.61s
- `GET /api/health` → 200，`providers = {free-third-party: true, local-placeholder: true}`（无 comfyui）
- `check_materials.py` → `MATERIAL CHECK PASS`
- `smoke_test.py` → `SMOKE PASS`
- `probe_chat.py` → `CHAT PROBE PASS`
- 前端浏览器实测通过（`http://127.0.0.1:5173/ai/scene`）

**本轮最重要的发现**
- 定位到「回炉无效」的真因：免费通道静默丢弃提示词尾部。
  实测 288 字尾部生效（81.7% 像素不同），732 字尾部被完全丢弃（0 像素不同）。
- 发现 `check_revision_effect.py` 此前是**假通过**（比字节而非比像素）。

**下一步（交给接手者）**
- 按 §4.5 的 ①~⑤ 执行修复，优先 ① 与 ③。
- 修复前**不要**把「回炉已修复」写进任何文档或 README。

**需要用户确认**
- §9 P2 的编造数据问题（`stars`/`forks`/`size` 等）。
