import { defineConfig } from 'vite'
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

export default defineConfig({
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
      '/comfy': {
        target: 'http://127.0.0.1:8188',
        changeOrigin: true,
        rewrite: (proxyPath) => proxyPath.replace(/^\/comfy/, ''),
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            proxyReq.removeHeader('origin')
            proxyReq.removeHeader('referer')
          })
        },
      },
      '/openwebui': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
        rewrite: (proxyPath) => proxyPath.replace(/^\/openwebui/, ''),
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
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
})
