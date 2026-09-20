@echo off
rem ---------------------------------------------------------------------------
rem  Forwarder.
rem  The real launcher lives in the project root:  ..\start.cmd  (wraps start.ps1)
rem  It is kept here so that ".\start.cmd" also works when your shell is sitting
rem  in backend\ -- which is where the backend-only commands are usually run.
rem  Arguments are passed straight through:  .\start.cmd -Reload
rem ---------------------------------------------------------------------------
call "%~dp0..\start.cmd" %*
exit /b %ERRORLEVEL%
