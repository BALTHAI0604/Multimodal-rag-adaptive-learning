@echo off
REM Double-click to start (after running setup once). Opens http://localhost:8000
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
pause
