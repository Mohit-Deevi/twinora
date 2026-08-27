<#
.SYNOPSIS
  Start the Jarvis console (HUD) at http://127.0.0.1:7788

.DESCRIPTION
  1. Ensures Hermes' OpenAI-compatible API server is enabled in %LOCALAPPDATA%\hermes\.env
     (API_SERVER_ENABLED=true + a random API_SERVER_KEY) and restarts the gateway if it had to add them.
  2. Starts interface\server.py with the Hermes venv python (PyYAML available).
  3. Opens the HUD in your default browser (Chrome/Edge recommended for voice).
  Use -NoBrowser to skip step 3. Ctrl+C stops the console; Hermes keeps running.
#>
[CmdletBinding()]
param([switch] $NoBrowser, [int] $Port = 7788)

$ErrorActionPreference = "Stop"
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }
$VenvPython = Join-Path $HermesHome "hermes-agent\venv\Scripts\python.exe"
$HermesExe = Join-Path $HermesHome "hermes-agent\bin\hermes.exe"
$EnvFile = Join-Path $HermesHome ".env"

if (-not (Test-Path $VenvPython)) { throw "Hermes venv python not found at $VenvPython" }

$envText = Get-Content $EnvFile -Raw
if ($envText -notmatch "(?m)^API_SERVER_ENABLED=true") {
  Write-Host "== Enabling Hermes API server (loopback, bearer key)" -ForegroundColor Cyan
  Copy-Item $EnvFile "$EnvFile.bak.$(Get-Date -Format yyyyMMdd_HHmmss)"
  $key = -join ((1..48) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
  Add-Content -Path $EnvFile -Encoding utf8 -Value "`n# Jarvis console: local OpenAI-compatible API server (loopback only)`nAPI_SERVER_ENABLED=true`nAPI_SERVER_KEY=$key"
  & $HermesExe gateway restart
}

$env:JARVIS_PORT = "$Port"
if (-not $NoBrowser) {
  Start-Job -ScriptBlock { param($p) Start-Sleep -Seconds 2; Start-Process "http://127.0.0.1:$p" } -ArgumentList $Port | Out-Null
}
Write-Host "== Jarvis console on http://127.0.0.1:$Port  (Ctrl+C to stop)" -ForegroundColor Cyan
& $VenvPython (Join-Path $PSScriptRoot "server.py")
