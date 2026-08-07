@echo off
REM GroundedOps installer - double-click this file.
REM Wraps install.ps1 with -ExecutionPolicy Bypass, because Windows blocks
REM unsigned .ps1 files by default and the error looks like a broken download.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
  echo.
  echo Install failed - see the messages above.
  pause
)
