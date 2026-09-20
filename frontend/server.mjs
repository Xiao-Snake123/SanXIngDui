import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs'
import { createServer } from 'node:http'
import http from 'node:http'
import https from 'node:https'
import { extname, join, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT_DIR = fileURLToPath(new URL('.', import.meta.url))
const DIST_DIR = resolve(ROOT_DIR, 'dist')
const PORT = Number(process.env.PORT || 8023)

loadLocalEnv('.env.local')

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.otf': 'font/otf',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  // 缺了这几条时，Hero 的 mp4 会以 application/octet-stream 返回，
  // 浏览器直接下载而不是播放（public/web.config 只在 IIS 下生效，Node 不读它）。
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.ogg': 'audio/ogg',
}

function resolveAgentTarget() {
  const raw = process.env.AGENT_BACKEND_URL || 'http://127.0.0.1:8123'
  try {
    const url = new URL(raw)
    return {
      protocol: url.protocol,
      hostname: url.hostname,
      port: Number(url.port || (url.protocol === 'https:' ? 443 : 80)),
    }
  } catch {
    return { protocol: 'http:', hostname: '127.0.0.1', port: 8123 }
  }
}

const AGENT_TARGET = resolveAgentTarget()

const PROXIES = [
  {
    prefix: '/agent',
    ...AGENT_TARGET,
    // 前缀剥离后直接透传，后端路由本身就是 /api/...
    authEnv: null,
    // SSE 需要逐帧透传，禁用压缩中间层
    streaming: true,
    label: 'multi-agent backend',
  },
  // 这里原本还有 /dashscope 与 /dify 两条「自动附加密钥」的代理：
  // 任何访问者都能借它用你的 DASHSCOPE_API_KEY / CHAT_APP_API_KEY 代发请求，
  // 等于把计费密钥做成公开代理。dev 侧（vite.config.ts）早已删除，
  // 生产侧此前漏删。前端只应经 /agent 访问自己的后端，不直连任何第三方模型服务。
]

function loadLocalEnv(filename) {
  const envPath = join(ROOT_DIR, filename)
  if (!existsSync(envPath)) {
    return
  }

  const lines = readFileSync(envPath, 'utf-8').split(/\r?\n/)
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) {
      continue
    }

    const separatorIndex = trimmed.indexOf('=')
    if (separatorIndex <= 0) {
      continue
    }

    const key = trimmed.slice(0, separatorIndex).trim()
    const value = trimmed.slice(separatorIndex + 1).trim().replace(/^['"]|['"]$/g, '')
    if (!process.env[key]) {
      process.env[key] = value
    }
  }
}

function sendText(res, statusCode, text) {
  res.writeHead(statusCode, {
    'Content-Type': 'text/plain; charset=utf-8',
  })
  res.end(text)
}

function proxyRequest(req, res, proxy) {
  const apiKey = proxy.authEnv ? process.env[proxy.authEnv] : null
  if (proxy.authEnv && !apiKey) {
    sendText(res, 500, `Missing ${proxy.authEnv}`)
    return
  }

  const targetPath = req.url.replace(proxy.prefix, '') || '/'
  const headers = {
    ...req.headers,
    host: proxy.hostname,
  }
  if (apiKey) {
    headers.authorization = `Bearer ${apiKey}`
  }
  if (proxy.streaming) {
    // 关掉 gzip / 压缩缓冲，否则 SSE 会被中间层攒成一批再下发
    headers['accept-encoding'] = 'identity'
    headers['x-accel-buffering'] = 'no'
  }

  delete headers.origin
  delete headers.referer

  const client = proxy.protocol === 'https:' ? https : http
  const proxyReq = client.request(
    {
      protocol: proxy.protocol,
      hostname: proxy.hostname,
      port: proxy.port,
      path: targetPath,
      method: req.method,
      headers,
    },
    (proxyRes) => {
      const responseHeaders = { ...proxyRes.headers }
      if (proxy.streaming) {
        delete responseHeaders['content-encoding']
        delete responseHeaders['content-length']
        responseHeaders['cache-control'] = 'no-cache, no-transform'
        responseHeaders['x-accel-buffering'] = 'no'
      }
      res.writeHead(proxyRes.statusCode || 502, responseHeaders)
      // 直接 pipe：SSE / 大图都走流式，不做整包缓冲
      proxyRes.pipe(res)
    },
  )

  proxyReq.on('error', (error) => {
    sendText(res, 502, `Proxy request failed: ${error.message}`)
  })

  req.pipe(proxyReq)
}

function getStaticPath(urlPath) {
  const cleanPath = decodeURIComponent(urlPath.split('?')[0])
  const requestedPath = cleanPath === '/' ? '/index.html' : cleanPath
  const filePath = normalize(join(DIST_DIR, requestedPath))

  if (!filePath.startsWith(DIST_DIR)) {
    return null
  }

  if (existsSync(filePath) && statSync(filePath).isFile()) {
    return filePath
  }

  return join(DIST_DIR, 'index.html')
}

const server = createServer((req, res) => {
  if (!req.url) {
    sendText(res, 400, 'Bad request')
    return
  }

  if (req.method === 'OPTIONS') {
    // 同源部署：前端静态资源与 /agent 都由本服务提供，不需要跨域头。
    // 此前这里是 `Access-Control-Allow-Origin: *`，等于允许任意站点跨域调用
    // /agent/*（含 POST /api/restore/stream 这类会真实消耗出图额度的接口）。
    res.writeHead(204, {
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
      'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
    })
    res.end()
    return
  }

  const matchedProxy = PROXIES.find((proxy) => req.url?.startsWith(`${proxy.prefix}/`))
  if (matchedProxy) {
    proxyRequest(req, res, matchedProxy)
    return
  }

  const filePath = getStaticPath(req.url)
  if (!filePath || !existsSync(filePath)) {
    sendText(res, 404, 'Not found')
    return
  }

  const extension = extname(filePath)
  res.writeHead(200, {
    'Content-Type': MIME_TYPES[extension] || 'application/octet-stream',
  })
  createReadStream(filePath).pipe(res)
})

server.listen(PORT, '0.0.0.0', () => {
  console.log(`Sanxingdui web server running at http://0.0.0.0:${PORT}`)
  console.log(`Serving static files from ${DIST_DIR}`)
})
