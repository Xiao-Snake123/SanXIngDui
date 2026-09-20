/**
 * 开发者页：**本项目真实的运行数据与技术指标**。
 *
 * 这一页原来展示的是一套编造的开源社区内容：虚构的 HuggingFace 产物（`stars: 1847`）、
 * 不存在的接口（`POST /v1/generate`、`Base URL: https://api.sanxingdui.ai`）、
 * 编造的 issue 列表与社区数据（`Stars 5,134`）。对一个会被面试官点开的项目来说，
 * 这不是「演示数据」，而是**可被当场证伪的内容**。
 *
 * 现在这一页只展示可以从当前运行的服务里查出来的东西：
 *   运行数据 ← /api/health（含存储三层档位）+ /api/stats/quality + /api/tasks
 *   接口文档 ← /openapi.json（FastAPI 真实路由，不可能与实现漂移）
 *   模型矩阵 ← /api/models（每个角色的选型理由与降级链）
 *   本地运行 ← 仓库里真实存在的脚本
 *
 * 一条纪律：**拿不到数据时显示拿不到的原因**，不用占位数字把它填满。
 */

import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router";
import { motion, AnimatePresence } from "motion/react";
import {
  Activity,
  Copy,
  Check,
  Code2,
  Cpu,
  Database,
  HardDrive,
  Image as ImageIcon,
  RefreshCw,
  Server,
  Terminal,
} from "lucide-react";
import {
  fetchAgentHealth,
  fetchGraph,
  fetchModelMatrix,
  fetchOpenApiOperations,
  fetchQualityStats,
  fetchTaskDetail,
  fetchTaskList,
  resolveAssetUrl,
  type AgentHealth,
  type GraphInfo,
  type ModelMatrix,
  type OpenApiOperation,
  type QualityStats,
  type TaskDetail,
  type TaskSummary,
} from "@/services/agentApi";

const TEAL = "#45A29E";
const GOLD = "#D4AF37";
const GREEN = "#6BAF8E";
const RED = "#D44545";

const TABS = [
  { id: "runs", label: "运行数据" },
  { id: "api", label: "接口清单" },
  { id: "models", label: "模型矩阵" },
  { id: "local", label: "本地运行" },
];

// ─── 通用小件 ────────────────────────────────────────────────────────────────
function Card({
  title,
  icon,
  right,
  children,
  style,
}: {
  title?: string;
  icon?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div
      style={{
        background: "#111820",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: 12,
        padding: 20,
        ...style,
      }}
    >
      {title && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 14,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {icon}
            <span
              style={{
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 11,
                color: TEAL,
                letterSpacing: 2,
              }}
            >
              {title}
            </span>
          </div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

function Field({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div>
      <p style={{ fontFamily: "monospace", fontSize: 10, color: "#3a4550", marginBottom: 3 }}>
        {label}
      </p>
      <p style={{ fontFamily: "monospace", fontSize: 13, color: "#C5C6C7" }}>{value}</p>
      {hint && (
        <p
          style={{
            fontFamily: "'Noto Sans SC', sans-serif",
            fontSize: 11,
            color: "#556372",
            marginTop: 3,
            lineHeight: 1.6,
          }}
        >
          {hint}
        </p>
      )}
    </div>
  );
}

function Badge({ text, color }: { text: string; color: string }) {
  return (
    <span
      style={{
        background: `${color}15`,
        border: `1px solid ${color}35`,
        borderRadius: 4,
        padding: "2px 8px",
        fontFamily: "monospace",
        fontSize: 10,
        color,
        flexShrink: 0,
      }}
    >
      {text}
    </span>
  );
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 7,
        height: 7,
        borderRadius: "50%",
        background: ok ? GREEN : "#5a3a3a",
        boxShadow: ok ? `0 0 6px ${GREEN}` : "none",
        flexShrink: 0,
      }}
    />
  );
}

function Empty({ text }: { text: string }) {
  return (
    <p
      style={{
        fontFamily: "'Noto Sans SC', sans-serif",
        fontSize: 12,
        color: "#556372",
        padding: "16px 0",
        lineHeight: 1.8,
      }}
    >
      {text}
    </p>
  );
}

/** 三态：还没加载 / 加载失败 / 拿到数据。失败时**不填占位数字**，而是把原因说出来。 */
type Async<T> = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; data: T };

function useAsync<T>(loader: (signal?: AbortSignal) => Promise<T | null>, deps: unknown[]) {
  const [value, setValue] = useState<Async<T>>({ state: "loading" });
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    setValue({ state: "loading" });
    loader(controller.signal).then(
      (data) => {
        if (!alive) return;
        setValue(
          data === null
            ? { state: "error", message: "后端未响应或该接口不可用" }
            : { state: "ready", data },
        );
      },
      (error: unknown) => {
        if (!alive) return;
        setValue({ state: "error", message: (error as Error)?.message ?? "请求失败" });
      },
    );
    return () => {
      alive = false;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { value, reload };
}

// ─── 运行数据 ────────────────────────────────────────────────────────────────
function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
}

function num(value: number | null | undefined, digits = 3, suffix = ""): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(digits)}${suffix}`;
}

function RunsTab() {
  const health = useAsync<AgentHealth>(fetchAgentHealth, []);
  const quality = useAsync<QualityStats>((signal) => fetchQualityStats(30, signal), []);
  const tasks = useAsync((signal) => fetchTaskList({ limit: 15 }, signal), []);
  const graph = useAsync<GraphInfo>(fetchGraph, []);
  const [selected, setSelected] = useState<TaskDetail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);

  const openTask = async (taskId: string) => {
    setDetailBusy(true);
    const detail = await fetchTaskDetail(taskId);
    setDetailBusy(false);
    setSelected(detail);
  };

  const h = health.value.state === "ready" ? health.value.data : null;
  const storage = h?.storage;
  // 先把 ready 分支的数据取出来再往下用：只在 JSX 里靠 `state === "ready"` 判断，
  // 判别联合类型在嵌套闭包里会被 TS 丢掉（这类窄化失败只在编译期暴露，运行期没有症状）
  const qualityData = quality.value.state === "ready" ? quality.value.data : null;
  const overall = qualityData?.overall ?? null;
  const byKind = (qualityData?.by_kind ?? []).filter((row) => row.kind);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: 16,
        }}
      >
        <Card
          title="POSTGRESQL"
          icon={<Database size={13} color={TEAL} />}
          right={<StatusDot ok={!!storage?.database.available} />}
        >
          {health.value.state === "loading" && <Empty text="读取中…" />}
          {health.value.state === "error" && <Empty text={health.value.message} />}
          {storage && (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Field
                label="server"
                value={storage.database.server_version ?? "未连接"}
                hint={`schema ${storage.database.schema_revision ?? "未初始化"}`}
              />
              <Field
                label="pgvector"
                value={storage.database.pgvector_version ?? "未安装"}
                hint={`向量维度 ${storage.vector.dimension}`}
              />
              {storage.database.reason && (
                <p
                  style={{
                    fontFamily: "monospace",
                    fontSize: 11,
                    color: RED,
                    lineHeight: 1.6,
                    wordBreak: "break-all",
                  }}
                >
                  {storage.database.reason}
                </p>
              )}
            </div>
          )}
        </Card>

        <Card
          title="会话存储"
          icon={<Server size={13} color={TEAL} />}
          right={<StatusDot ok={!!storage?.sessions.available} />}
        >
          {storage ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Field
                label="backend"
                value={storage.sessions.backend}
                hint={
                  storage.sessions.available
                    ? "会话可跨进程共享，滚动重启不丢上下文"
                    : "退回进程内存储：单机行为一致，但多副本部署会丢上下文"
                }
              />
              {storage.sessions.reason && (
                <p style={{ fontFamily: "monospace", fontSize: 11, color: "#8A9BAD", wordBreak: "break-all" }}>
                  {storage.sessions.reason}
                </p>
              )}
            </div>
          ) : (
            <Empty text="读取中…" />
          )}
        </Card>

        <Card
          title="向量通道"
          icon={<Cpu size={13} color={TEAL} />}
          right={<StatusDot ok={storage?.vector.backend === "pgvector"} />}
        >
          {h ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Field
                label="backend"
                value={storage?.vector.backend ?? h.retrieval.vector_backend ?? "memory"}
                hint={`embedder ${h.retrieval.embedder}${h.retrieval.embedder_degraded ? "（已降级）" : ""}`}
              />
              <Field
                label="语料向量"
                value={`${h.retrieval.corpus_size} 条`}
                hint={
                  h.retrieval.vector_reused !== undefined
                    ? `本次启动复用 ${h.retrieval.vector_reused} 条 / 重算 ${h.retrieval.vector_embedded ?? 0} 条 —— 复用命中就省掉了 embedding 调用`
                    : undefined
                }
              />
            </div>
          ) : (
            <Empty text="读取中…" />
          )}
        </Card>

        <Card title="编排引擎" icon={<Activity size={13} color={TEAL} />}>
          {h ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Field
                label="engine"
                value={h.engine.engine}
                hint={
                  h.engine.fallback_reason
                    ? `已从 ${h.engine.configured} 降级：${h.engine.fallback_reason}`
                    : `拓扑：planner → supervisor → worker → quality（未达标回炉）`
                }
              />
              <Field
                label="出图链"
                value={Object.entries(h.providers)
                  .filter(([, ok]) => ok === true)
                  .map(([name]) => name)
                  .join(" → ") || "local-placeholder"}
                hint={`模型 ${h.models_configured ? "已配置" : "未配置（LLM/VLM 走规则与本地实现）"}`}
              />
              {graph.value.state === "ready" && (
                <Field
                  label="回炉上限"
                  value={`${graph.value.data.max_revisions} 次 / 阈值 ${graph.value.data.style_threshold}`}
                  hint={`步数安全阀 ${graph.value.data.max_graph_steps}`}
                />
              )}
            </div>
          ) : (
            <Empty text="读取中…" />
          )}
        </Card>
      </div>

      {/* 质量统计：来自 SQL 聚合，跨重启累计 */}
      <Card
        title="质量统计（SQL 聚合 · 近 30 天 · 跨重启累计）"
        icon={<Activity size={13} color={GOLD} />}
        right={
          <button
            onClick={quality.reload}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: "none",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 6,
              padding: "4px 10px",
              color: "#8A9BAD",
              fontFamily: "monospace",
              fontSize: 11,
              cursor: "pointer",
            }}
          >
            <RefreshCw size={11} /> 刷新
          </button>
        }
      >
        {quality.value.state === "loading" && <Empty text="读取中…" />}
        {quality.value.state === "error" && <Empty text={quality.value.message} />}
        {quality.value.state === "ready" && !quality.value.data.available && (
          <Empty
            text={`数据库不可用，无法做 SQL 聚合：${quality.value.data.reason ?? "未配置 DATABASE_URL"}。
                  本页不会用占位数字填满它 —— 进程内计数器在 /api/metrics，但那份数据重启即归零。`}
          />
        )}
        {overall && (
          <div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                gap: 12,
              }}
            >
              {[
                { k: "任务总数", v: String(overall.tasks), hint: `其中跳过质检 ${overall.qa_skipped}` },
                { k: "通过率", v: pct(overall.pass_rate), hint: `分母是判过分的 ${overall.judged} 条` },
                { k: "平均分", v: num(overall.avg_score, 4), hint: "质检总分均值" },
                { k: "平均回炉", v: num(overall.avg_revisions, 2, " 次"), hint: "Self-Correction 次数" },
              ].map((item) => (
                <div
                  key={item.k}
                  style={{ background: "rgba(255,255,255,0.02)", borderRadius: 8, padding: 14 }}
                >
                  <p
                    style={{
                      fontFamily: "'Noto Serif SC', serif",
                      fontSize: 22,
                      fontWeight: 700,
                      color: GOLD,
                      marginBottom: 4,
                    }}
                  >
                    {item.v}
                  </p>
                  <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 11, color: "#8A9BAD" }}>
                    {item.k}
                  </p>
                  <p style={{ fontFamily: "monospace", fontSize: 10, color: "#3a4550", marginTop: 4 }}>
                    {item.hint}
                  </p>
                </div>
              ))}
            </div>
            {byKind.length > 0 && (
              <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 6 }}>
                {byKind.map((row) => (
                  <div
                    key={row.kind}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "100px 1fr 90px 90px 90px",
                      gap: 10,
                      alignItems: "center",
                      fontFamily: "monospace",
                      fontSize: 12,
                      color: "#8A9BAD",
                      padding: "8px 0",
                      borderTop: "1px solid rgba(255,255,255,0.04)",
                    }}
                  >
                    <Badge text={row.kind ?? "-"} color={TEAL} />
                    <span>{row.tasks} 个任务</span>
                    <span>通过 {pct(row.pass_rate)}</span>
                    <span>均分 {num(row.avg_score, 3)}</span>
                    <span>回炉 {num(row.avg_revisions, 2)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Card>

      {/* 历史任务：这是「落库」相对「JSONL trace」唯一有价值的证明 */}
      <Card
        title="历史任务（来自 PostgreSQL）"
        icon={<HardDrive size={13} color={TEAL} />}
        right={
          <button
            onClick={tasks.reload}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: "none",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 6,
              padding: "4px 10px",
              color: "#8A9BAD",
              fontFamily: "monospace",
              fontSize: 11,
              cursor: "pointer",
            }}
          >
            <RefreshCw size={11} /> 刷新
          </button>
        }
      >
        {tasks.value.state === "loading" && <Empty text="读取中…" />}
        {tasks.value.state === "error" && <Empty text={`${tasks.value.message}（任务查询需要 PostgreSQL）`} />}
        {tasks.value.state === "ready" &&
          (tasks.value.data.items.length === 0 ? (
            <Empty text="还没有落库的任务。去「AI 复原」页跑一次，或在命令行执行 python scripts/smoke_test.py。" />
          ) : (
            <div style={{ display: "flex", flexDirection: "column" }}>
              {tasks.value.data.items.map((task: TaskSummary) => (
                <button
                  key={task.task_id}
                  onClick={() => void openTask(task.task_id)}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 90px 110px 80px 80px",
                    gap: 10,
                    alignItems: "center",
                    background: "none",
                    border: "none",
                    borderTop: "1px solid rgba(255,255,255,0.04)",
                    padding: "10px 0",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                    <Badge text={task.kind} color={TEAL} />
                    <span
                      style={{
                        fontFamily: "'Noto Sans SC', sans-serif",
                        fontSize: 13,
                        color: "#C5C6C7",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {task.item || task.goal || task.task_id}
                    </span>
                  </span>
                  <span style={{ fontFamily: "monospace", fontSize: 11, color: "#556372" }}>
                    {task.provider ?? "—"}
                  </span>
                  <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <Badge
                      text={task.qa_skipped ? "skipped" : task.passed ? "passed" : "failed"}
                      color={task.qa_skipped ? "#8A9BAD" : task.passed ? GREEN : RED}
                    />
                  </span>
                  <span style={{ fontFamily: "monospace", fontSize: 12, color: "#8A9BAD" }}>
                    {num(task.score, 3)}
                  </span>
                  <span style={{ fontFamily: "monospace", fontSize: 11, color: "#556372" }}>
                    回炉 {task.revisions}
                  </span>
                </button>
              ))}
              <p
                style={{
                  fontFamily: "monospace",
                  fontSize: 11,
                  color: "#3a4550",
                  marginTop: 12,
                }}
              >
                共 {tasks.value.data.total} 条 · 点击任意一行查看逐轮出图与质检明细
              </p>
            </div>
          ))}
      </Card>

      {/* 逐轮明细 */}
      <AnimatePresence>
        {selected && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
          >
            <Card
              title={`任务明细 · ${selected.task_id}`}
              icon={<ImageIcon size={13} color={GOLD} />}
              right={
                <button
                  onClick={() => setSelected(null)}
                  style={{
                    background: "none",
                    border: "1px solid rgba(255,255,255,0.08)",
                    borderRadius: 6,
                    padding: "4px 10px",
                    color: "#8A9BAD",
                    fontFamily: "monospace",
                    fontSize: 11,
                    cursor: "pointer",
                  }}
                >
                  收起
                </button>
              }
            >
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                {selected.images.map((image) => {
                  const verdict = selected.verdicts.find((v) => v.round_index === image.round_index);
                  return (
                    <div
                      key={image.round_index}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "160px 1fr",
                        gap: 16,
                        background: "rgba(255,255,255,0.02)",
                        borderRadius: 10,
                        padding: 14,
                      }}
                    >
                      <div>
                        {image.image_url ? (
                          <img
                            src={resolveAssetUrl(image.image_url)}
                            alt={`round ${image.round_index}`}
                            style={{ width: "100%", borderRadius: 8, display: "block" }}
                          />
                        ) : (
                          <div
                            style={{
                              height: 120,
                              borderRadius: 8,
                              background: "rgba(0,0,0,0.3)",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              fontFamily: "monospace",
                              fontSize: 11,
                              color: "#3a4550",
                            }}
                          >
                            无图
                          </div>
                        )}
                        <p
                          style={{
                            fontFamily: "monospace",
                            fontSize: 10,
                            color: "#3a4550",
                            marginTop: 8,
                            lineHeight: 1.7,
                          }}
                        >
                          round {image.round_index} · {image.provider}
                          <br />
                          {image.width ?? "?"}×{image.height ?? "?"} · seed {image.seed ?? "—"}
                        </p>
                      </div>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                          {verdict ? (
                            <>
                              <Badge
                                text={verdict.decision ?? "—"}
                                color={
                                  verdict.decision === "accept"
                                    ? GREEN
                                    : verdict.decision === "skipped"
                                      ? "#8A9BAD"
                                      : RED
                                }
                              />
                              <span style={{ fontFamily: "monospace", fontSize: 12, color: "#8A9BAD" }}>
                                {num(verdict.score, 4)} / 阈值 {num(verdict.threshold, 2)}
                              </span>
                            </>
                          ) : (
                            <span style={{ fontFamily: "monospace", fontSize: 12, color: "#556372" }}>
                              该轮无质检记录
                            </span>
                          )}
                        </div>
                        {!!verdict?.feedback?.length && (
                          <p
                            style={{
                              fontFamily: "'Noto Sans SC', sans-serif",
                              fontSize: 12,
                              color: "#C5C6C7",
                              lineHeight: 1.8,
                              marginBottom: 8,
                            }}
                          >
                            <span style={{ color: GOLD }}>修改指令：</span>
                            {verdict.feedback.join("；")}
                          </p>
                        )}
                        <p
                          style={{
                            fontFamily: "monospace",
                            fontSize: 11,
                            color: "#556372",
                            lineHeight: 1.7,
                            maxHeight: 96,
                            overflow: "hidden",
                          }}
                        >
                          {image.prompt?.slice(0, 280) ?? "（无提示词记录）"}
                          {(image.prompt?.length ?? 0) > 280 ? " …" : ""}
                        </p>
                      </div>
                    </div>
                  );
                })}
                {selected.error && (
                  <p style={{ fontFamily: "monospace", fontSize: 11, color: RED }}>{selected.error}</p>
                )}
              </div>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>

      {detailBusy && (
        <p style={{ fontFamily: "monospace", fontSize: 11, color: "#3a4550" }}>正在读取任务明细…</p>
      )}
    </div>
  );
}

// ─── 接口清单（直接读 FastAPI 的 OpenAPI）────────────────────────────────────
function ApiTab() {
  const operations = useAsync<OpenApiOperation[]>(fetchOpenApiOperations, []);
  const [copied, setCopied] = useState<string | null>(null);

  const copy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 1600);
  };

  if (operations.value.state === "loading") return <Empty text="读取接口清单中…" />;
  if (operations.value.state === "error")
    return <Empty text={`读不到 OpenAPI 文档：${operations.value.message}`} />;

  const groups = new Map<string, OpenApiOperation[]>();
  for (const operation of operations.value.data) {
    const tag = operation.tags[0] ?? "default";
    groups.set(tag, [...(groups.get(tag) ?? []), operation]);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <Card title="这份清单从哪来" icon={<Code2 size={13} color={TEAL} />}>
        <p
          style={{
            fontFamily: "'Noto Sans SC', sans-serif",
            fontSize: 12.5,
            color: "#8A9BAD",
            lineHeight: 1.9,
          }}
        >
          它由前端直接读取后端的 <code style={{ color: TEAL }}>/openapi.json</code> 渲染 ——
          FastAPI 把真实路由暴露成机器可读的规范，所以
          <span style={{ color: "#C5C6C7" }}>「文档里写着的接口，一定是真的存在的」</span>。
          手写一份接口清单迟早会漂移，而漂移的接口文档比没有文档更糟。
          当前共 <span style={{ color: GOLD }}>{operations.value.data.length}</span> 个操作。
        </p>
      </Card>

      {[...groups.entries()].map(([tag, list]) => (
        <Card key={tag} title={tag.toUpperCase()} icon={<Server size={13} color={TEAL} />}>
          <div style={{ display: "flex", flexDirection: "column" }}>
            {list.map((operation) => {
              const color =
                operation.method === "GET" ? GREEN : operation.method === "POST" ? TEAL : GOLD;
              return (
                <div
                  key={`${operation.method}-${operation.path}`}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "60px 300px 1fr auto",
                    gap: 12,
                    alignItems: "center",
                    padding: "10px 0",
                    borderTop: "1px solid rgba(255,255,255,0.04)",
                  }}
                  className="max-md:block"
                >
                  <Badge text={operation.method} color={color} />
                  <code style={{ fontFamily: "monospace", fontSize: 12.5, color: "#8A9BAD" }}>
                    {operation.path}
                  </code>
                  <span
                    style={{
                      fontFamily: "'Noto Sans SC', sans-serif",
                      fontSize: 12,
                      color: "#556372",
                    }}
                  >
                    {operation.summary}
                  </span>
                  <button
                    onClick={() =>
                      copy(
                        `curl -s ${operation.method === "GET" ? "" : "-X POST "}http://127.0.0.1:8123${operation.path}`,
                        `${operation.method}-${operation.path}`,
                      )
                    }
                    style={{
                      background: "none",
                      border: "1px solid rgba(255,255,255,0.06)",
                      borderRadius: 5,
                      padding: "4px 8px",
                      color: "#556372",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: 4,
                      fontFamily: "monospace",
                      fontSize: 10,
                    }}
                  >
                    {copied === `${operation.method}-${operation.path}` ? (
                      <>
                        <Check size={10} /> 已复制
                      </>
                    ) : (
                      <>
                        <Copy size={10} /> curl
                      </>
                    )}
                  </button>
                </div>
              );
            })}
          </div>
        </Card>
      ))}

      <Card title="在线文档">
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12.5, color: "#8A9BAD", lineHeight: 1.9 }}>
          后端默认监听 <code style={{ color: TEAL }}>http://127.0.0.1:8123</code>，
          交互式文档在 <code style={{ color: TEAL }}>/docs</code>（Swagger UI），
          规范原文在 <code style={{ color: TEAL }}>/openapi.json</code>。
          如用 <code style={{ color: TEAL }}>-BackendPort</code> 改过端口，以实际启动的端口为准。
        </p>
      </Card>
    </div>
  );
}

// ─── 模型矩阵 ────────────────────────────────────────────────────────────────
function ModelsTab() {
  const matrix = useAsync<ModelMatrix>(fetchModelMatrix, []);
  if (matrix.value.state === "loading") return <Empty text="读取模型矩阵中…" />;
  if (matrix.value.state === "error") return <Empty text={`读不到模型矩阵：${matrix.value.message}`} />;

  const data = matrix.value.data;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card title="出图链" icon={<ImageIcon size={13} color={GOLD} />}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
          <Field label="主模型" value={data.image_generation.primary} />
          <Field
            label="降级链"
            value={data.image_generation.fallback}
            hint="配置 Key 后生产环境建议关掉免鉴权通道"
          />
          <Field
            label="提示词增强"
            value={data.image_generation.prompt_extend ? "开启" : "关闭"}
            hint={
              data.image_generation.prompt_extend
                ? "开启等于允许模型改写用户选定的提示词"
                : "用户选定的提示词逐字下发，不会被模型改写"
            }
          />
          <Field label="水印" value={data.image_generation.watermark ? "开启" : "关闭"} />
        </div>
      </Card>

      {data.roles.map((role) => (
        <Card
          key={role.role}
          title={role.title.toUpperCase()}
          icon={<Cpu size={13} color={TEAL} />}
          right={<Badge text={role.modality} color={TEAL} />}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <code style={{ fontFamily: "monospace", fontSize: 14, color: GOLD }}>{role.primary}</code>
              {role.fallbacks.length > 0 && (
                <span style={{ fontFamily: "monospace", fontSize: 11, color: "#556372" }}>
                  → 降级 {role.fallbacks.join(" → ")}
                </span>
              )}
            </div>
            <p
              style={{
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 12.5,
                color: "#8A9BAD",
                lineHeight: 1.9,
              }}
            >
              {role.rationale}
            </p>
            <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
              <span style={{ fontFamily: "monospace", fontSize: 11, color: "#556372" }}>
                成本 {role.cost_hint}
              </span>
              <span style={{ fontFamily: "monospace", fontSize: 11, color: "#556372" }}>
                延迟 {role.latency_hint}
              </span>
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {role.tags.map((tag) => (
                <Badge key={tag} text={tag} color="#8A9BAD" />
              ))}
            </div>
          </div>
        </Card>
      ))}

      <Card title="本地微调（可选）" icon={<Cpu size={13} color={TEAL} />}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
          <Field label="基座" value={data.local_finetune.base} />
          <Field label="适配器" value={data.local_finetune.adapter ?? "未配置"} />
          <Field
            label="状态"
            value={data.local_finetune.enabled ? "启用" : "未启用"}
            hint="需要 GPU；未启用时参考图理解走 API 通道"
          />
          <Field
            label="API Key"
            value={data.configured ? "已配置" : "未配置"}
            hint={data.configured ? undefined : "未配置时 LLM/VLM/Embedding 全部降级为规则与本地实现"}
          />
        </div>
      </Card>
    </div>
  );
}

// ─── 本地运行 ────────────────────────────────────────────────────────────────
const LOCAL_COMMANDS: Array<{ group: string; items: Array<{ comment: string; cmd: string; desc: string }> }> = [
  {
    group: "启动",
    items: [
      {
        comment: "# 安装依赖",
        cmd: "pip install -r requirements.txt",
        desc: "FastAPI / LangGraph / SQLAlchemy / asyncpg / pgvector / redis",
      },
      {
        comment: "# 启动后端（在 backend/ 下）",
        cmd: "python -m uvicorn app.main:app --host 127.0.0.1 --port 8123",
        desc: "不配任何 Key 也能启动：LLM/出图/检索会自动降级并如实标注",
      },
      {
        comment: "# 启动前端（在 frontend/ 下）",
        cmd: "npm run dev",
        desc: "开发服把 /agent 代理到后端；生产用 npm run build && npm start",
      },
    ],
  },
  {
    group: "存储（可选，不配也能跑）",
    items: [
      {
        comment: "# 本机初始化 PostgreSQL + pgvector",
        cmd: "python scripts/setup_postgres.py",
        desc: "免安装发行版 + 预编译 pgvector，幂等；已有 PostgreSQL 时只建库建角色",
      },
      { comment: "# 建表与向量索引", cmd: "python scripts/migrate.py", desc: "Alembic 迁移到最新；--recreate-vectors 可按维度重建向量表" },
      { comment: "# 验证真实存储链路", cmd: "python scripts/check_storage.py", desc: "38 项断言：落库、幂等、pgvector 与进程内数值一致性、Redis 跨实例读回、外键 CASCADE" },
    ],
  },
  {
    group: "自检与诊断",
    items: [
      { comment: "# 离线端到端冒烟", cmd: "python scripts/smoke_test.py", desc: "检索 + 跑完一次完整复原任务，不依赖任何 Key（落到占位图）" },
      { comment: "# 全量单测", cmd: "python -m pytest -q", desc: "219 个用例，离线、秒级；conftest 会把出图通道与数据库都关掉" },
      { comment: "# 回炉是否真的改变了画面", cmd: "python scripts/check_revision_effect.py", desc: "比像素不比字节；两轮图像逐像素相同即判失败" },
      { comment: "# 出图通道的有效窗口", cmd: "python scripts/probe_freeimage_tail.py short 3 head2", desc: "量出上游能吃多长的提示词，以及差异放在头部/尾部是否生效" },
    ],
  },
];

function LocalTab() {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 1600);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {LOCAL_COMMANDS.map((group) => (
        <Card key={group.group} title={group.group} icon={<Terminal size={13} color={TEAL} />}>
          <div style={{ display: "flex", flexDirection: "column" }}>
            {group.items.map((item) => (
              <div
                key={item.cmd}
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr auto",
                  gap: 12,
                  alignItems: "center",
                  padding: "12px 0",
                  borderTop: "1px solid rgba(255,255,255,0.04)",
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <p style={{ fontFamily: "monospace", fontSize: 11, color: "#3a4550", marginBottom: 4 }}>
                    {item.comment}
                  </p>
                  <code
                    style={{
                      fontFamily: "monospace",
                      fontSize: 12.5,
                      color: TEAL,
                      wordBreak: "break-all",
                    }}
                  >
                    $ {item.cmd}
                  </code>
                  <p
                    style={{
                      fontFamily: "'Noto Sans SC', sans-serif",
                      fontSize: 11.5,
                      color: "#556372",
                      marginTop: 6,
                      lineHeight: 1.7,
                    }}
                  >
                    {item.desc}
                  </p>
                </div>
                <button
                  onClick={() => copy(item.cmd, item.cmd)}
                  style={{
                    background: "none",
                    border: "1px solid rgba(255,255,255,0.06)",
                    borderRadius: 5,
                    padding: "5px 9px",
                    color: "#556372",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                    fontFamily: "monospace",
                    fontSize: 10,
                  }}
                >
                  {copied === item.cmd ? (
                    <>
                      <Check size={10} /> 已复制
                    </>
                  ) : (
                    <>
                      <Copy size={10} /> 复制
                    </>
                  )}
                </button>
              </div>
            ))}
          </div>
        </Card>
      ))}

      <Card title="关于「零配置也能跑」">
        <p style={{ fontFamily: "'Noto Sans SC', sans-serif", fontSize: 12.5, color: "#8A9BAD", lineHeight: 1.95 }}>
          这个项目的每一层外部依赖都有明确的降级路径，并且会把降级事实
          <span style={{ color: "#C5C6C7" }}>如实标注</span>，而不是静默返回次品：
          没有模型 Key 时对话走规则与模板、检索走 BM25 + 本地哈希向量、出图落到免鉴权通道；
          没有数据库时任务不落库、向量走进程内检索；没有 Redis 时会话退回进程内。
          健康检查 <code style={{ color: TEAL }}>/api/health</code> 会告诉你当前实际在用哪一档，
          以及连不上时的原始报错。
        </p>
      </Card>
    </div>
  );
}

// ─── MAIN PAGE ───────────────────────────────────────────────────────────────
export function DeveloperPage() {
  const { tab } = useParams<{ tab: string }>();
  const navigate = useNavigate();
  const activeTab = TABS.some((t) => t.id === tab) ? (tab as string) : "runs";

  return (
    <div style={{ paddingTop: 64, minHeight: "100vh" }}>
      <div
        style={{
          background: "linear-gradient(180deg, rgba(31,40,51,0.5) 0%, transparent 100%)",
          borderBottom: "1px solid rgba(69,162,158,0.08)",
          padding: "40px 0 0",
        }}
      >
        <div className="max-w-7xl mx-auto px-6">
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
            <p
              style={{
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 11,
                color: TEAL,
                letterSpacing: 5,
                marginBottom: 8,
              }}
            >
              RUNTIME &amp; ENGINEERING / 运行数据
            </p>
            <h1
              style={{
                fontFamily: "'Noto Serif SC', serif",
                fontSize: "clamp(24px,4vw,44px)",
                fontWeight: 900,
                color: "#EFEFEF",
                marginBottom: 4,
              }}
            >
              这个系统<span style={{ color: TEAL }}>正在怎么跑</span>
            </h1>
            <p
              style={{
                fontFamily: "'Noto Sans SC', sans-serif",
                fontSize: 14,
                color: "#8A9BAD",
                marginTop: 8,
                maxWidth: 720,
                lineHeight: 1.9,
              }}
            >
              本页所有数字都来自当前运行的服务：引擎与出图链、存储三层（PostgreSQL / pgvector / Redis）
              的实际档位、按 SQL 聚合的质检指标、以及落库的历史任务与逐轮明细。
              拿不到数据时就显示拿不到的原因，不填占位数字。
            </p>
          </motion.div>
          <div
            style={{
              display: "flex",
              gap: 0,
              marginTop: 32,
              borderBottom: "1px solid rgba(255,255,255,0.06)",
              overflowX: "auto",
            }}
          >
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => navigate(`/developer/${t.id}`)}
                style={{
                  padding: "12px 24px",
                  background: "none",
                  border: "none",
                  borderBottom: activeTab === t.id ? `2px solid ${TEAL}` : "2px solid transparent",
                  color: activeTab === t.id ? TEAL : "#8A9BAD",
                  fontFamily: "'Noto Sans SC', sans-serif",
                  fontSize: 14,
                  cursor: "pointer",
                  transition: "color 0.2s",
                  whiteSpace: "nowrap",
                  marginBottom: -1,
                }}
                onMouseEnter={(e) => {
                  if (activeTab !== t.id) (e.currentTarget as HTMLElement).style.color = "#C5C6C7";
                }}
                onMouseLeave={(e) => {
                  if (activeTab !== t.id) (e.currentTarget as HTMLElement).style.color = "#8A9BAD";
                }}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="max-w-7xl mx-auto px-6" style={{ paddingTop: 40, paddingBottom: 80 }}>
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.25 }}
          >
            {activeTab === "runs" && <RunsTab />}
            {activeTab === "api" && <ApiTab />}
            {activeTab === "models" && <ModelsTab />}
            {activeTab === "local" && <LocalTab />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}
