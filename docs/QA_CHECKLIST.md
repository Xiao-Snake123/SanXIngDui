# 自测清单

> 这份文档是给「现在就要上手点一遍」的人准备的：当前环境状态、起停命令、
> 建议按什么顺序自测、以及**哪些现象看起来像 bug 但其实不是**。
> 设计取舍与实测数据在 [`STORAGE.md`](STORAGE.md) 和 [`HANDOFF.md`](HANDOFF.md)。

---

## 一、当前已就绪的环境

写这份文档时，下面这些都已经在跑（重启机器后按 §二 依次拉起即可）：

| 组件 | 地址 / 位置 | 版本 | 说明 |
|---|---|---|---|
| PostgreSQL | `127.0.0.1:5432`，库 `sanxingdui`，用户 `sxd` | **17.6** | 二进制在 `D:\develop\pgsql`，数据目录 `D:\develop\pgdata` |
| pgvector | 同库（`CREATE EXTENSION vector`） | **0.8.6** | 向量列 `vector(1024)` + HNSW 余弦索引 |
| Redis | `127.0.0.1:6379` | **8.10.1** | 可执行文件 `D:\develop\redis\redis-server.exe` |
| 后端 | **<http://127.0.0.1:8123>** | schema `a64944e4659d` | 交互式文档 `/docs`。**不是 8000**，见下方说明 |
| 前端 | **<http://127.0.0.1:5173>** | Vite 6 | `/agent` 代理已指向后端 8123 |

### ⚠️ 后端在 8123，不是 8000

**8000 端口被另一个项目占了**：`fashionops.api.main:app`（uvicorn，pid 会变）。
它的路由是 `/api/v1/...`，没有 `/api/health` 与 `/api/corpus` —— 所以若把
前端代理指向 8000，页面能打开，但每个请求都返回 `404`，看起来像「我们的后端坏了」。

本项目后端因此固定在 **8123**，并且这个端口已经写进默认值：
`backend/scripts/run.ps1`、`backend/app/core/config.py`、`frontend/vite.config.ts`
与 `frontend/.env.example` 都是 8123 —— 前后端各自直接启动即可，**无需再设 `AGENT_BACKEND_URL`**。

```powershell
npm run dev        # 默认就代理到 8123，无需额外环境变量
```

（早期这里需要手动设 `AGENT_BACKEND_URL=http://127.0.0.1:8123`，因为那时前端默认还是 8000；
现在默认值已统一，设了也不会错，只是没必要。）

### ⚠️ 必须用 `127.0.0.1`，不能用 `localhost`

Windows 上 `localhost` 优先解析到 `::1`，而 `::1:5173` / `::1:5174` 上跑的是**另一个项目**
（`Desktop\Work agent\platform\frontend`）。用 `localhost:5173` 打开会看到别人的页面，
而且它不报错 —— 很容易误判成「我们的前端坏了」。

请一律使用 `http://127.0.0.1:5173`。

### ⚠️ 跑前端测试前必须清掉 `NODE_OPTIONS`

在 IDE 自带的终端里跑 `npm test`，会看到**全部用例文件都失败**，报的是：

```
Error: Vitest failed to find the current suite.
 ❯ src/test/setup.ts:15:1   ← afterEach(...)
      Test Files  3 failed (3)
           Tests  no tests
```

**这不是项目的 bug。** 原因是 IDE 插件往环境里注入了一个 Node 预加载 shim：

```
NODE_OPTIONS = --require=".../extensions/genie/out/vendor/shim/node-language-shim.cjs"
```

它会被加进**每一个** Node 进程（含 vitest 的 worker 内部），而 vitest 的包是双入口的
（`import` → `dist/index.js`，`require` → `index.cjs`）。shim 干扰模块加载后，
用例文件 `import` 到的 `vitest` 变成了**另一份未初始化的实例** ——
它的内部状态是空的，所以 `afterEach`/`describe` 一调用就报「找不到当前 suite」。

**判定依据**（当时就是这么定位的）：写一个只 import `vitest`、不碰任何项目代码的
最小用例，它**同样失败** —— 说明与源码无关。清掉变量后立刻通过。

**修法**（PowerShell）：

```powershell
cd c:\Users\Administrator\Desktop\SXD\SanXIngDui\frontend
Remove-Item Env:NODE_OPTIONS -ErrorAction SilentlyContinue
npm test          # → 4 passed / 31 tests
```

**注意**：在 cmd 里用 `set NODE_OPTIONS=` 常常不生效（取决于外层怎么包装命令），
用 PowerShell 的 `Remove-Item Env:NODE_OPTIONS` 才可靠。

这只是**测试环境**的问题，不影响 `npm run dev` 与后端。

---

## 二、日常起停

```powershell
# ── PostgreSQL ──────────────────────────────────────────────────────────────
$pg = 'D:\develop\pgsql'
& "$pg\bin\pg_ctl.exe" -D 'D:\develop\pgdata' -o "-p 5432 -c listen_addresses=127.0.0.1" -l 'D:\develop\pgdata\server.log' start
& "$pg\bin\pg_ctl.exe" -D 'D:\develop\pgdata' status
& "$pg\bin\pg_ctl.exe" -D 'D:\develop\pgdata' stop

# 连库看一眼
$env:PGPASSWORD='sxd_app_2026'
& "$pg\bin\psql.exe" -U sxd -h 127.0.0.1 -d sanxingdui

# ── Redis ───────────────────────────────────────────────────────────────────
& 'D:\develop\redis\redis-server.exe'          # 前台起；已有实例时会提示端口占用

# ── 后端（在 backend/ 下）────────────────────────────────────────────────────
$env:PYTHONPATH = "$PWD"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8123

# ── 前端（在 frontend/ 下，另开一个终端）────────────────────────────────────
# 默认已指向 8123，无需设置；换端口时才需要 $env:AGENT_BACKEND_URL = 'http://127.0.0.1:<port>'
npm run dev
```

> 现在默认值就是 8123，不设也对。若哪天又被改回 8000，会走成 `fashionops` ——
> 表现为「页面能开、但每个接口都 404」。

---

## 三、先跑这三条一键自检

失败就不必往下手工点了 —— 自动化都没过，手工只会更慢。

| 命令 | 目录 | 预期输出 |
|---|---|---|
| `python -m pytest -q` | `backend` | **322 passed** |
| `python scripts/check_storage.py` | `backend` | **STORAGE CHECK PASS — 45 项全部通过** |
| `npm test` | `frontend` | **39 passed (4 files)** ⚠️ 先清 `NODE_OPTIONS`，见 §一 |
| `npm run typecheck` | `frontend` | 0 error |

`check_storage.py` 会往真实库里写测试数据再自动清掉（外键 CASCADE 也一并验证）。
想看它到底造了什么数据：`python scripts/check_storage.py --keep`。

---

## 四、建议手工自测的路径（按价值排序）

### 1. 主链路：对话 → 选方案 → 出图 → 质检 → 回炉 ⭐ 最该先点

打开 <http://127.0.0.1:5173/ai/scene>，输入类似「大祭司站在青铜神树祭坛前」。

要重点确认四件事：

- **方案卡**显示 `768×1024 · 风格强度 N% · 提示词增强 关`（关 = 选定后不允许模型改写）
- 选定方案出图后，界面明确写着**「已锁定为你选定的提示词」**
- 9 个节点的执行轨迹是**流式**推进的（不是假进度条）
- 若质检不达标触发回炉，第二轮画面应当与第一轮**明显不同**；
  相同就是回炉失效，请参照 §五 第 3 条

### 2. 开发者页：运行数据 ⭐ 这页是「接了数据库」的唯一可见证据

<http://127.0.0.1:5173/developer/runs>

- **PostgreSQL 卡**：`17.6` / `schema a64944e4659d` / `pgvector 0.8.6` / 向量维度 1024
- **会话存储卡**：`redis`
- **向量通道卡**：`pgvector`，并显示「本次启动复用 50 条 / 重算 0 条」
- **质量统计**：任务数 / 通过率 / 平均分 / 平均回炉（含「其中跳过质检 N」）
- **历史任务**：点任意一行 → 展开**逐轮明细**（每轮实际下发的 prompt、分数、修改指令、出图）
- **接口清单** tab：由后端 `/openapi.json` 渲染（18 个操作），不是手写的

数据对不上时先看：`curl -s http://127.0.0.1:8123/api/tasks?limit=3`。

### 3. 降级自测 ⭐ 最有价值的一项

本项目对外的核心承诺是「**不配也能跑，配了连不上必须被看见**」。这条只有把服务停掉才试得出来。

```powershell
# ① 停掉 PostgreSQL，然后重启后端
& "$pg\bin\pg_ctl.exe" -D 'D:\develop\pgdata' stop

# ② 看后端是否照常启动（应当能起来），再看健康检查怎么报
curl.exe -s http://127.0.0.1:8123/api/health | python -c "import json,sys; print(json.dumps(json.load(sys.stdin)['storage'], ensure_ascii=False, indent=2))"
```

期望：**服务正常启动**，`/api/health.storage` 里 `database.available=false` 且
`reason` 是**原始异常**（不是一句「数据库不可用」）；开发者页的「质量统计」显示
拿不到数据的原因，而**不会**渲染出一个伪造的 `0.0%`。

再把 Redis 停掉（重启后端），期望会话退回 `memory`，且健康检查给出降级原因。

测完记得把服务起回来。

### 4. 回炉是否真的改变画面

```powershell
cd backend
$env:PYTHONPATH = "$PWD"
python scripts/check_revision_effect.py
```

期望 `REVISION EFFECT CHECK PASS`，两轮像素差异 99.9% / 100.0%，分数逐轮上升。

**注意退出码**：`0`=通过，`1`=失败，`2`=**未验证**（免费通道被限流、落到占位图，
拿不到真实生成图）。`2` 不是通过 —— 这是刻意为自动化区分的。

### 5. 上游提示词有效窗口（可选，会真实调上游）

```powershell
python scripts/probe_freeimage_tail.py short 3 head2
```

期望：288 字尾部生效 / 732 字尾部被忽略（0 像素不同）/ 584 字把差异放开头则生效。
这一条是「修正指令为什么必须前置」的原始证据。

### 6. 会话是否真的存在 Redis 里

```powershell
& 'D:\develop\redis\redis-cli.exe' -h 127.0.0.1 keys 'sxd:chat:*'
& 'D:\develop\redis\redis-cli.exe' -h 127.0.0.1 zrange sxd:chat:index 0 -1 WITHSCORES
```

在页面上聊几句后，应当能看到会话键与按活跃时间排序的索引；
TTL 约 1800 秒（`ttl sxd:chat:<id>`）。

---

## 五、看起来像 bug、其实不是的现象

| 现象 | 真实原因 | 怎么确认 |
|---|---|---|
| `localhost:5173` 打开是别的项目 | Windows 下 `localhost` → `::1`，那里是另一个项目 | 用 `127.0.0.1:5173` |
| 出图返回**占位示意图**，质检显示 `skipped` | 免费通道间歇性 429 限流；没配 Key 时它是唯一真实出图通道 | 看任务明细里的 `provider`；`python scripts/probe_image_channels.py` |
| 文案像模板写的、很生硬 | 未配 `DASHSCOPE_API_KEY`，LLM 降级为规则与模板实现 | `/api/health` 的 `models_configured=false` |
| 启动日志写「向量复用 50 / 新算 0」，怀疑没生效 | 这正是**生效了**：库里有同 embedder 同指纹的向量，直接跳过 embedding | 改一条史料正文后再启动，会变成「新算 1 条」 |
| 通过率不是 100% 也不是 0% | 分母是**判过分的任务数**，落占位图的任务被计入 `qa_skipped`、不进分母 | 质量统计卡里的「其中跳过质检 N」 |
| 改了 `VECTOR_DIM` 后向量写不进去 | 向量列的维度在建表时固定，改维度必须重建表 | `python scripts/migrate.py --recreate-vectors` |
| `/api/tasks` 里任务越来越多 | 任务表按设计不删除（这是审计记录），目前**没有归档策略** | 见 §六 |

---

## 六、已知限制（明确没做，不是 bug）

| 项 | 现状 | 影响 |
|---|---|---|
| 前端 hooks 无测试 | `useAgentChat` / `useAgentRestoration` 没有单测 | 回调→状态的翻译层出问题只能靠肉眼发现 |
| 会话并发写 | 读-改-写，无分布式锁 | 同一会话并发请求可能覆盖；前端一次只发一轮所以当前不会触发 |
| 任务表归档 | 无 | 长期运行会持续增长 |
| 轨迹存储 | 仍在 `var/traces/*.jsonl` 文件，未入库 | 跨任务检索轨迹要写脚本 |
| 图片二进制 | 只在 `var/outputs/` 落盘，库里存元信息 | 单机够用，多副本需要对象存储 |
| 连接池指标 | 只暴露可用性 0/1，没有 in-use / overflow | 压测前应补 |
| 多副本自动迁移 | `DB_AUTO_MIGRATE=true` 时每个副本启动都会尝试迁移 | 生产多副本应设 `false`，改由发布流程跑一次 |
| **同一进程多次 `asyncio.run`** | 数据库层已能自动重建连接池；但出图 / LLM / Embedding 持有的 `httpx.AsyncClient` **仍绑定在第一个事件循环上** | 在这些领域你会看到 `RuntimeError: Event loop is closed`，随后**静默降级到占位图**。仓库内没有任何脚本会这样用（都是单次 `asyncio.run(main())`），只有 ad-hoc 的 `for t in tasks: asyncio.run(run_once(t))` 会触发。要跑多个任务就一次性放进同一个循环里 |
| 上游窗口是 token 计 | 字符数只是近似 | 预算取保守值 256；不要调大 |
| museum / culture 页素材 | 图片与文案未逐条核验来源 | 对外发布前需要再过一遍 |

---

## 七、提交前必看：仓库里的 4 处重构残留

前后端拆分（`backend/` + `frontend/`）**从未提交过**，`git status` 有 115 项未入库。
拆分后根目录留下了 4 个残留，已用哈希确认是**完全重复**或**已被取代**的文件：

| 根目录残留 | 与谁重复 | 说明 |
|---|---|---|
| `vite.config.ts` | `frontend/vite.config.ts` | 大小与内容完全一致（4055 B） |
| `server.mjs` | `frontend/server.mjs` | 哈希一致（5877 B） |
| `src/services/index.ts` | `frontend/src/services/index.ts` | 拆分后只剩这一个文件没搬走 |
| `.env.example` | `frontend/.env.example` + `backend/.env.example` | 是拆分前的前端模板，已被后者取代 |

**为什么值得处理**：根目录同时存在 `vite.config.ts` 和 `server.mjs`，会让人以为
「在根目录也能起前端」——但根目录没有 `package.json`，跑起来只会报错。
面试官翻仓库时看到两份同名配置，观感也不好。

> ⚠️ **默认端口改成 8123 时，只改了 `frontend/` 与 `backend/` 里的正式文件**，
> 因此这三份残留现在仍是 `8000` —— 上表「完全一致」已不再成立，**更确认它们该删**。

它们**不影响自测**（前端从 `frontend/` 起、后端从 `backend/` 起）。
要清理的话（都是 git 已跟踪文件，可随时 `git checkout` 找回）：

```powershell
cd c:\Users\Administrator\Desktop\SXD\SanXIngDui
Remove-Item .\vite.config.ts, .\server.mjs -Force
Remove-Item .\src -Recurse -Force
Remove-Item .\.env.example -Force
```

另外提醒：**不要把 `.env` 提交上去**。`backend/.env` 含数据库密码，
`.gitignore` 已覆盖它，但 115 项未入库的改动里混进什么都需要自己扫一眼：

```powershell
git --no-pager status --short | Select-String '\.env$'
```

---

## 八、发现问题时，把这些信息给我

```powershell
# 1. 任务完整明细（含逐轮 prompt / 质检 / 违规项）—— 排查单个任务最有用
curl.exe -s http://127.0.0.1:8123/api/tasks/<task_id>

# 2. 该任务的完整执行轨迹（每个节点的输入输出与耗时）
curl.exe -s http://127.0.0.1:8123/api/traces/<task_id>

# 3. 进程内计数器快照（降级比例、各组件调用数）
curl.exe -s http://127.0.0.1:8123/api/metrics

# 4. 后端日志（启动时的存储档位、每次降级的原因都会打在这里）
Get-Content backend\var\server.out.log -Tail 50
Get-Content backend\var\server.err.log -Tail 50
```

另外告诉我**你在哪个页面、点了什么、期望看到什么、实际看到什么**。
有 `task_id` 最好 —— 上面 1、2 两条能把整条链路还原出来。
