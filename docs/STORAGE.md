# 存储层：PostgreSQL + pgvector + Redis

> 本文档记录**为什么这样接**，而不是怎么调用 API。
> 接口清单在 [`../backend/README.md`](../backend/README.md)，复现步骤在文末。

---

## 1. 三层存储与降级矩阵

| 用途 | 生产实现 | 降级实现 | 降级后失去什么 |
|---|---|---|---|
| 任务 / 产物 / 质检记录 | **PostgreSQL 17** | 不落库（只留 JSONL trace） | 无法用 SQL 查询历史任务；指标只能靠进程内计数器，**重启归零** |
| 语料向量 | **pgvector 0.8（HNSW + 余弦）** | 进程内 numpy 暴力检索 | 召回质量不变；**重启要重新调一次 embedding（重复花钱）**，且多副本各算一遍 |
| 对话会话 | **Redis 8** | 进程内 TTL 存储 | 会话不能跨进程共享 → 多副本部署时用户会「失忆」；滚动重启丢上下文 |

降级方向是单向的，且**每一层都遵循项目一贯的原则：不配也能启动，配了却连不上必须被看见**。
`GET /api/health` 输出的是**实际在用哪一档**：

```json
{
  "storage": {
    "database": { "backend": "postgresql+asyncpg", "available": true,
                  "server_version": "17.6", "pgvector_version": "0.8.6",
                  "schema_revision": "c52d299de6b7", "reason": null },
    "sessions": { "backend": "redis", "available": true, "reason": null },
    "vector":   { "backend": "pgvector", "pgvector_version": "0.8.6", "dimension": 1024 }
  }
}
```

连不上时 `reason` 是**原始异常**（`OperationalError: ...`），不是一句「数据库不可用」。
这不是锦上添花：最坏的情况不是数据库挂了，而是**配了 `DATABASE_URL` 却没连上、日志还写着一切正常**。

---

## 2. 为什么是这三个

**向量库用 pgvector 而不是独立服务（Qdrant / Milvus）。**
本项目在 50~万级语料；这个规模下独立向量服务的运维成本远大于收益。
PostgreSQL 本身就是 ACID 数据库，pgvector 让「关系数据 + 向量」共用一个事务与一份备份。
真到了 HNSW 撑不住的规模，再拆出去也不晚 —— 那时 `VectorIndex` 抽象已经在了
（`app/storage/vectors.py`），加一个适配器即可，上层检索逻辑不用动。

**Redis 只做会话，不做缓存。**
会话的问题不是性能，是**行为**：进程内存储在多副本下会漂移、滚动重启会丢上下文、
运维侧看不到会话在哪。这三件事才是上 Redis 的理由。目前没有引入任何缓存语义
（那会带来一致性问题的另一整套复杂度）。

---

## 3. 数据模型

```
restoration_tasks   一次复原任务一行：输入摘要 + 最终质检结论 + 降级情况 + 耗时
   └─ task_images   每一轮出图一行（round_index 唯一）：实际下发的 prompt / provider / 尺寸
   └─ qa_verdicts   每一轮质检一行（round_index 唯一）：分数 / 判定 / 修改指令 / 违规项
corpus_chunks       史料分块 + 向量（embedding vector(1024) + HNSW 余弦索引）
```

四个决定值得单独说：

### 3.1 `task_images.prompt` 存的是**实际下发的那份文字**

不是规划阶段的草稿。回炉轮次下发的提示词是被改写过的（修正指令前置），
所以「这张图到底是用什么提示词生成的」这个问题的答案，只能在落库时抓取。
不存它，事后复现一张图就得去翻 JSONL trace。

### 3.2 逐轮落库，而不是任务结束一次性写

理由有两个，第二个更重要：

1. 回炉是逐轮的，第 2 轮跑完时第 1 轮的分数必须已经可查；
2. **钱已经花了** —— 任务中途崩溃时，前面几轮的出图与质检记录必须留下来。
   否则「失败任务卡在哪一轮」只能靠猜。

写入钩子在 `app/graph/runner.py::astream`，落在节点执行完、状态已合并之后。

### 3.3 落占位图时 `qa_skipped` 单独记录

落占位图（没配 Key 且免费通道不可用）时质检是 `skipped` —— 没有可判定的对象。
它必须和「判了但没通过」区分开：把 skipped 算进通过率分母，指标会凭空变差。
这类**口径错误比代码 bug 更难发现**，因为它看起来像个合理的数字。

SQL 侧的实现见 `TaskRepository._ratio_view`：分母是 `tasks - qa_skipped`。

### 3.4 复用一条向量，要同时满足两个条件

「库里已经有这条向量了，不用重算」这个判断有两个独立的失效来源，
少考虑任何一个都会让检索**静默变差**（不报错、不打日志，只是「召回好像不太准」）：

| 条件 | 列 | 不检查的后果 |
|---|---|---|
| 同一个 embedding 模型 | `embedder` | 换模型后旧向量与新 query 不在同一语义空间，余弦值没有意义 |
| 正文没变过 | `content_hash` | `doc_id` 是稳定主键（要能追溯回报告页码），所以修史料时人们会**保留 id 只改正文**；不看指纹就会继续用旧向量 |

判定逻辑抽成了纯函数 `entries_needing_embedding()`，因此可以在**不连数据库**的前提下
把所有分支测掉（`tests/test_storage_vectors.py`）。之所以要这么较真：
这一处判断写错不会抛异常，只会让「检索变差」变成一个查不出原因的现象。

另外，加 `content_hash` 列之前写入的行该列是 `NULL`，天然不等于任何指纹，
于是迁移后会整体重算一次 —— 这是**期望行为**：宁可多花一次 embedding 的钱，
也不要用一批来源不明的向量。

---

## 4. 向量是「派生数据」，任务记录不是

这个性质决定了哪些操作可以粗暴、哪些绝对不行：

| | 向量（`corpus_chunks`） | 任务记录（`restoration_tasks` 等） |
|---|---|---|
| 能否从源头重建 | 能（语料 jsonl + embedding 模型） | **不能**（出图会花钱、且结果不可复现） |
| 维度变更 | 直接 `DROP TABLE` 重建即可 | 必须走 Alembic 迁移，逐列演进 |

所以 `python scripts/migrate.py --recreate-vectors` 可以毫无顾虑地删表重建，
而任务表永远只走 `alembic upgrade head`。

**一个实测收益**：向量持久化之后，重启不再重复调用 embedding。启动日志：

```
史料语料: 50 条（向量通道: pgvector，维度 1024，复用 50 / 新算 0）
```

`复用 50 / 新算 0` 就是这条设计在本地跑出来的结果。

---

## 5. 本机部署（Windows，免安装发行版）

本机没有 Docker、没有包管理器、也没有 MSVC（编译不了 pgvector），所以走预编译二进制：

```powershell
cd backend
python scripts/setup_postgres.py      # 幂等：下载 / 解压 / initdb / 启动 / 建库建角色 / 装扩展
python scripts/migrate.py             # 建表（Alembic upgrade head）
```

`setup_postgres.py` 做四件事，重复执行安全：

| 步骤 | 默认值 | 说明 |
|---|---|---|
| PostgreSQL 二进制 | `D:\develop\pgsql` | EDB 官方免安装 zip，约 315 MB |
| pgvector | `0.8.6` | 社区预编译包（Windows 无编译环境），覆盖 PG13~PG18 |
| 数据目录 | `D:\develop\pgdata` | `initdb -E UTF8 --locale=C`，仅监听 `127.0.0.1:5432` |
| 角色 / 库 | `sxd` / `sanxingdui` | `CREATE EXTENSION vector` 需要超级用户，所以由脚本代劳 |

> **注意**：`CREATE EXTENSION vector` 必须由超级用户执行 ——
> pgvector 的 control 文件没有标 `trusted`，普通角色会拿到
> `permission denied to create extension "vector"`。

`.env` 只需两行（其余走代码默认值）：

```ini
DATABASE_URL=postgresql+asyncpg://sxd:sxd_app_2026@127.0.0.1:5432/sanxingdui
REDIS_URL=redis://127.0.0.1:6379/0
```

---

## 6. 验证：`scripts/check_storage.py`

**这是本项目里唯一能证明存储真接上了的东西。** 原因见下一节。

```powershell
python scripts/check_storage.py            # 全量自检，跑完自动清理测试数据
python scripts/check_storage.py --keep     # 保留数据，便于人工查库核对
```

实测输出（40 项全部通过）：

| 分区 | 关键项 | 实测结果 |
|---|---|---|
| 基础设施 | 表 / 向量列维度 / HNSW 索引 | `vector(1024)`、`USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)` |
| 落库 | JSONB 往返、回炉轮次幂等 | `feedback=['降低镜面高光']` 无损；同一 `(task_id, round_index)` 重写仍是 1 行 |
| 执行链路 | 真实跑一次任务后的库内状态 | `status=ok`、1 轮出图、1 轮质检、`qa_skipped=True` 被如实标记 |
| pgvector | 与进程内余弦的数值一致性 | 共同 10 条，**最大偏差 3.32e-08** |
| pgvector | HNSW 召回 | top10 与暴力检索 **重合 10/10 (100%)** |
| pgvector | 两条通道的端到端排序 | 3/3 条 query 的 top3 **完全一致** |
| pgvector | 向量复用 | 第二次启动 `reused=50, embedded=0`，**新增 embedding 调用 0 次** |
| pgvector | **改过正文的史料会被重算** | 只改一条的 `text`（`doc_id` 不变）→ `embedded=1, reused=49` |
| pgvector | **指纹对内容敏感** | 改回原文 → 同样 `embedded=1, reused=49` |
| Redis | 跨实例读回 | 新建一个后端实例仍能读到完整 2 轮、提案与意图无损、TTL 已挂 |
| 清理 | 外键 CASCADE | 删除任务后，产物行从 2 → 0，**随任务一起被删除** |

其中「与进程内余弦数值一致」这条最关键：pgvector 的 `<=>` 返回的是**余弦距离**，
而进程内实现算的是**归一化点积（相似度）**，两者差一个 `1 - x` 的换算。
写错这个符号不会报错，只会让检索悄悄变成「最不相关优先」——
一条断言把它钉住了。

### 关于 HNSW 索引的一个诚实说明

50 条语料下，PostgreSQL 规划器**不会**选择 HNSW 索引（全表扫更快，它是对的）。
所以自检里的断言是「**禁用顺序扫描后能命中 HNSW**」，而不是「自然计划必须命中」：

```
[PASS] HNSW 索引可被规划器使用（禁用顺序扫描后命中）
       -> Index Scan using ix_corpus_embedding_hnsw on corpus_chunks
[info] 50 条语料下的自然计划首行: Limit (cost=11.71..11.73 rows=10)
       (未走 HNSW 属正常：小表上顺序扫描更快)
```

把「自然计划没走索引」当成失败，是一条**看起来严格、实际是错的**断言。

---

## 7. 单测为什么不碰真实数据库

`tests/conftest.py` 有一个 autouse fixture 把 `DATABASE_URL` 与 `REDIS_URL` 清空，
让全部单测跑在进程内实现上。理由和出图通道完全相同：

- 单测会**写脏真实数据库**，跑几次之后 `/api/tasks` 的结果就没法看了；
- CI 与别人的机器上没有这两个服务，测试会变成「只在我电脑上绿」。

所以分工是：

| | 覆盖什么 | 谁来跑 |
|---|---|---|
| 单测（219 个） | 领域模型、降级分支、序列化往返、容量与 TTL 语义、**向量复用判定**（纯函数，`tests/test_storage_vectors.py`） | `python -m pytest -q`，离线、秒级 |
| `check_storage.py` | 真实 PostgreSQL / pgvector / Redis 的行为 | 显式执行，需要本机服务 |

「哪些条目要重新向量化」这个判断被刻意抽成纯函数而不是留在 I/O 里：
它是复用逻辑中唯一的判断，也最容易写错，把它留在 `PgVectorIndex` 内部
就等于让它跟着 I/O 一起被排除在单测之外。

### 验证本身也要能被证伪

写测试时容易陷入「一次就全绿 = 没问题」的错觉。所以对最容易漏的那条路径做过一次
**变异检查**：把 `consumeSse` 结尾的 `if (buffer.trim()) handleFrame(buffer)`
注释掉（它负责处理服务端提前关闭、最后一帧没有结尾空行的情况），

```
Test Files  1 failed | 2 passed (3)
     Tests  1 failed | 29 passed (30)
```

恰好只挂掉对应的那一条。去掉断言里真正起作用的代码，测试必须变红 ——
否则它测的只是「代码能被 import」。

真实链路**必须有人显式验证**，而不是假装单测覆盖了它 —— 这跟
`app/eval/harness.py` 对真实出图链路的处理是同一套做法。

---

## 8. 已知边界（没做，以及为什么）

| 项 | 现状 | 说明 |
|---|---|---|
| 多副本自动迁移 | `DB_AUTO_MIGRATE=true` 时每个副本启动都会尝试迁移 | 生产多副本应设为 `false`，改成发布流程里跑一次 `scripts/migrate.py` |
| 向量维度 | 建表时固定 `vector(1024)`，与 `VECTOR_DIM` 绑定 | 改维度需 `migrate.py --recreate-vectors`；迁移脚本本身仍是 1024 |
| 历史数据归档 | 无 | 任务表会一直增长。上量后需要按 `created_at` 分区或定期归档 |
| 轨迹（trace） | 仍在 `var/traces/*.jsonl` 文件里，未入库 | 单条轨迹可达数百 KB，进关系库不如进对象存储；且它已经是「按 task_id 一文件」的可寻址结构 |
| 图片二进制 | 只在 `var/outputs/` 落盘，库里存元信息 | 二进制进 PG 会拖慢备份与查询；真要统一管理应该上对象存储 |
| 会话并发写 | 读-改-写，无分布式锁 | 前端一次只发一轮，实际不会并发；真要并发需要 Redis 锁或 Lua 脚本原子化 |
| 连接池监控 | 只有可用性 0/1 指标 | 没有暴露 pool 的 in-use / overflow 计数，压测前应补上 |

---

## 9. 延伸阅读

- [`../backend/README.md`](../backend/README.md) —— 接口清单与后端架构
- [`MODEL_SELECTION.md`](MODEL_SELECTION.md) —— 模型选型与降级矩阵
- [`HANDOFF.md`](HANDOFF.md) —— 当前进度、剩余任务与工作日志
