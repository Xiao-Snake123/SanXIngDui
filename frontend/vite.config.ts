import { defineConfig, loadEnv } from 'vite'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'

function resolveFromRoot(relativePath: string) {
  return decodeURIComponent(new URL(relativePath, import.meta.url).pathname)
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, resolveFromRoot('./'), '')
  // 多智能体后端（FastAPI + LangGraph）。默认本机 8123 端口。
  const agentTarget =
    env.AGENT_BACKEND_URL || env.VITE_AGENT_BACKEND_URL || 'http://127.0.0.1:8123'

  // 测试环境（vitest 运行 NODE_ENV=test）完全不加载 vite 插件：
  // vitest 5.0.1 在收集阶段加载 vite.config.ts 时，react() 插件会修改 config 内部状态，
  // 导致 "Cannot read properties of undefined (reading 'config')"。vitest.config.ts 自带的
  // react() 已满足测试需求，这里只补一个 alias 即可。
  if (process.env.NODE_ENV === 'test') {
    return {
      resolve: { alias: { '@': resolveFromRoot('./src') } },
    }
  }
  return {
    plugins: [react(), tailwindcss()],
    server: {
      allowedHosts:[
        'zfsxd.cpolar.top',
      ],
      host: '0.0.0.0',
      proxy: {
        // ── 多智能体复原服务（含领域问答）────────────────────────────────
        // 前端统一走 /agent 前缀，后端返回的 /media/... 图片也经此前缀回源，
        // 因此浏览器端只需要知道一个相对地址，无需感知后端端口与部署形态。
        // SSE 要求关闭任何形式的缓冲与压缩，这里显式配置 ws=false / 不启用压缩。
        //
        // 讲解助手（/api/ask）也走这条：问答要带引用，引用必须来自我们自己的语料，
        // 所以它由后端提供，前端不再需要任何直连模型服务的通道。
        '/agent': {
          target: agentTarget,
          changeOrigin: true,
          secure: false,
          ws: false,
          rewrite: (proxyPath) => proxyPath.replace(/^\/agent/, ''),
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              // 关闭中间层压缩与缓冲，保证 SSE 逐帧到达
              proxyReq.setHeader('Accept-Encoding', 'identity')
              proxyReq.setHeader('X-Accel-Buffering', 'no')
            })
            proxy.on('proxyRes', (proxyRes) => {
              delete proxyRes.headers['content-encoding']
              proxyRes.headers['cache-control'] = 'no-cache, no-transform'
            })
          },
        },
        // 这里曾有 '/dashscope' 与 '/dify' 两条代理，已删除：
        //   - '/dashscope' 会把 DASHSCOPE_API_KEY **自动注入**到任何匹配该前缀的请求上。
        //     即使只监听本机，任何能访问 http://localhost:5173/dashscope/... 的进程或页面
        //     都能拿到一个用该 Key 签名的请求。而全仓库已无任何代码使用它 ——
        //     一条没人用、却会自动附加密钥的代理，是纯粹的风险敞口。
        //   - '/dify' 是讲解助手改走本项目的 /api/ask 之前的外部对话通道，
        //     一并删除，避免留下一条绕过检索、无从溯源的旁路。
        // 需要恢复时从 git 历史取回，不要凭记忆重写。
      },
    },

    resolve: {
      alias: {
        // Alias @ to the src directory
        '@': resolveFromRoot('./src'),
      },
    },

    // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
    assetsInclude: ['**/*.svg', '**/*.csv'],
  }
})
