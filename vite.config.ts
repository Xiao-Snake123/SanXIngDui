import { defineConfig, loadEnv } from 'vite'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'

function resolveFromRoot(relativePath: string) {
  return decodeURIComponent(new URL(relativePath, import.meta.url).pathname)
}

function figmaAssetResolver() {
  return {
    name: 'figma-asset-resolver',
    resolveId(id: string) {
      if (id.startsWith('figma:asset/')) {
        const filename = id.replace('figma:asset/', '')
        return resolveFromRoot(`./src/assets/${filename}`)
      }
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, resolveFromRoot('./'), '')
  const dashscopeApiKey = env.DASHSCOPE_API_KEY || env.VITE_DASHSCOPE_API_KEY || ''
  const chatAppApiKey = env.CHAT_APP_API_KEY || env.DIFY_APP_API_KEY || env.VITE_CHAT_APP_API_KEY || ''

  return {
    plugins: [
      figmaAssetResolver(),
      // The React and Tailwind plugins are both required for Make, even if
      // Tailwind is not being actively used – do not remove them
      react(),
      tailwindcss(),
    ],
    server: {
      allowedHosts:[
        'zfsxd.cpolar.top',
      ],
      host: '0.0.0.0',
      proxy: {
        '/dashscope': {
          target: 'https://dashscope.aliyuncs.com',
          changeOrigin: true,
          secure: true,
          rewrite: (proxyPath) => proxyPath.replace(/^\/dashscope/, ''),
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (dashscopeApiKey) {
                proxyReq.setHeader('Authorization', `Bearer ${dashscopeApiKey}`)
              }
              proxyReq.removeHeader('origin')
              proxyReq.removeHeader('referer')
            })
          },
        },
        '/dify': {
          target: 'https://sxdapi.aitrais.cn',
          changeOrigin: true,
          secure: true,
          rewrite: (proxyPath) => proxyPath.replace(/^\/dify/, ''),
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (chatAppApiKey) {
                proxyReq.setHeader('Authorization', `Bearer ${chatAppApiKey}`)
              }
              proxyReq.removeHeader('origin')
              proxyReq.removeHeader('referer')
            })
          },
        },
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
