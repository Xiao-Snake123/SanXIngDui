/**
 * SSE 帧解析与流式接口的测试。
 *
 * 为什么这一块最该测：`consumeSse` 是整个前端唯一一处**手工实现的分帧逻辑**
 * （EventSource 不支持 POST，所以只能用 fetch + ReadableStream 自己切）。
 * 它要同时处理四种容易被忽略的情况，而每一种的表现都是「偶发、难复现」：
 *
 *   1. 一个帧被 TCP 分片成多次 read —— 必须靠 buffer 拼接，否则 JSON.parse 半截就炸；
 *   2. 一次 read 里到达多个帧 —— 必须循环切，否则后面的帧一直卡在 buffer 里；
 *   3. `: ping` 心跳注释帧 —— 必须忽略，不能当数据帧喂给调用方；
 *   4. 流结束时的残留 buffer —— 没有结尾空行时最后一帧会丢。
 *
 * 这些都不是「理论上的边界」，是这个项目真实遇到过的：SSE 掉帧会表现为
 * 「图已经出来了但界面还卡在 68%」，而且只在网络抖动时出现。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  consumeSse,
  streamChat,
  streamRestoration,
  type ChatStreamHandlers,
  type StreamHandlers,
} from './agentApi'

/** 造一个可控的 SSE 响应：每个字符串元素是**一次 read** 返回的内容。 */
function sseResponse(
  chunks: string[],
  init: { ok?: boolean; status?: number; text?: string } = {},
): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    body: stream,
    text: () => Promise.resolve(init.text ?? ''),
  } as unknown as Response
}

function frame(event: string, payload: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`
}

describe('consumeSse / 分帧', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('一次 read 里到达多个帧时全部派发', async () => {
    const received: Array<[string, unknown]> = []
    await consumeSse(sseResponse([frame('a', { n: 1 }) + frame('b', { n: 2 })]), (event, payload) =>
      received.push([event, payload]),
    )
    expect(received).toEqual([
      ['a', { n: 1 }],
      ['b', { n: 2 }],
    ])
  })

  it('一个帧被切成多段时靠 buffer 拼回来', async () => {
    // 模拟 TCP 分片：JSON 被从中间切开，任何一段单独解析都会失败
    const received: Array<[string, unknown]> = []
    await consumeSse(
      sseResponse(['event: node\nda', 'ta: {"progress":', '68}\n', '\n']),
      (event, payload) => received.push([event, payload]),
    )
    expect(received).toEqual([['node', { progress: 68 }]])
  })

  it(': ping 心跳注释帧被忽略，不喂给调用方', async () => {
    const received: Array<[string, unknown]> = []
    await consumeSse(sseResponse([': ping\n\n', frame('node', { ok: true })]), (event, payload) =>
      received.push([event, payload]),
    )
    expect(received).toEqual([['node', { ok: true }]])
  })

  it('data 不是合法 JSON 时忽略该帧而不是抛异常', async () => {
    const received: Array<[string, unknown]> = []
    await expect(
      consumeSse(sseResponse(['event: x\ndata: {oops\n\n', frame('ok', { v: 1 })]), (event, payload) =>
        received.push([event, payload]),
      ),
    ).resolves.toBeUndefined()
    // 坏帧被丢掉，后面的好帧不受影响 —— 这是「一个坏帧不该打断整条流」的语义
    expect(received).toEqual([['ok', { v: 1 }]])
  })

  it('流结束时没有结尾空行的残留帧也会被处理', async () => {
    // 服务端最后一帧没写 `\n\n` 就断开，这是真实遇到过的情况（客户端提前关闭）
    const received: Array<[string, unknown]> = []
    await consumeSse(sseResponse(['event: done\ndata: {"final":true}']), (event, payload) =>
      received.push([event, payload]),
    )
    expect(received).toEqual([['done', { final: true }]])
  })

  it('响应非 2xx 时抛出带状态码与正文的错误', async () => {
    await expect(
      consumeSse(sseResponse([], { ok: false, status: 502, text: 'bad gateway' }), () => {}),
    ).rejects.toThrow(/502.*bad gateway/s)
  })
})

describe('streamRestoration / 事件分发', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const silent: StreamHandlers = {}

  it('把 start / node / trace / done 分发到对应回调', async () => {
    const seen: string[] = []
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        sseResponse([
          frame('start', { task_id: 't1', kind: 'scene', max_revisions: 2, engine: 'langgraph' }),
          frame('node', { node: 'restoration', label: '图像修复', progress: 68, completed: [], elapsed_ms: 1, patch: {} }),
          frame('trace', { seq: 1, task_id: 't1', kind: 'scene', node: 'quality', ts: '', payload: {} }),
          frame('done', { task_id: 't1', result: { task_id: 't1' } }),
        ]),
      ),
    )

    await streamRestoration({ kind: 'scene' }, {
      onStart: (event) => seen.push(`start:${event.task_id}`),
      onNode: (event) => seen.push(`node:${event.progress}`),
      onTrace: (event) => seen.push(`trace:${event.node}`),
      onDone: (event) => seen.push(`done:${event.result.task_id}`),
    })

    expect(seen).toEqual(['start:t1', 'node:68', 'trace:quality', 'done:t1'])
  })

  it('error 帧走 onError，不抛异常', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(sseResponse([frame('error', { message: '出图 provider 全部失败' })])),
    )
    const onError = vi.fn()
    await streamRestoration({ kind: 'scene' }, { onError })
    expect(onError).toHaveBeenCalledWith('出图 provider 全部失败')
  })

  it('连不上后端时给出可执行的提示，而不是一句「失败」', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new Error('ECONNREFUSED')))
    const onError = vi.fn()
    await streamRestoration({ kind: 'scene' }, { onError })
    // 提示要能直接指导排查（后端没起 / 代理指错），否则用户只能猜
    expect(onError.mock.calls[0][0]).toMatch(/后端|代理/)
  })

  it('调用方主动取消（AbortError）时向上抛出，不当成错误上报', async () => {
    // 用户点了「停止」不是故障。如果这里吞掉 AbortError 并调 onError，
    // 界面会在用户主动取消后弹一个红色错误条。
    const abort = new Error('aborted')
    abort.name = 'AbortError'
    vi.stubGlobal('fetch', () => Promise.reject(abort))
    const onError = vi.fn()
    await expect(streamRestoration({ kind: 'scene' }, { onError })).rejects.toMatchObject({
      name: 'AbortError',
    })
    expect(onError).not.toHaveBeenCalled()
  })

  it('空 handlers 不炸（所有回调都是可选的）', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(sseResponse([frame('node', { node: 'planner' })])))
    await expect(streamRestoration({ kind: 'scene' }, silent)).resolves.toBeUndefined()
  })
})

describe('streamChat / 事件分发', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('按帧类型分发，delta 只取 text 字段', async () => {
    const events: string[] = []
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        sseResponse([
          frame('session', { session_id: 'chat-1', turns: 2 }),
          frame('intent', { intent: { kind: 'scene' }, mode: 'rule' }),
          frame('delta', { text: '正在' }),
          frame('delta', { text: '生成' }),
          frame('proposals', { items: [], mode: 'rule' }),
          frame('message', { text: '好了', mode: 'rule' }),
          frame('followups', { items: ['再试一次'] }),
        ]),
      ),
    )

    const handlers: ChatStreamHandlers = {
      onSession: (payload) => events.push(`session:${payload.session_id}`),
      onIntent: (payload) => events.push(`intent:${payload.mode}`),
      onDelta: (text) => events.push(`delta:${text}`),
      onProposals: (payload) => events.push(`proposals:${payload.items.length}`),
      onMessage: (payload) => events.push(`message:${payload.text}`),
      onFollowups: (items) => events.push(`followups:${items.join(',')}`),
    }

    await streamChat('你好', null, handlers)
    expect(events).toEqual([
      'session:chat-1',
      'intent:rule',
      'delta:正在',
      'delta:生成',
      'proposals:0',
      'message:好了',
      'followups:再试一次',
    ])
  })

  it('未知帧类型被安静忽略（后端加新事件不该让老前端炸）', async () => {
    const onMessage = vi.fn()
    vi.stubGlobal('fetch', () =>
      Promise.resolve(sseResponse([frame('brand_new_event', { x: 1 }), frame('message', { text: 'hi' })])),
    )
    await streamChat('hi', null, { onMessage })
    expect(onMessage).toHaveBeenCalledTimes(1)
  })

  it('请求体带上 message 与 session_id', async () => {
    let body: unknown = null
    vi.stubGlobal('fetch', (_input: RequestInfo | URL, init?: RequestInit) => {
      body = JSON.parse(String(init?.body))
      return Promise.resolve(sseResponse([]))
    })
    await streamChat('想看青铜大立人', 'chat-9', {})
    expect(body).toEqual({ message: '想看青铜大立人', session_id: 'chat-9' })
  })
})
