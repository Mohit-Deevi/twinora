<#
.SYNOPSIS
  Jarvis — Phase 2/3: install persona, policy guard, skills and config into Hermes. Idempotent.

.DESCRIPTION
  Copies from this repo into %LOCALAPPDATA%\hermes:
    hermes\SOUL.md                  → SOUL.md            (existing file backed up)
    hermes\agent-hooks\*.py         → agent-hooks\
    hermes\skills\jarvis\*          → skills\jarvis\
    hermes\config.additions.yaml    → deep-merged into config.yaml (backup first)
  Adds WHATSAPP_HOME_CHANNEL to .env if missing (cron delivery target), registers `jarvis` in your
  PowerShell profile, then restarts the gateway so the hook + persona are live.
#>
[CmdletBinding()]
param([switch] $NoRestart, [switch] $DryRun)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }
$VenvPython = Join-Path $HermesHome "hermes-agent\venv\Scripts\python.exe"
$HermesExe = Join-Path $HermesHome "hermes-agent\bin\hermes.exe"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "   ✓ $msg" -ForegroundColor Green }

if (-not (Test-Path $VenvPython)) { throw "Hermes venv python not found at $VenvPython" }

Step "Persona (SOUL.md)"
$soul = Join-Path $HermesHome "SOUL.md"
if ((Test-Path $soul) -and -not $DryRun) { Copy-Item $soul "$soul.bak.$Stamp"; Ok "backed up existing SOUL.md" }
if (-not $DryRun) { Copy-Item (Join-Path $Root "hermes\SOUL.md") $soul -Force }
Ok "SOUL.md installed"

Step "Owner profile (jarvis\OWNER.md)"
$jarvisDir = Join-Path $HermesHome "jarvis"
if (-not (Test-Path $jarvisDir)) { New-Item -ItemType Directory -Force $jarvisDir | Out-Null }
if (-not $DryRun) { Copy-Item (Join-Path $Root "hermes\jarvis\OWNER.md") (Join-Path $jarvisDir "OWNER.md") -Force }
foreach ($d in @("career", "outreach", "content", "trends", "brand", "approvals")) { $p = Join-Path $jarvisDir $d; if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force $p | Out-Null } }
Ok "OWNER.md + jarvis\{career,outreach,content,trends,brand,approvals}"

Step "Policy guard hook"
$hooksDir = Join-Path $HermesHome "agent-hooks"
if (-not (Test-Path $hooksDir)) { New-Item -ItemType Directory -Force $hooksDir | Out-Null }
if (-not $DryRun) { Copy-Item (Join-Path $Root "hermes\agent-hooks\*.py") $hooksDir -Force }
foreach ($d in @("jarvis", "jarvis\approvals", "jarvis\trends", "jarvis\brand", "jarvis\content")) {
  $p = Join-Path $HermesHome $d; if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force $p | Out-Null }
}
Ok "policy-guard.py → $hooksDir"

Step "Skills"
$skillsDst = Join-Path $HermesHome "skills\jarvis"
if (-not $DryRun) {
  if (-not (Test-Path $skillsDst)) { New-Item -ItemType Directory -Force $skillsDst | Out-Null }
  Copy-Item (Join-Path $Root "hermes\skills\jarvis\*") $skillsDst -Recurse -Force
}
Get-ChildItem (Join-Path $Root "hermes\skills\jarvis") -Directory | ForEach-Object { Ok "skill: $($_.Name)" }

Step "config.yaml additions (approvals, hook, delegation, guardrails, memory, terminal)"
$mergeArgs = @((Join-Path $Root "setup\merge-config.py")); if ($DryRun) { $mergeArgs += "--dry-run" }
& $VenvPython @mergeArgs

Step "WhatsApp home channel (cron delivery target)"
# WhatsApp now addresses chats by LID (e.g. 24863632834715@lid), so the home channel must be
# captured from a real inbound chat rather than typed as a phone number. `/sethome` does that
# and writes it to config.yaml (canonical) — this script only checks it exists.
$envFile = Join-Path $HermesHome ".env"
$cfgText = Get-Content (Join-Path $HermesHome "config.yaml") -Raw
$envText = Get-Content $envFile -Raw
if (($cfgText -match "(?ms)whatsapp:.*?home_channel") -or ($envText -match "(?m)^WHATSAPP_HOME_CHANNEL=")) {
  Ok "home channel already set"
} else {
  Write-Host "   ! not set — open your WhatsApp chat with Hermes and send:  /sethome" -ForegroundColor Yellow
  Write-Host "     (makes that chat the destination for cron jobs and self-initiated messages)"
}

Step "jarvis command in your PowerShell profile"
$profileDir = Split-Path $PROFILE
if (-not (Test-Path $profileDir)) { New-Item -ItemType Directory -Force $profileDir | Out-Null }
$line = "function jarvis { & '$(Join-Path $Root 'bin\jarvis.ps1')' @args }"
if (-not (Test-Path $PROFILE) -or -not ((Get-Content $PROFILE -Raw) -like "*bin\jarvis.ps1*")) {
  if (-not $DryRun) { Add-Content -Path $PROFILE -Value "`n# Jarvis (Hermes) owner controls`n$line" -Encoding utf8 }
  Ok "added to $PROFILE (open a new terminal, then: jarvis approvals)"
} else { Ok "already in profile" }

Step "Hook consent + sanity"
& $HermesExe hooks list
& $HermesExe hooks doctor

if (-not $NoRestart -and -not $DryRun) {
  Step "Restart gateway so persona + hook are live"
  & $HermesExe gateway --accept-hooks restart
}

Write-Host "`nNext: .\setup\03-cron-jobs.ps1 to schedule the morning brief and trend hunter." -ForegroundColor Cyan
