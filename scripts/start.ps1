<#
 Start the platform (Windows PowerShell).   .\scripts\start.ps1 [-NoChain] [-Port 8000]
 Runs the local Hardhat chain (auto-deploys the audit contract) and the API serving the built UI on ONE port.
 Press Ctrl+C to stop; the blockchain window is closed automatically.
#>
param([switch]$NoChain, [int]$Port = 8000)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "Run .\scripts\setup.ps1 first." }
if (-not (Test-Path frontend\dist\index.html)) { Push-Location frontend; npm run build | Out-Null; Pop-Location }
New-Item -ItemType Directory -Force data\logs | Out-Null
$chainProc = $null
function Rpc($method, $params) {
  $body = @{ jsonrpc = "2.0"; method = $method; params = $params; id = 1 } | ConvertTo-Json -Compress
  Invoke-RestMethod -Uri http://127.0.0.1:8545 -Method Post -Body $body -ContentType "application/json" -TimeoutSec 3
}
try {
  if (-not $NoChain) {
    Write-Host "Starting local blockchain (Hardhat)..." -ForegroundColor Cyan
    $chainProc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c","cd /d `"$Root\blockchain`" && npx hardhat node > `"$Root\data\logs\hardhat.log`" 2>&1" -WindowStyle Minimized -PassThru
    $up = $false
    for ($i = 0; $i -lt 60 -and -not $up; $i++) { try { Rpc "eth_chainId" @() | Out-Null; $up = $true } catch { Start-Sleep 1 } }
    if (-not $up) { throw "The local blockchain did not start. See data\logs\hardhat.log" }
    $addr = (& $Py scripts\envset.py get CONTRACT_ADDRESS).Trim()
    $hasCode = $false
    if ($addr) { try { $hasCode = (Rpc "eth_getCode" @($addr, "latest")).result -ne "0x" } catch { } }
    if (-not $hasCode) {
      Push-Location backend
      $line = & $Py -m app.cli deploy-contract | Where-Object { $_ -like "CONTRACT_ADDRESS=*" }
      Pop-Location
      if ($line) { $new = $line.Substring("CONTRACT_ADDRESS=".Length); & $Py scripts\envset.py set CONTRACT_ADDRESS $new; Write-Host "Audit contract deployed at $new" -ForegroundColor Green }
    }
  } else { $env:BLOCKCHAIN_ENABLED = "false" }   # this run only; .env is untouched
  Write-Host "Starting the platform on http://localhost:$Port  (Ctrl+C to stop)" -ForegroundColor Green
  Start-Job { Start-Sleep 4; Start-Process "http://localhost:$using:Port" } | Out-Null
  Push-Location backend
  & $Py -m uvicorn app.main:app --host 127.0.0.1 --port $Port
  Pop-Location
} finally {
  if ($chainProc) { Write-Host "Stopping blockchain..."; taskkill /PID $chainProc.Id /T /F 2>$null | Out-Null }
}
