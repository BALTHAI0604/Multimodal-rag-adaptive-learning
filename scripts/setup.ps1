<#
 One-time setup (Windows PowerShell 5.1+ or PowerShell 7).
   .\scripts\setup.ps1 [-Demo] [-AI]
     -Demo  also create sample accounts (DEVELOPMENT ONLY); one-time passwords are saved to data\demo-accounts.txt
     -AI    also install the optional neural-embedding + Whisper packages (large downloads)
 If scripts are blocked:  Set-ExecutionPolicy -Scope Process Bypass
#>
param([switch]$Demo, [switch]$AI)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
function Need($cmd, $hint) { if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd not found. $hint" } }
Need python "Install Python 3.10+ from https://www.python.org/downloads/ (tick 'Add to PATH')."
Need node   "Install Node.js 20+ from https://nodejs.org/"

Write-Host "== Backend" -ForegroundColor Cyan
if (-not (Test-Path backend\.venv)) { python -m venv backend\.venv }
$Py = Join-Path $Root "backend\.venv\Scripts\python.exe"
& $Py -m pip install -q --upgrade pip
& $Py -m pip install -q -r backend\requirements-dev.txt
if ($AI) { & $Py -m pip install -q -r backend\requirements-optional.txt }
& $Py scripts\envset.py init
Push-Location backend; & $Py -m app.cli init-db; Pop-Location

Write-Host "== Front-end" -ForegroundColor Cyan
Push-Location frontend; npm install --no-audit --no-fund --loglevel=error; npm run build | Out-Null; Pop-Location
Write-Host "== Blockchain tooling" -ForegroundColor Cyan
Push-Location blockchain; npm install --no-audit --no-fund --loglevel=error; Pop-Location

if ($Demo) {
  New-Item -ItemType Directory -Force data | Out-Null
  Push-Location backend; & $Py -m app.cli demo-data | Tee-Object -FilePath ..\data\demo-accounts.txt; Pop-Location
  Write-Host "Sample accounts saved to data\demo-accounts.txt (git-ignored)." -ForegroundColor Green
} else {
  Write-Host "`nCreate your administrator:`n  cd backend; .\.venv\Scripts\python.exe -m app.cli create-admin --email you@example.edu --name `"Your Name`"" -ForegroundColor Yellow
}
Write-Host "`nSetup complete. Start everything with:  .\scripts\start.ps1" -ForegroundColor Green
