/**
 * 「每一轮出图都要留下来」的回归测试。
 *
 * 为什么这件事重要：回炉每重画一次就是一张新图，只看最终那张，
 * 用户无法判断回炉到底有没有用。实测出现过连出四轮、分数纹丝不动
 * （都封顶在 0.40）的情况 —— 如果界面上只有最终一张，这个过程完全不可见。
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/agentApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/services/agentApi")>("@/services/agentApi");
  return { ...actual, streamRestoration: vi.fn() };
});

import {
  streamRestoration,
  type DoneEvent,
  type RestoreRequest,
  type StreamHandlers,
  type TraceEvent,
} from "@/services/agentApi";
import { useAgentRestoration } from "./useAgentRestoration";

const mocked = vi.mocked(streamRestoration);

function imageEvent(round: number, payload: Record<string, unknown> = {}): TraceEvent {
  return {
    seq: round + 1,
    task_id: "t1",
    kind: "image_ready",
    node: "restoration",
    ts: "",
    payload: {
      round_index: round,
      image_url: `/media/r${round}.png`,
      provider: "dashscope",
      degraded: false,
      latency_ms: 100,
      seed: 7,
      ...payload,
    },
  };
}

function doneEvent(result: Record<string, unknown>): DoneEvent {
  // 测试只关心 `revision_history`，不必造出 `RestorationResult` 的全部 16 个字段。
  // 先过 `unknown` 是刻意的：直接 `as` 会被 tsc 判为「两个类型不重叠」。
  return { type: "done", task_id: "t1", result: result as unknown as DoneEvent["result"] };
}

/** 让 mock 的 streamRestoration 按脚本驱动一次运行。 */
function script(events: TraceEvent[], done?: DoneEvent): void {
  mocked.mockImplementation(async (_request: RestoreRequest, handlers: StreamHandlers) => {
    for (const event of events) handlers.onTrace?.(event);
    if (done) handlers.onDone?.(done);
  });
}

async function runOnce(...args: Parameters<typeof script>): Promise<ReturnType<typeof renderHook<ReturnType<typeof useAgentRestoration>, unknown>>> {
  script(...args);
  const view = renderHook(() => useAgentRestoration());
  await act(async () => {
    await view.result.current.run({ kind: "scene" });
  });
  return view;
}

describe("各轮出图的收集", () => {
  afterEach(() => {
    vi.clearAllMocks();
  })

  it("每一轮都留下一条记录，并按轮次升序", async () => {
    // 故意乱序送达：事件顺序不该决定展示顺序
    const view = await runOnce([imageEvent(1), imageEvent(0)]);

    expect(view.result.current.roundImages.map((item) => item.round)).toEqual([0, 1]);
    expect(view.result.current.roundImages[0].url).toBe("/media/r0.png");
  });

  it("同一轮重复到达时覆盖，而不是追加", async () => {
    // 重连或事件重放会让同一轮来两次；追加会渲染出两张一模一样的「第 1 轮」
    const view = await runOnce([
      imageEvent(0),
      imageEvent(0, { image_url: "/media/r0-again.png" }),
    ]);

    expect(view.result.current.roundImages).toHaveLength(1);
    expect(view.result.current.roundImages[0].url).toBe("/media/r0-again.png");
  });

  it("没有图片地址的事件不产生条目", async () => {
    // 记一条「有轮次但没图」的空条目，界面上就是一个破图占位 —— 比不显示更糟
    const view = await runOnce([imageEvent(0, { image_url: "" })]);

    expect(view.result.current.roundImages).toHaveLength(0);
  });

  it("完成时用服务端历史补齐事件里缺的轮", async () => {
    // `image_ready` 在重连/提前断开时会缺轮，`revision_history` 才是完整那份
    const view = await runOnce(
      [imageEvent(0)],
      doneEvent({
        revision_history: [
          {
            round_index: 0,
            image_url: "/media/r0.png",
            provider: "dashscope",
            seed: 1,
            prompt: "",
            degraded: false,
          },
          {
            round_index: 1,
            image_url: "/media/r1.png",
            provider: "local-placeholder",
            seed: null,
            prompt: "",
            degraded: true,
          },
        ],
      }),
    );

    expect(view.result.current.roundImages.map((item) => item.round)).toEqual([0, 1]);
    expect(view.result.current.roundImages[1].provider).toBe("local-placeholder");
    expect(view.result.current.roundImages[1].degraded).toBe(true);
  });

  it("校准不会把实时事件里独有的耗时抹成 null", async () => {
    const view = await runOnce(
      [imageEvent(0)],
      doneEvent({
        revision_history: [
          {
            round_index: 0,
            image_url: "/media/r0.png",
            provider: "dashscope",
            seed: 3,
            prompt: "",
            degraded: false,
          },
        ],
      }),
    );

    // 历史里没有 latency_ms，覆盖式重建会让它变成 null，画廊上就少了一行信息
    expect(view.result.current.roundImages[0].latencyMs).toBe(100);
  });

  it("reset 之后不留上一轮的图", async () => {
    const view = await runOnce([imageEvent(0), imageEvent(1)]);
    expect(view.result.current.roundImages).toHaveLength(2);

    act(() => view.result.current.reset());
    expect(view.result.current.roundImages).toHaveLength(0);
  });
});

/** 让 streamRestoration 先推事件，然后挂着不返回，直到 signal 被 abort。 */
function hangUntilAborted(events: TraceEvent[]): void {
  mocked.mockImplementation(
    async (_request: RestoreRequest, handlers: StreamHandlers, signal?: AbortSignal) => {
      for (const event of events) handlers.onTrace?.(event);
      await new Promise<void>((_resolve, reject) => {
        signal?.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
      });
    },
  );
}

describe("运行期间可以停止", () => {
  afterEach(() => {
    vi.clearAllMocks();
  })

  it("停止后 running 归位、标记 stopped，且保留已产出的图", async () => {
    // 这是原先最要命的一处：`run()` 的 catch 直接忽略了 AbortError，
    // 于是 `running` 永远停在 true —— 转圈永远不停，用户也不知道停没停成功。
    hangUntilAborted([imageEvent(0)]);
    const view = renderHook(() => useAgentRestoration());

    let running: Promise<void> = Promise.resolve();
    act(() => {
      running = view.result.current.run({ kind: "scene" });
    });
    expect(view.result.current.running).toBe(true);

    act(() => view.result.current.stop());
    expect(view.result.current.running).toBe(false);
    expect(view.result.current.stopped).toBe(true);
    // 停止是用户意图，不是故障 —— 不该渲染成一次事故
    expect(view.result.current.error).toBeNull();
    // 已经跑出来的东西是半成品，必须留下（最常见的场景是图出来了还在回炉）
    expect(view.result.current.roundImages).toHaveLength(1);

    await act(async () => {
      await running;
    });
  });

  it("被取代的那一轮不会回头把新一轮的 running 覆盖成 false", async () => {
    // 这是「点了生成、跑一下就自己停了」的成因：
    // 上一轮被取消后，它的 catch 在微任务里执行，把新一轮的 running 改回 false。
    hangUntilAborted([imageEvent(0)]);
    const view = renderHook(() => useAgentRestoration());

    let first: Promise<void> = Promise.resolve();
    act(() => {
      first = view.result.current.run({ kind: "scene" });
    });
    // 第二轮开始 —— 第一轮被 abort
    let second: Promise<void> = Promise.resolve();
    act(() => {
      second = view.result.current.run({ kind: "scene" });
    });

    // 给第一轮的 catch 足够的机会去犯错
    await act(async () => {
      await first;
      await Promise.resolve();
    });

    expect(view.result.current.running).toBe(true);
    expect(view.result.current.stopped).toBe(false);

    act(() => view.result.current.stop());
    await act(async () => {
      await second;
    });
  });

  it("开始新一轮会清掉上一轮的停止标记", async () => {
    hangUntilAborted([]);
    const view = renderHook(() => useAgentRestoration());

    let first: Promise<void> = Promise.resolve();
    act(() => {
      first = view.result.current.run({ kind: "scene" });
    });
    act(() => view.result.current.stop());
    expect(view.result.current.stopped).toBe(true);
    await act(async () => {
      await first;
    });

    hangUntilAborted([]);
    let second: Promise<void> = Promise.resolve();
    act(() => {
      second = view.result.current.run({ kind: "scene" });
    });
    expect(view.result.current.stopped).toBe(false);
    expect(view.result.current.running).toBe(true);

    act(() => view.result.current.stop());
    await act(async () => {
      await second;
    });
  });
});
