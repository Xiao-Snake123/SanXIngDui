@echo off
rem ============================================================================
rem  One-command launcher for the whole project (backend + frontend).
rem
rem  Usage:
rem      start.cmd                  (or just double-click this file)
rem      start.cmd -Reload
rem      start.cmd -BackendPort 8124 -FrontendPort 5174
rem      start.cmd -CheckOnly       (dry run: preflight only, starts nothing)
rem
rem  Why a .cmd wrapper: Windows blocks .ps1 scripts by default
rem  (ExecutionPolicy=Restricted), so a bare "start.ps1" would be refused.
rem  This wrapper invokes PowerShell with -ExecutionPolicy Bypass for this one
rem  run only -- no system-wide policy change.
rem ============================================================================
setlocal

if not exist "%~dp0start.ps1" (
    echo [!] start.ps1 not found next to this file: %~dp0
    pause
    exit /b 1
)

where powershell >nul 2>nul
if errorlevel 1 (
    echo [!] powershell not found on PATH.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "CODE=%ERRORLEVEL%"

rem Keep the window open on failure so double-click users can read the message.
if not "%CODE%"=="0" (
    echo.
    echo [!] start.ps1 exited with code %CODE%
    pause
)

endlocal & exit /b %CODE%
