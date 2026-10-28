@echo off
REM Double-click once. Installs everything and creates sample accounts (development only).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" -Demo
pause
