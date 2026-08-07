@echo off
REM GroundedOps - start AND share with everyone on your network.
REM Double-click to run. The window shows the address to give colleagues.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -Lan
if errorlevel 1 pause
