# ─────────────────────────────────────────────────────────────────────────────
#  一键启动：后端（FastAPI / uvicorn）+ 前端（Vite dev server）
#
#  用法（在项目根目录 SanXIngDui/ 下执行）：
#    powershell -ExecutionPolicy Bypass -File start.ps1
#    powershell -ExecutionPolicy Bypass -File start.ps1 -Reload
#    powershell -ExecutionPolicy Bypass -File start.ps1 -CheckOnly
#    powershell -ExecutionPolicy Bypass -File start.ps1 -BackendPort 8124 -FrontendPort 5174
#
#  它做的事：
#    1. 预检 python / node / npm、后端依赖、前端 node_modules（只报告，不自动安装）；
#    2. 后端端口上若已跑着**本项目**后端（/api/health 通），复用它而不是另起一个 ——
#       HANDOFF 记录过「同时存在两个后端，前端连上旧代码」的坑；
#       若端口被别的进程占用且不答 /api/health，直接报错退出，**不乱杀别人的进程**；
#    3. 后台启动后端（日志写 backend/var/dev.backend.*.log），轮询等 /api/health 就绪；
#    4. 前台启动前端；脚本退出（含 Ctrl+C）时，停掉**它自己启动的**后端进程树。
#
#  控制台输出刻意保持 ASCII：Windows PowerShell 5.1 控制台是 GBK，
#  本文件是 UTF-8 无 BOM，打印中文/符号会乱码（见 docs/HANDOFF.md §7.3）。
# ─────────────────────────────────────────────────────────────────────────────
param(
    [int]$BackendPort = 8123,
    [int]$FrontendPort = 5173,
    [int]$TimeoutSec = 90,
    [switch]$Reload,
    [switch]$NoBackend,
    [switch]$NoFrontend,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'

# 本机探活不要让系统代理插手：Clash 之类会把 127.0.0.1 也丢给代理（见 backend/app/core/http.py）
[System.Net.WebRequest]::DefaultWebProxy = $null

$root = $PSScriptRoot
$backendDir = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'
$healthUrl = "http://127.0.0.1:$BackendPort/api/health"

function Write-Step($text) { Write-Host "[*] $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "[OK] $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "[!] $text" -ForegroundColor Yellow }

function Test-BackendHealth {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

if (-not (Test-Path $backendDir) -or -not (Test-Path $frontendDir)) {
    Write-Warn2 "start.ps1 must sit in the project root (backend/ and frontend/ are its siblings)."
    exit 1
}

# ── 1) 预检 ──────────────────────────────────────────────────────────────────
Write-Step 'checking prerequisites'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Warn2 'python not found on PATH.'
    exit 1
}

if (-not $NoBackend) {
    $required = @('fastapi', 'uvicorn', 'pydantic_settings', 'httpx', 'numpy', 'PIL')
    $missing = @()
    foreach ($pkg in $required) {
        python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('$pkg') else 1)" 2>$null
        if ($LASTEXITCODE -ne 0) { $missing += $pkg }
    }
    if ($missing.Count -gt 0) {
        Write-Warn2 ('missing python packages: ' + ($missing -join ', '))
        Write-Warn2 'run:  cd backend;  python -m pip install -r requirements.txt'
        exit 1
    }
    if (-not (Test-Path (Join-Path $backendDir '.env'))) {
        Write-Warn2 'backend/.env not found -> will start in fully-degraded mode (still functional).'
        Write-Warn2 'optional:  Copy-Item backend\.env.example backend\.env'
    }
}

if (-not $NoFrontend) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        Write-Warn2 'node not found on PATH.'
        exit 1
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Warn2 'npm not found on PATH.'
        exit 1
    }
    if (-not (Test-Path (Join-Path $frontendDir 'node_modules'))) {
        Write-Warn2 'frontend/node_modules not found.'
        Write-Warn2 'run:  cd frontend;  npm install'
        exit 1
    }
}

Write-Ok 'prerequisites ok'

if ($CheckOnly) {
    Write-Host ''
    Write-Host "backend  : http://127.0.0.1:$BackendPort   ($backendDir)"
    Write-Host "frontend : http://127.0.0.1:$FrontendPort   ($frontendDir)"
    Write-Host "reload   : $Reload"
    Write-Host '(nothing was started: -CheckOnly)'
    exit 0
}

# ── 2) 后端：能复用就复用；端口被外人占了就报错退出 ──────────────────────────
$backendProc = $null
$startedBackend = $false

if (-not $NoBackend) {
    $listeners = @(Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue)

    if ($listeners.Count -gt 0) {
        if (Test-BackendHealth) {
            Write-Ok "an SXD backend is already serving on :$BackendPort -- reusing it (avoiding a second backend)."
        } else {
            Write-Warn2 "port $BackendPort is taken, and it does NOT answer /api/health."
            Write-Warn2 'that is not this project''s backend -- pick another port:  -BackendPort 8124'
            foreach ($connection in $listeners) {
                $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
                if ($proc) { Write-Warn2 ('  pid ' + $proc.ProcessId + ' : ' + $proc.CommandLine) }
            }
            exit 1
        }
    } else {
        $varDir = Join-Path $backendDir 'var'
        if (-not (Test-Path $varDir)) { New-Item -ItemType Directory -Path $varDir | Out-Null }
        $outLog = Join-Path $varDir 'dev.backend.out.log'
        $errLog = Join-Path $varDir 'dev.backend.err.log'

        $backendArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$BackendPort")
        if ($Reload) { $backendArgs += '--reload' }

        Write-Step "starting backend on :$BackendPort  (log: backend\var\dev.backend.out.log)"
        $backendProc = Start-Process -FilePath 'python' -ArgumentList $backendArgs `
            -WorkingDirectory $backendDir -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        $startedBackend = $true

        $deadline = (Get-Date).AddSeconds($TimeoutSec)
        $ready = $false
        while ((Get-Date) -lt $deadline) {
            if ($backendProc.HasExited) { break }
            if (Test-BackendHealth) { $ready = $true; break }
            Start-Sleep -Milliseconds 700
        }

        if (-not $ready) {
            Write-Warn2 "backend did not become healthy within ${TimeoutSec}s."
            if ($backendProc.HasExited) { Write-Warn2 ('process exited, code ' + $backendProc.ExitCode) }
            Write-Warn2 'tail of backend\var\dev.backend.err.log:'
            if (Test-Path $errLog) { Get-Content $errLog -Tail 20 | ForEach-Object { Write-Host "    $_" } }
            if (-not $backendProc.HasExited) { & taskkill /PID $backendProc.Id /T /F 2>$null | Out-Null }
            exit 1
        }
        Write-Ok "backend healthy: $healthUrl"
    }
}

# ── 3) 前端（前台运行，Ctrl+C 结束）───────────────────────────────────────────
$exitCode = 0
try {
    if ($NoFrontend) {
        if ($startedBackend) {
            Write-Ok "backend is running. Press Ctrl+C to stop it."
            Wait-Process -Id $backendProc.Id -ErrorAction SilentlyContinue
        } else {
            Write-Ok 'nothing to do (backend reused, frontend disabled).'
        }
    } else {
        # 显式设一遍代理目标：默认值已是 8123，但这里跟随 -BackendPort，避免两者不一致
        $env:AGENT_BACKEND_URL = "http://127.0.0.1:$BackendPort"
        Write-Host ''
        Write-Ok "frontend: http://127.0.0.1:$FrontendPort   (proxying /agent -> $env:AGENT_BACKEND_URL)"
        Write-Host 'Press Ctrl+C to stop both.' -ForegroundColor DarkGray
        Write-Host ''
        Push-Location $frontendDir
        try {
            & npm run dev -- --port $FrontendPort
            $exitCode = $LASTEXITCODE
        } finally {
            Pop-Location
        }
    }
} finally {
    # 只停自己启动的后端；复用来的后端不动它
    if ($startedBackend -and $backendProc -and -not $backendProc.HasExited) {
        Write-Step "stopping backend (pid $($backendProc.Id))"
        & taskkill /PID $backendProc.Id /T /F 2>$null | Out-Null
    }
}

exit $exitCode
