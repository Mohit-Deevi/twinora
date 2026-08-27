<#
.SYNOPSIS
  Jarvis — Phase 1 foundation for this Windows machine. Idempotent; safe to re-run.

.DESCRIPTION
  Installs/verifies the tools Hermes needs to act as your orchestrator:
    - GitHub CLI (gh)                       via winget
    - Claude Code CLI (claude)              via npm (global)  ← the coding worker
    - Playwright Chromium                   for Hermes browser_* tools
    - Work directories                      C:\Users\<you>\jarvis-work\repos
    - PATH repairs                          npm global bin, hermes bin
  It does NOT log you in anywhere — interactive auth steps are printed at the end.
#>
[CmdletBinding()]
param([switch] $SkipPlaywright)

$ErrorActionPreference = "Continue"
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }
$HermesAgent = Join-Path $HermesHome "hermes-agent"
$NpmGlobal = Join-Path $env:APPDATA "npm"

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "   ✓ $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "   ! $msg" -ForegroundColor Yellow }
function Has($cmd) { try { Get-Command $cmd -ErrorAction Stop | Out-Null; $true } catch { $false } }
function Add-UserPath($dir) {
  $current = [Environment]::GetEnvironmentVariable("Path", "User")
  if (($current -split ";") -notcontains $dir) {
    [Environment]::SetEnvironmentVariable("Path", "$current;$dir", "User")
    Ok "added to user PATH: $dir (open a new terminal to pick it up)"
  }
  if (($env:Path -split ";") -notcontains $dir) { $env:Path += ";$dir" }
}

Step "Hermes install"
if (Test-Path (Join-Path $HermesAgent "bin\hermes.exe")) { Ok "hermes found at $HermesAgent" } else { Warn "hermes.exe not found under $HermesAgent — re-run the Hermes installer first"; exit 1 }
Add-UserPath (Join-Path $HermesAgent "bin")

Step "GitHub CLI"
if (Has "gh") { Ok "gh already installed: $((gh --version | Select-Object -First 1))" }
else {
  winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements
  Add-UserPath "C:\Program Files\GitHub CLI"
  if (Has "gh") { Ok "gh installed" } else { Warn "gh not on PATH yet — open a new terminal and re-run" }
}

Step "Claude Code CLI (npm global)"
if (-not (Test-Path $NpmGlobal)) { New-Item -ItemType Directory -Force $NpmGlobal | Out-Null; Ok "created $NpmGlobal" }
Add-UserPath $NpmGlobal
if (Has "claude") { Ok "claude already installed: $(claude --version 2>$null)" }
else {
  npm install -g @anthropic-ai/claude-code
  if (Has "claude") { Ok "claude installed: $(claude --version 2>$null)" } else { Warn "claude not on PATH yet — open a new terminal and check 'claude --version'" }
}

Step "Work directories"
$work = Join-Path $env:USERPROFILE "jarvis-work"
foreach ($d in @($work, (Join-Path $work "repos"), (Join-Path $work "inbox"))) { if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force $d | Out-Null } }
Ok "workspace: $work"

Step "Jarvis state directories in HERMES_HOME"
foreach ($d in @("jarvis", "jarvis\approvals", "jarvis\trends", "jarvis\brand", "jarvis\content", "agent-hooks")) {
  $p = Join-Path $HermesHome $d; if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force $p | Out-Null }
}
Ok "created under $HermesHome"

if (-not $SkipPlaywright) {
  Step "Playwright Chromium (browser tools)"
  Push-Location $HermesAgent
  npx --yes playwright install chromium
  Pop-Location
  Ok "playwright chromium install attempted (hermes doctor will confirm)"
}

Step "Done. Manual steps (interactive, do these now):"
Write-Host @"
  1. Open a NEW terminal (PATH changed).
  2. gh auth login                       → GitHub (choose HTTPS, authenticate via browser)
  3. claude                              → first run: log in (Max/Pro OAuth) — or set ANTHROPIC_API_KEY in %LOCALAPPDATA%\hermes\.env
     claude auth status --text           → confirm
  4. hermes model                        → pick a strong orchestrator model (see runbook Phase 1)
  5. hermes fallback                     → add a second provider so Jarvis survives outages
  6. hermes doctor                       → everything green except optional extras
  7. .\setup\02-install-into-hermes.ps1  → persona, policy guard, skills, config
"@
