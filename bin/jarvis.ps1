<#
.SYNOPSIS
  Owner-side controls for Jarvis (Hermes Agent).

.DESCRIPTION
  jarvis approve <key>     Issue a single-use, 30-minute approval ticket for a Level 2 action.
                           Keys: merge | push-main | release | deploy | email-send | social-post | issue-close
  jarvis approvals         List outstanding tickets and their age.
  jarvis revoke <key>      Delete a ticket.
  jarvis log [n]           Show the last n (default 30) policy-guard decisions.
  jarvis status            Gateway + WhatsApp state at a glance.
  jarvis pause | resume    Emergency stop / lift (wraps `hermes pause` / `hermes resume`).

  Tickets are plain files under %LOCALAPPDATA%\hermes\jarvis\approvals\<key>.ok. Only a human at this
  machine can create them, which is the point: a prompt injection arriving by email or WhatsApp cannot.
#>
param(
  [Parameter(Position = 0)] [string] $Command = "help",
  [Parameter(Position = 1)] [string] $Arg = ""
)

$ErrorActionPreference = "Stop"
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }
$ApprovalsDir = Join-Path $HermesHome "jarvis\approvals"
$PolicyLog = Join-Path $HermesHome "jarvis\policy.log"
$ValidKeys = @("merge", "push-main", "release", "deploy", "email-send", "social-post", "issue-close", "media-spend")
$HermesExe = Join-Path $HermesHome "hermes-agent\bin\hermes.exe"

function Ensure-Dir { if (-not (Test-Path $ApprovalsDir)) { New-Item -ItemType Directory -Force $ApprovalsDir | Out-Null } }

switch ($Command.ToLower()) {
  "approve" {
    if (-not $Arg) { Write-Host "usage: jarvis approve <key> [standing]   keys: $($ValidKeys -join ', ')"; exit 1 }
    if ($ValidKeys -notcontains $Arg) { Write-Host "unknown key '$Arg'. keys: $($ValidKeys -join ', ')"; exit 1 }
    Ensure-Dir
    if ($args -contains "standing" -or $env:JARVIS_STANDING -eq "1") {
      $f = Join-Path $ApprovalsDir "$Arg.standing"
      Set-Content -Path $f -Value ("standing approval by {0} at {1}" -f $env:USERNAME, (Get-Date -Format o)) -Encoding utf8
      Write-Host ("STANDING approval set for '{0}' — Jarvis may do this without asking until you run: jarvis revoke {0}" -f $Arg)
    } else {
      $ticket = Join-Path $ApprovalsDir "$Arg.ok"
      Set-Content -Path $ticket -Value ("approved by {0} at {1}" -f $env:USERNAME, (Get-Date -Format o)) -Encoding utf8
      Write-Host ("Ticket issued: {0}  (valid 30 min, single use). Tell Jarvis to retry." -f $Arg)
    }
  }
  "approvals" {
    Ensure-Dir
    $files = Get-ChildItem $ApprovalsDir -Include "*.ok","*.standing" -Recurse -ErrorAction SilentlyContinue
    if (-not $files) { Write-Host "no outstanding tickets or standing approvals"; break }
    foreach ($f in $files) {
      if ($f.Extension -eq ".standing") { Write-Host ("{0,-12} STANDING (no expiry)" -f $f.BaseName); continue }
      $age = [int]((Get-Date) - $f.LastWriteTime).TotalMinutes
      $state = if ($age -gt 30) { "EXPIRED" } else { "valid" }
      Write-Host ("{0,-12} {1,3} min old  {2}" -f $f.BaseName, $age, $state)
    }
  }
  "revoke" {
    if (-not $Arg) { Write-Host "usage: jarvis revoke <key>"; exit 1 }
    $n = 0
    foreach ($ext in @(".ok", ".standing")) { $p = Join-Path $ApprovalsDir "$Arg$ext"; if (Test-Path $p) { Remove-Item $p -Force; $n++ } }
    if ($n) { Write-Host "revoked $Arg" } else { Write-Host "no ticket or standing approval for $Arg" }
  }
  "log" {
    $n = if ($Arg) { [int]$Arg } else { 30 }
    if (Test-Path $PolicyLog) { Get-Content $PolicyLog -Tail $n } else { Write-Host "no policy decisions logged yet ($PolicyLog)" }
  }
  "status" {
    $stateFile = Join-Path $HermesHome "gateway_state.json"
    if (Test-Path $stateFile) {
      $s = Get-Content $stateFile -Raw | ConvertFrom-Json
      Write-Host ("gateway: {0} (pid {1})" -f $s.gateway_state, $s.pid)
      foreach ($p in $s.platforms.PSObject.Properties) { Write-Host ("  {0,-10} {1}" -f $p.Name, $p.Value.state) }
    } else { Write-Host "gateway_state.json not found — gateway never started?" }
    & $HermesExe cron list
  }
  "pause"  { & $HermesExe pause }
  "resume" { & $HermesExe resume }
  default {
    Get-Help $PSCommandPath -Detailed | Out-String | Write-Host
  }
}
