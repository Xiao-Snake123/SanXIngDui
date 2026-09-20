import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  streamRestoration,
  type AgentNode,
  type NodeEvent,
  type RestorationResult,
  type RestoreRequest,
  type TraceEvent,
} from "@/services/agentApi";

export type StepStatus = "running" | "done" | "failed";

export interface AgentStep {
  id: string;
  node: AgentNode;
  label: string;
  status: StepStatus;
  round: number;
  startedAt: number;
  durationMs?: number;
  summary?: string;
  tone: "info" | "ok" | "warn" | "error";
}

export interface QaRecord {
  round: number;
  /** 质检被跳过（占位图不可判定）时为 null —— 不能用 0 冒充，那是伪造结论 */
  score: number | null;
  objectiveScore: number | null;
  judgeScore: number | null;
  threshold: number | null;
  passed: boolean;
  skipped: boolean;
  decision: string;
  reason: string;
  feedback: string[];
  anachronisms: string[];
  degraded: boolean;
  /**
   * 被一票否决压下来**之前**的融合分，以及压制原因。
   *
   * 少了这两个字段就无法回答「0.40 到底是封顶还是真差」——
   * 实测有一轮融合分 0.745（已经越过 0.72 阈值），只因 VLM 报了时代错配
   * 被封顶到 0.40。把它当成「画得很差」会让人去调提示词或换模型，
   * 而真正该做的是修那一处时代错配。
   */
  rawScore: number | null;
  cappedBy: string;
}

/**
 * 某一轮出图的产物。
 *
 * 回炉每重画一次就是一张新图，只看最终那张会丢掉「这一轮改成了什么样」——
 * 而那正是判断「回炉有没有用」的唯一依据。
 */
export interface RoundImage {
  /** 与 `QaRecord.round` 对齐，用来把图和该轮的质检结论配对 */
  round: number;
  url: string;
  provider: string;
  degraded: boolean;
  latencyMs: number | null;
  seed: number | null;
}

export interface AgentRunState {
  running: boolean;
  progress: number;
  steps: AgentStep[];
  qaHistory: QaRecord[];
  /** 每一轮的出图，按轮次升序。跑的时候就逐轮追加，完成时以服务端历史校准。 */
  roundImages: RoundImage[];
  warnings: string[];
  copyStream: string;
  result: RestorationResult | null;
  error: string | null;
  /**
   * 用户主动叫停（既不是失败，也不是跑完）。
   *
   * 与 `error` 分开：停止是**用户的意图**，不该被渲染成一次事故；
   * 而且叫停时已经产出的东西（各轮图、史料、已完成的步骤）全部保留。
   */
  stopped: boolean;
  taskId: string | null;
  engine: string | null;
  elapsedMs: number;
}

const NODE_LABELS: Record<string, string> = {
  planner: "规划 Agent",
  supervisor: "调度中枢",
  retrieval: "史料检索 Worker",
  restoration: "图像修复 Worker",
  quality: "质检 Agent",
  copywriting: "科普文案 Worker",
  finalize: "结果汇总",
};

/** 只展示这三个字段不会被长度问题影响；其余细节放折叠区。 */
const EMPTY: AgentRunState = {
  running: false,
  progress: 0,
  steps: [],
  qaHistory: [],
  roundImages: [],
  warnings: [],
  copyStream: "",
  result: null,
  error: null,
  stopped: false,
  taskId: null,
  engine: null,
  elapsedMs: 0,
};

/**
 * 节点结束摘要优先用服务端下发的 `summary`（口径由后端统一定义，见 app/graph/nodes.py），
 * 仅在旧版本后端未提供时，才用前端本地兜底。
 */
function summarizeNodeEnd(node: string, payload: Record<string, unknown>): string | undefined {
  const provided = payload.summary;
  if (typeof provided === "string" && provided.trim()) return provided;

  switch (node) {
    case "planner":
      return payload.goal ? `目标：${String(payload.goal).slice(0, 42)}` : undefined;
    case "retrieval":
      return `命中 ${payload.hits ?? 0} 条史料`;
    case "quality":
      return `得分 ${Number(payload.score ?? 0).toFixed(2)}`;
    default:
      return undefined;
  }
}

/**
 * 完成时用服务端的 `revision_history` 校准各轮图。
 *
 * `image_ready` 事件负责「跑的过程中就能逐轮看到」，但它在重连、提前断开、
 * 或订阅晚于首轮时会缺轮。`revision_history` 是后端在每轮出图后累积的完整历史，
 * 所以完成时以它为准；实时事件里独有的字段（耗时）则保留下来不被覆盖成 null。
 */
function mergeRoundImages(
  lived: RoundImage[],
  result: RestorationResult | null,
): RoundImage[] {
  const history = result?.revision_history ?? [];
  if (history.length === 0) return lived;

  const byRound = new Map<number, RoundImage>();
  for (const item of lived) byRound.set(item.round, item);
  for (const item of history) {
    const existing = byRound.get(item.round_index);
    byRound.set(item.round_index, {
      round: item.round_index,
      url: item.image_url,
      provider: item.provider,
      degraded: item.degraded,
      // 这两项只有实时事件里有，别把已有值覆盖掉
      latencyMs: existing?.latencyMs ?? null,
      seed: item.seed ?? existing?.seed ?? null,
    });
  }
  return [...byRound.values()]
    .filter((item) => Boolean(item.url))
    .sort((a, b) => a.round - b.round);
}

/**
 * 驱动一次多智能体复原，并把 SSE 事件转成可渲染的 Agent 时间线。
 *
 * 设计要点：所有状态变更都由服务端事件驱动，组件不做任何「假装在跑」的定时器。
 * 如果后端不可达，界面会明确显示连接失败，而不是走完一个假进度条再给出一张占位图。
 */
export function useAgentRestoration() {
  const [state, setState] = useState<AgentRunState>(EMPTY);
  const abortRef = useRef<AbortController | null>(null);
  const startedAtRef = useRef<number>(0);
  const restorationRoundsRef = useRef<number>(0);
  const patchRef = useRef<Record<string, unknown>>({});

  // 卸载时中断。同时把 ref 置空：`run()` 的 catch 靠它判断「这一轮还算不算数」，
  // 置空之后那次中断不会再 setState 到已卸载的组件上。
  useEffect(
    () => () => {
      abortRef.current?.abort();
      abortRef.current = null;
    },
    [],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState(EMPTY);
  }, []);

  /**
   * 用户主动停止。
   *
   * 与 `reset()` 的区别是**保留已经跑出来的东西**：已生成的各轮图、命中的史料、
   * 已完成的步骤。最常见的场景是「图已经出来了、还在回炉」——那时用户往往
   * 只是想叫停，而不是把结果一起丢掉。
   */
  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState((prev) => (prev.running ? { ...prev, running: false, stopped: true } : prev));
  }, []);

  const handleTrace = useCallback((event: TraceEvent) => {
    setState((prev) => {
      switch (event.kind) {
        case "node_start": {
          const round = event.node === "restoration" ? restorationRoundsRef.current : 0;
          const id = `${event.node}#${event.seq}`;
          if (event.node === "restoration") restorationRoundsRef.current += 1;
          return {
            ...prev,
            steps: [
              ...prev.steps.map((step) => (step.status === "running" ? { ...step, status: "done" as StepStatus } : step)),
              {
                id,
                node: event.node as AgentNode,
                label: NODE_LABELS[event.node] ?? event.node,
                status: "running",
                round,
                startedAt: Date.now(),
                tone: "info",
              },
            ],
          };
        }
        case "node_end": {
          const summary = summarizeNodeEnd(event.node, event.payload ?? {});
          let patched = false;
          const steps = [...prev.steps];
          for (let index = steps.length - 1; index >= 0; index -= 1) {
            if (steps[index].node === event.node && steps[index].status === "running") {
              steps[index] = {
                ...steps[index],
                status: "done",
                durationMs: event.duration_ms ?? undefined,
                summary,
              };
              patched = true;
              break;
            }
          }
          return patched ? { ...prev, steps } : prev;
        }
        case "error": {
          const message = String(event.payload?.message ?? "节点执行失败");
          return {
            ...prev,
            warnings: [...prev.warnings, `${NODE_LABELS[event.node] ?? event.node}：${message}`],
            steps: prev.steps.map((step) =>
              step.node === event.node && step.status === "running"
                ? { ...step, status: "failed" as StepStatus, tone: "error" as const, summary: message }
                : step,
            ),
          };
        }
        case "degraded": {
          const fallback = String(event.payload?.fallback ?? "");
          const reason = String(event.payload?.reason ?? "");
          return {
            ...prev,
            warnings: [...prev.warnings, `${event.node} 降级 → ${fallback}（${reason.slice(0, 60)}）`],
          };
        }
        case "qa_verdict": {
          const payload = event.payload ?? {};
          const optional = (value: unknown): number | null =>
            value === null || value === undefined || value === "" ? null : Number(value);
          const record: QaRecord = {
            round: Number(payload.round_index ?? 0),
            score: optional(payload.score),
            objectiveScore: optional(payload.objective_score),
            judgeScore: optional(payload.judge_score),
            threshold: optional(payload.threshold),
            passed: Boolean(payload.passed),
            skipped: Boolean(payload.skipped) || payload.decision === "skipped",
            decision: String(payload.decision ?? ""),
            reason: String(payload.reason ?? ""),
            feedback: (payload.feedback as string[]) ?? [],
            anachronisms: (payload.anachronisms as string[]) ?? [],
            degraded: Boolean(payload.degraded),
            rawScore: optional(payload.raw_score),
            cappedBy: String(payload.capped_by ?? ""),
          };
          if (prev.steps.length === 0) return { ...prev, qaHistory: [...prev.qaHistory, record] };

          const steps = [...prev.steps];
          for (let index = steps.length - 1; index >= 0; index -= 1) {
            if (steps[index].node === "quality") {
              // 跳过质检既不是通过也不是不达标，用 info 语气避免误导
              const tone: AgentStep["tone"] = record.skipped
                ? "info"
                : record.passed
                  ? "ok"
                  : "warn";
              steps[index] = { ...steps[index], tone };
              break;
            }
          }
          return { ...prev, qaHistory: [...prev.qaHistory, record], steps };
        }
        case "image_ready": {
          const payload = event.payload ?? {};
          const url = String(payload.image_url ?? "");
          // 没有 URL 的事件不记 —— 记一条「有轮次但没图」的空条目，
          // 界面上就是一个破图占位，比不显示更糟。
          if (!url) return prev;
          const round = Number(payload.round_index ?? 0);
          const entry: RoundImage = {
            round,
            url,
            provider: String(payload.provider ?? ""),
            degraded: Boolean(payload.degraded),
            latencyMs: payload.latency_ms == null ? null : Number(payload.latency_ms),
            seed: payload.seed == null ? null : Number(payload.seed),
          };
          // 同一轮重复到达时覆盖而非追加：事件重放/重连会让同一轮来两次，
          // 追加会渲染出两张一模一样的「第 2 轮」。
          const others = prev.roundImages.filter((item) => item.round !== round);
          return {
            ...prev,
            roundImages: [...others, entry].sort((a, b) => a.round - b.round),
          };
        }
        case "copy_delta": {
          return { ...prev, copyStream: prev.copyStream + String(event.payload?.delta ?? "") };
        }
        default:
          return prev;
      }
    });
  }, []);

  const handleNode = useCallback((event: NodeEvent) => {
    patchRef.current = { ...patchRef.current, [event.node]: event.patch };
    setState((prev) => ({
      ...prev,
      progress: Math.max(prev.progress, event.progress),
      elapsedMs: event.elapsed_ms,
    }));
  }, []);

  const run = useCallback(
    async (request: RestoreRequest) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      restorationRoundsRef.current = 0;
      patchRef.current = {};
      startedAtRef.current = Date.now();

      setState({ ...EMPTY, running: true });

      try {
        await streamRestoration(
          request,
          {
            onStart: (event) =>
              setState((prev) => ({
                ...prev,
                taskId: event.task_id,
                engine: event.engine,
                running: true,
              })),
            onNode: handleNode,
            onTrace: handleTrace,
            onDone: (event) =>
              setState((prev) => ({
                ...prev,
                running: false,
                progress: 100,
                result: event.result,
                roundImages: mergeRoundImages(prev.roundImages, event.result),
                copyStream: event.result.copy?.text || prev.copyStream,
                elapsedMs: event.result.duration_ms ?? prev.elapsedMs,
                warnings: prev.warnings,
              })),
            onError: (message) =>
              setState((prev) => ({ ...prev, running: false, error: message })),
          },
          controller.signal,
        );
      } catch (error) {
        // 这一轮已经不算数了（被用户停止、或被新一轮取代）就别再动状态。
        // 否则被取消的那一轮会在微任务里回头把新一轮的 `running` 覆盖成 false ——
        // 表现为「点了生成，跑一下就自己停了」。
        if (abortRef.current !== controller) return;

        if ((error as Error).name === "AbortError") {
          // 停止是用户意图，不是故障：走 `stopped` 而不是 `error`，
          // 已产出的东西一律保留。原先这里直接忽略 AbortError，
          // 于是 `running` 永远停在 true —— 转圈永远不停，用户也不知道停没停成功。
          setState((prev) => ({ ...prev, running: false, stopped: true }));
          return;
        }
        setState((prev) => ({ ...prev, running: false, error: (error as Error).message }));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [handleNode, handleTrace],
  );

  const derived = useMemo(
    () => ({
      imageUrl: state.result?.image_url ?? null,
      qa: state.result?.qa ?? null,
      copy: state.result?.copy ?? null,
      evidence: state.result?.evidence ?? [],
      lastQa: state.qaHistory.length ? state.qaHistory[state.qaHistory.length - 1] : null,
      revisionCount: state.qaHistory.filter((record) => record.decision === "revise").length,
    }),
    [state.result, state.qaHistory],
  );

  return { ...state, ...derived, run, reset, stop };
}
