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
}

const PROXIES = [
  {
    prefix: '/dashscope',
    protocol: 'https:',
    hostname: 'dashscope.aliyuncs.com',
    port: 443,
    authEnv: 'DASHSCOPE_API_KEY',
  },
  {
    prefix: '/dify',
    protocol: 'https:',
    hostname: 'sxdapi.aitrais.cn',
    port: 443,
    authEnv: 'CHAT_APP_API_KEY',
  },
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
  const apiKey = process.env[proxy.authEnv]
  if (!apiKey) {
    sendText(res, 500, `Missing ${proxy.authEnv}`)
    return
  }

  const targetPath = req.url.replace(proxy.prefix, '') || '/'
  const headers = {
    ...req.headers,
    host: proxy.hostname,
    authorization: `Bearer ${apiKey}`,
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
      responseHeaders['access-control-allow-origin'] = '*'
      res.writeHead(proxyRes.statusCode || 502, responseHeaders)
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
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
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
