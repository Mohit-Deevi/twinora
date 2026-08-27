<#
.SYNOPSIS
  Register the Growstack router shim as a Windows scheduled task so it starts at login.

.DESCRIPTION
  Jarvis talks to the Growstack LLM router through a local shim on 127.0.0.1:8799 (the router's SSE
  path drops tool_calls, so Hermes' streaming requests are downgraded to non-streaming upstream and
  re-emitted as correct SSE). If the shim is not running, Hermes gets connection-refused on every turn
  and falls back to the Anthropic Max plan.

  This registers "JarvisRouterShim" to run at logon, hidden, with automatic restart on failure.

  Remove with:  .\install-shim-task.ps1 -Uninstall
  Check with :  schtasks /Query /TN JarvisRouterShim /V /FO LIST
#>
[CmdletBinding()]
param([switch] $Uninstall, [int] $Port = 8799)

$ErrorActionPreference = "Stop"
$TaskName = "JarvisRouterShim"
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }
$Python = Join-Path $HermesHome "hermes-agent\venv\Scripts\pythonw.exe"
if (-not (Test-Path $Python)) { $Python = Join-Path $HermesHome "hermes-agent\venv\Scripts\python.exe" }
$Script = Join-Path $PSScriptRoot "router_shim.py"
$LogDir = Join-Path $HermesHome "jarvis"
$Log = Join-Path $LogDir "router-shim.log"

if ($Uninstall) {
  schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
  Write-Host "Removed scheduled task $TaskName" -ForegroundColor Yellow
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'router_shim\.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "  stopped PID $($_.ProcessId)" }
  return
}

if (-not (Test-Path $Script)) { throw "router_shim.py not found at $Script" }
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force $LogDir | Out-Null }

# A tiny launcher keeps stdout/stderr in a log file — schtasks cannot redirect on its own.
$Launcher = Join-Path $PSScriptRoot "run-shim.cmd"
@"
@echo off
"$Python" "$Script" --port $Port --verbose >> "$Log" 2>&1
"@ | Set-Content -Path $Launcher -Encoding ascii

$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$Launcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
              -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Growstack LLM router shim for Jarvis (restores tool_calls in streamed responses)" -Force | Out-Null

Write-Host "Registered scheduled task: $TaskName" -ForegroundColor Green
Write-Host "  runs   : $Python $Script --port $Port"
Write-Host "  log    : $Log"
Write-Host "  starts : at logon, restarts up to 3x on failure"
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$live = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) -ne $null
Write-Host ("  status : " + $(if ($live) { "LISTENING on $Port" } else { "not listening yet - check the log" })) `
    -ForegroundColor $(if ($live) { "Green" } else { "Yellow" })
