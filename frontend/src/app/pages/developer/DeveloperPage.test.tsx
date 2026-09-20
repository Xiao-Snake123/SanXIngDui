/**
 * 开发者页的渲染测试。
 *
 * 这一页的整个存在意义是「展示**真实**的运行数据」，所以这里断言的不是样式，
 * 而是两件事：
 *   1. 拿到数据时，页面上出现的是后端返回的那个值（而不是硬编码的常量）；
 *   2. 拿不到数据时，页面显示的是**失败原因**，而不是把占位数字填进去。
 *
 * 第 2 条是这个项目的一条硬纪律（宁可如实说降级，也不静默返回次品）。
 * 它同样适用于 UI：一页编造的漂亮数字，比一页写着「数据库连不上」的页面危险得多。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { DeveloperPage } from './DeveloperPage'

function mockFetch(routes: Array<{ match: string; body?: unknown; ok?: boolean }>) {
  vi.stubGlobal('fetch', (input: RequestInfo | URL) => {
    const url = String(input)
    const route = routes.find((item) => url.includes(item.match))
    if (!route || route.ok === false) {
      return Promise.resolve({
        ok: false,
        status: route?.ok === false ? 500 : 404,
        json: () => Promise.resolve({}),
      } as Response)
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(route.body) } as Response)
  })
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/developer/runs']}>
      <Routes>
        <Route path="/developer/:tab" element={<DeveloperPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const HEALTHY = [
  {
    match: '/api/health',
    body: {
      status: 'ok',
      engine: { engine: 'langgraph', configured: 'langgraph', fallback_reason: null },
      providers: { 'free-third-party': true, 'local-placeholder': true },
      retrieval: {
        corpus_size: 50,
        embedder: 'hashing-ngram-1024',
        embedder_degraded: true,
        vector_backend: 'pgvector',
        vector_dimension: 1024,
        vector_reused: 50,
        vector_embedded: 0,
      },
      models_configured: false,
      style_profiles: 5,
      version: '2.0.0',
      storage: {
        database: {
          backend: 'postgresql+asyncpg',
          available: true,
          configured: true,
          server_version: '17.6',
          pgvector_version: '0.8.6',
          schema_revision: 'c52d299de6b7',
          reason: null,
        },
        sessions: { backend: 'redis', available: true, configured: true, reason: null },
        vector: { backend: 'pgvector', pgvector_version: '0.8.6', dimension: 1024, reason: null },
      },
    },
  },
  {
    match: '/api/stats/quality',
    body: {
      available: true,
      window_days: 30,
      overall: {
        kind: null,
        tasks: 4,
        judged: 3,
        passed: 2,
        pass_rate: 0.6667,
        avg_score: 0.7412,
        avg_revisions: 0.75,
        avg_duration_ms: 12345.6,
        qa_skipped: 1,
      },
      by_kind: [
        {
          kind: 'scene',
          tasks: 2,
          judged: 2,
          passed: 1,
          pass_rate: 0.5,
          avg_score: 0.71,
          avg_revisions: 1,
          avg_duration_ms: null,
          qa_skipped: 0,
        },
      ],
    },
  },
  {
    match: '/api/tasks',
    body: {
      available: true,
      total: 4,
      items: [
        {
          task_id: 'sxd-test00000001',
          kind: 'scene',
          item: '青铜大立人',
          identity: null,
          scene: null,
          style: null,
          goal: '复原大祭司',
          engine: 'langgraph',
          profile_key: 'museum_doc',
          material_key: null,
          status: 'ok',
          score: 0.7412,
          objective_score: 0.7412,
          judge_score: null,
          passed: true,
          decision: 'accept',
          revisions: 1,
          provider: 'free-third-party',
          image_degraded: true,
          qa_skipped: false,
          evidence_count: 4,
          degraded_components: [],
          duration_ms: 9000,
          created_at: '2026-09-17T02:00:00+08:00',
          updated_at: '2026-09-17T02:00:00+08:00',
        },
      ],
    },
  },
  { match: '/api/graph', body: { engine: 'langgraph', configured: 'langgraph', fallback_reason: null, max_revisions: 2, style_threshold: 0.72, max_graph_steps: 18, topology: { entry: 'planner', nodes: {}, self_correction_loop: '' } } },
]

describe('DeveloperPage / 运行数据', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('渲染后端返回的真实档位，而不是硬编码常量', async () => {
    mockFetch(HEALTHY)
    renderPage()

    // PostgreSQL 版本、pgvector 版本、Redis 档位都来自 /api/health
    expect(await screen.findByText('17.6')).toBeInTheDocument()
    expect(await screen.findByText('0.8.6')).toBeInTheDocument()
    expect((await screen.findAllByText('redis')).length).toBeGreaterThan(0)
    expect((await screen.findAllByText('pgvector')).length).toBeGreaterThan(0)
    // 向量复用条数是真实的性能证据，必须显示出来
    expect(await screen.findByText('50 条')).toBeInTheDocument()
  })

  it('质量指标按 SQL 聚合的口径展示（分母是判过分的任务数）', async () => {
    mockFetch(HEALTHY)
    renderPage()

    expect(await screen.findByText('66.7%')).toBeInTheDocument()
    expect(await screen.findByText('0.7412')).toBeInTheDocument()
    // skipped 的任务要单独可见：把它算进分母会凭空拉低通过率
    expect(await screen.findByText('其中跳过质检 1')).toBeInTheDocument()
  })

  it('历史任务列出真实 task_id 与判定', async () => {
    mockFetch(HEALTHY)
    renderPage()

    expect(await screen.findByText('青铜大立人')).toBeInTheDocument()
    expect(await screen.findByText('共 4 条 · 点击任意一行查看逐轮出图与质检明细')).toBeInTheDocument()
    expect(await screen.findByText('passed')).toBeInTheDocument()
  })
})

describe('DeveloperPage / 拿不到数据时', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('数据库不可用时显示原始报错，且不出现伪造的 0%', async () => {
    mockFetch([
      {
        match: '/api/health',
        body: {
          status: 'ok',
          engine: { engine: 'builtin', configured: 'langgraph', fallback_reason: 'langgraph 未安装' },
          providers: { 'local-placeholder': true },
          retrieval: { corpus_size: 50, embedder: 'hashing-ngram-1024', embedder_degraded: true },
          models_configured: false,
          version: '2.0.0',
          storage: {
            database: {
              backend: 'disabled',
              available: false,
              configured: true,
              server_version: null,
              pgvector_version: null,
              schema_revision: null,
              reason: 'OperationalError: connection refused',
            },
            sessions: { backend: 'memory', available: false, configured: true, reason: 'ConnectionError: 6379 refused' },
            vector: { backend: 'memory', pgvector_version: null, dimension: 1024, reason: 'OperationalError: connection refused' },
          },
        },
      },
      {
        match: '/api/stats/quality',
        body: { available: false, reason: 'OperationalError: connection refused' },
      },
      { match: '/api/tasks', ok: false },
      { match: '/api/graph', body: { engine: 'builtin', configured: 'langgraph', fallback_reason: 'x', max_revisions: 2, style_threshold: 0.72, max_graph_steps: 18, topology: { entry: 'planner', nodes: {}, self_correction_loop: '' } } },
    ])
    renderPage()

    // 原始报错必须原样出现 —— 一句「服务不可用」等于把排查线索丢掉
    expect((await screen.findAllByText(/connection refused/)).length).toBeGreaterThan(0)
    // 会话退回进程内时要说清代价
    expect(await screen.findByText(/多副本部署会丢上下文/)).toBeInTheDocument()
    // 关键：没有数据库时，页面上不该出现「通过率 0.0%」这种看起来正常、实际是编的数字
    await waitFor(() => {
      expect(screen.queryByText('0.0%')).not.toBeInTheDocument()
    })
  })
})
