# ─────────────────────────────────────────────────────────────────────────────
#  启动多智能体复原后端（Windows PowerShell）
#  用法：  powershell -ExecutionPolicy Bypass -File backend/scripts/run.ps1
#          ... -Port 8124        换端口
#          ... -Force            先停掉这个端口上已在跑的本项目后端，再重启
#  权限：  无需管理员权限
# ─────────────────────────────────────────────────────────────────────────────
param(
    [int]$Port = 8123,
    # 不能叫 $Host —— 那是 PowerShell 的自动只读变量（host 对象），
    # 拿它当参数名会直接报 "无法覆盖变量 Host / VariableNotWritable"。
    [string]$BindHost = "127.0.0.1",
    [switch]$Reload,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$backendRoot = Split-Path -Parent $PSScriptRoot
Set-Location $backendRoot

Write-Host "backend root : $backendRoot" -ForegroundColor DarkGray

# 0) 端口预检 —— 否则会「完整启动一遍、最后 bind 失败」（Errno 10048），白等一次。
#    三种情况分开处理：空闲 → 正常启动；已有本项目后端 → 复用它；被别的进程占了 → 报错退出。
$healthUrl = "http://${BindHost}:${Port}/api/health"
$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    $ownerPids = @()
    $ownedByUs = $false
    foreach ($conn in $listeners) {
        $ownerPids += $conn.OwningProcess
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
        if ($proc -and $proc.CommandLine -like "*app.main:app*") { $ownedByUs = $true }
    }

    if ($ownedByUs -and -not $Force) {
        Write-Host "端口 $Port 上已经跑着本项目后端（pid $($ownerPids -join ', ')），无需重复启动。" -ForegroundColor Yellow
        Write-Host "  健康检查: $healthUrl" -ForegroundColor Yellow
        Write-Host "  要重启它: 加 -Force      要换端口: -Port 8124" -ForegroundColor DarkGray
        exit 0
    }

    if ($ownedByUs -and $Force) {
        Write-Host "先停掉端口 $Port 上已有的本项目后端（pid $($ownerPids -join ', ')）…" -ForegroundColor Yellow
        foreach ($procId in $ownerPids) { & taskkill /PID $procId /T /F 2>$null | Out-Null }
        Start-Sleep -Milliseconds 900
    } else {
        Write-Host "端口 $Port 被**别的进程**占用，且不像是本项目后端：" -ForegroundColor Red
        foreach ($conn in $listeners) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
            if ($proc) { Write-Host ("  pid " + $proc.ProcessId + " : " + $proc.CommandLine) -ForegroundColor DarkGray }
        }
        Write-Host "请换端口启动：-Port 8124" -ForegroundColor Yellow
        exit 1
    }
}

# 1) 依赖检查（只报告，不自动安装，避免污染用户环境）
$required = @("fastapi", "uvicorn", "pydantic_settings", "httpx", "numpy", "PIL")
$missing = @()
foreach ($pkg in $required) {
    python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('$pkg') else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) { $missing += $pkg }
}
if ($missing.Count -gt 0) {
    Write-Host "缺少依赖: $($missing -join ', ')" -ForegroundColor Yellow
    # 注意路径：脚本开头已 Set-Location 到 backend/，所以是 requirements.txt 而不是 backend\requirements.txt
    Write-Host "请先执行: python -m pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

# 2) 可选依赖提示
python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('langgraph') else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "未安装 langgraph，将使用内置 DAG 调度器（节点与路由规则一致）" -ForegroundColor Yellow
}

# 3) 配置提示
if (-not (Test-Path (Join-Path $backendRoot ".env"))) {
    Write-Host "未找到 backend\.env，将以「全部降级」模式启动（功能完整、效果受限）" -ForegroundColor Yellow
    Write-Host "可执行: Copy-Item backend\.env.example backend\.env" -ForegroundColor DarkGray
}

# 4) 启动
# 同理不用 $args（PowerShell 自动变量，保存未绑定的实参）——改名避免歧义。
$uvicornArgs = @("-m", "uvicorn", "app.main:app", "--host", $BindHost, "--port", "$Port")
if ($Reload) { $uvicornArgs += "--reload" }

Write-Host "启动: python $($uvicornArgs -join ' ')" -ForegroundColor Green
Write-Host "接口文档: http://${BindHost}:${Port}/docs" -ForegroundColor Green
python @uvicornArgs
