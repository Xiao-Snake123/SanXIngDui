@echo off
rem ============================================================================
rem  restart.cmd  -  ensure a FRESH backend (latest code) on :8123
rem
rem  start.cmd REUSES an already-healthy backend, so double-clicking it will
rem  NOT pick up code changes. This wrapper first kills the backend we started
rem  (matched by its command line containing 'app.main:app' -- never a blind
rem  port kill), then launches start.cmd -Reload so future edits auto-reload.
rem
rem  Usage:
rem      restart.cmd                     -> kill stale backend, start with -Reload
rem      restart.cmd -CheckOnly          -> preflight only (does NOT kill anything)
rem      restart.cmd -BackendPort 8124 -FrontendPort 5174
rem ============================================================================
setlocal
set "PORT=8123"
set "ARGS=%*"

rem -CheckOnly: just pass through, never kill anything
echo %ARGS% | findstr /I "CheckOnly" >nul
if not errorlevel 1 (
    call "%~dp0start.cmd" %ARGS%
    goto :eof
)

rem 1) stop any backend WE started that is still listening on %PORT%.
rem    Match by command line ('app.main:app') so we never kill a foreign process.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$pids = @(Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique); foreach ($procId in $pids) { $proc = Get-CimInstance Win32_Process -Filter \"ProcessId=$procId\" -ErrorAction SilentlyContinue; if ($proc -and $proc.CommandLine -like '*app.main:app*') { Write-Host ('[restart] stopping stale backend pid ' + $procId); & taskkill /PID $procId /T /F | Out-Null } }"

rem 2) give the OS a moment to free the port
timeout /t 1 >nul 2>nul

rem 3) start fresh; add -Reload unless the caller already passed it
echo %ARGS% | findstr /I "Reload" >nul
if errorlevel 1 ( set "RELOAD=-Reload" ) else ( set "RELOAD=" )
echo [restart] starting project (start.cmd %RELOAD% %ARGS%)
call "%~dp0start.cmd" %RELOAD% %ARGS%

endlocal
