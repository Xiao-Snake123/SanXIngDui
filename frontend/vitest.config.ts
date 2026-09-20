import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

function resolveFromRoot(relativePath: string) {
  return decodeURIComponent(new URL(relativePath, import.meta.url).pathname)
}

/**
 * 组件测试配置。
 *
 * 单独一份而不是塞进 vite.config.ts：那份配置是给构建与 dev server 用的
 * （代理、SSE 缓冲、Tailwind 插件），测试不需要也不该继承它们 ——
 * 一个 200 行的构建配置被测试间接依赖，是「改构建把测试搞挂」的常见来源。
 *
 * 环境用 jsdom（组件测试需要 DOM），别名与构建保持一致的 `@`。
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': resolveFromRoot('./src'),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    // 组件测试不联网：所有 fetch 都在用例里被替换。这里再加一道保险，
    // 防止有人忘了 mock 时测试悄悄打到真实后端（那会让结果依赖本机是否起了服务）。
    restoreMocks: true,
  },
})
