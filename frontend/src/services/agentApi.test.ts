import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  AGENT_BASE,
  fetchOpenApiOperations,
  fetchQualityStats,
  fetchTaskList,
  intentToRequest,
  proposalToOverride,
  resolveAssetUrl,
  type ChatProposal,
} from './agentApi'

/** 造一个最小的 fetch 桩：按 URL 片段匹配返回值，并记录被请求过的地址。 */
function mockFetch(routes: Array<{ match: string; body: unknown; ok?: boolean }>) {
  const calls: string[] = []
  vi.stubGlobal('fetch', (input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    const route = routes.find((item) => url.includes(item.match))
    if (!route) {
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) } as Response)
    }
    return Promise.resolve({
      ok: route.ok ?? true,
      status: route.ok === false ? 500 : 200,
      json: () => Promise.resolve(route.body),
    } as Response)
  })
  return calls
}

describe('resolveAssetUrl', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('把后端相对路径挂到 /agent 前缀下', () => {
    // 后端返回 /media/xxx.png，浏览器必须走代理才能取到 —— 直接请求会打到前端自己的静态服务
    expect(resolveAssetUrl('/media/a.png')).toBe(`${AGENT_BASE}/media/a.png`)
    expect(resolveAssetUrl('media/a.png')).toBe(`${AGENT_BASE}/media/a.png`)
  })

  it('绝对地址与 data URI 原样返回', () => {
    expect(resolveAssetUrl('https://cdn.example.com/a.png')).toBe('https://cdn.example.com/a.png')
    expect(resolveAssetUrl('data:image/png;base64,AAAA')).toBe('data:image/png;base64,AAAA')
  })

  it('空值返回空串，不产出 "/agent/null" 这种地址', () => {
    expect(resolveAssetUrl(null)).toBe('')
    expect(resolveAssetUrl(undefined)).toBe('')
  })
})

describe('proposalToOverride', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const proposal = {
    id: 'p1',
    title: '展陈纪实',
    prompt: 'PROMPT',
    negative_prompt: 'iron tools',
    style_profile: 'museum_doc',
    params: { width: 768, height: 960, style_strength: 0.5, prompt_extend: true },
  } as unknown as ChatProposal

  it('默认关闭提示词增强', () => {
    // 这是「已锁定为你选定的提示词」这句承诺在参数层面的落地：
    // 方案里就算写了 prompt_extend: true，选定后也必须关掉。
    const override = proposalToOverride(proposal)
    expect(override.prompt_extend).toBe(false)
    expect(override.prompt).toBe('PROMPT')
    expect(override.width).toBe(768)
  })

  it('显式覆盖优先', () => {
    const override = proposalToOverride(proposal, { prompt: '用户改写的' })
    expect(override.prompt).toBe('用户改写的')
  })
})

describe('intentToRequest', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('kind 必填，其余按存在与否透传', () => {
    const request = intentToRequest({ kind: 'scene', subject: '青铜大立人', scene: '博物馆展厅' })
    expect(request.kind).toBe('scene')
    expect(request.item).toBe('青铜大立人')
    expect(request.artifact).toBe('青铜大立人')
    expect(request.scene).toBe('博物馆展厅')
    expect('strength' in request).toBe(false)
  })

  it('strength 为 0 时不写入', () => {
    const request = intentToRequest({ kind: 'style', strength: 0 })
    expect('strength' in request).toBe(false)
  })
})

describe('fetchOpenApiOperations', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('把 openapi.json 展平成端点清单并跳过非方法字段', async () => {
    mockFetch([
      {
        match: '/openapi.json',
        body: {
          paths: {
            '/api/health': { get: { summary: '健康检查', tags: ['meta'] } },
            '/api/restore': { post: { summary: '同步复原', tags: ['exec'] }, parameters: [] },
          },
        },
      },
    ])
    const operations = await fetchOpenApiOperations()
    expect(operations).toHaveLength(2)
    expect(operations[0]).toMatchObject({ method: 'GET', path: '/api/health' })
    expect(operations[1]).toMatchObject({ method: 'POST', path: '/api/restore' })
    // 按路径排序，保证渲染顺序稳定（否则每次刷新列表都会跳）
    expect(operations.map((item) => item.path)).toEqual(['/api/health', '/api/restore'])
  })

  it('后端不可用时返回空数组而不是抛异常', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new Error('boom')))
    await expect(fetchOpenApiOperations()).resolves.toEqual([])
  })
})

describe('任务与统计接口', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('任务列表把过滤条件带进 query', async () => {
    const calls = mockFetch([
      { match: '/api/tasks', body: { items: [], total: 0, available: true } },
    ])
    await fetchTaskList({ limit: 5, kind: 'scene', passed: true })
    expect(calls[0]).toContain('limit=5')
    expect(calls[0]).toContain('kind=scene')
    expect(calls[0]).toContain('passed=true')
  })

  it('质量统计透出数据库不可用的原因，而不是伪造一个 0', async () => {
    mockFetch([
      {
        match: '/api/stats/quality',
        body: { available: false, reason: 'OperationalError: connection refused' },
      },
    ])
    const stats = await fetchQualityStats(30)
    expect(stats?.available).toBe(false)
    expect(stats?.reason).toContain('connection refused')
  })

  it('网络失败返回 null，由调用方决定怎么显示', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new Error('offline')))
    await expect(fetchTaskList()).resolves.toBeNull()
    await expect(fetchQualityStats()).resolves.toBeNull()
  })
})
