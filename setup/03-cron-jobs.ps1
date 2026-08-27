<#
.SYNOPSIS
  Jarvis — Phase 5: the scheduled automations. Re-runnable (skips jobs whose name already exists).

.DESCRIPTION
  Creates Hermes cron jobs. Delivery goes to the WhatsApp home channel — set it once by sending
  /sethome in your WhatsApp chat with Hermes. Edit times to taste; all times are this machine's local time.
  Cron runs are capped at Level 1 by SOUL.md and by approvals.cron_mode: deny.
#>
[CmdletBinding()]
param([string] $Deliver = "whatsapp")

$ErrorActionPreference = "Stop"
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }
$HermesExe = Join-Path $HermesHome "hermes-agent\bin\hermes.exe"
$Work = Join-Path $env:USERPROFILE "jarvis-work"

$existing = (& $HermesExe cron list 2>$null | Out-String)
function New-Job($name, $schedule, $prompt, [string[]] $extra) {
  if ($existing -like "*$name*") { Write-Host "   = exists: $name" -ForegroundColor DarkGray; return }
  & $HermesExe cron create $schedule $prompt --name $name @extra
  Write-Host "   + created: $name" -ForegroundColor Green
}

Write-Host "== Creating Jarvis cron jobs (deliver: $Deliver)" -ForegroundColor Cyan

# 06:45 Mon–Sat — trend hunter, with continuity so it dedupes against yesterday. Output stays local; the brief reads it.
New-Job "Trend hunter" "45 6 * * 1-6" `
  "Load the jarvis-trend-hunter skill and run the daily sweep. Output the ranked list (or [SILENT])." `
  @("--skill", "jarvis-trend-hunter", "--continuity", "--deliver", "local", "--workdir", $Work)

# 07:30 Mon–Sat — morning brief to WhatsApp.
New-Job "Morning brief" "30 7 * * 1-6" `
  "Load the jarvis-morning-brief skill and produce the brief for the last 24 hours. Include the latest trend hunter output if present." `
  @("--skill", "jarvis-morning-brief", "--deliver", $Deliver, "--workdir", $Work)

# 09:00 every day — the daily post: today's best angle → copy + image (+ video) → virality checklist →
# publish under a standing approval (jarvis approve social-post standing) or deliver for one-tap approval.
New-Job "Daily content 9am" "0 9 * * *" `
  "Load the jarvis-daily-content skill and run today's post. If no trend hunter output exists for today, run jarvis-trend-hunter first." `
  @("--skill", "jarvis-daily-content", "--skill", "jarvis-creative-director", "--continuity", "--deliver", $Deliver, "--workdir", $Work)

# 10:00 Mon–Fri — outreach follow-ups and reply tracking (sending needs a ticket or standing approval).
New-Job "Outreach follow-ups" "0 10 * * 1-5" `
  "Load the jarvis-outreach skill. Check tracked threads for replies, process due follow-ups within the approval rules, and report counts. If no ICP contract exists yet, respond [SILENT]." `
  @("--skill", "jarvis-outreach", "--deliver", $Deliver)

# Every 2h during the working day — CI shepherd for Jarvis-opened PRs (Level 1: comments only).
New-Job "PR shepherd" "0 9-19/2 * * 1-6" `
  "For every open PR authored by me (gh pr list --author @me --state open), check CI with gh pr checks. Summarise failures with gh run view --log-failed, comment a short diagnosis on the PR if the failure is caused by the PR, and report in one message. Never merge. If all green or no PRs, respond [SILENT]." `
  @("--skill", "github-pr-workflow", "--deliver", $Deliver, "--workdir", (Join-Path $Work "repos"))

# 18:30 Mon–Fri — evening wrap: what happened, what is waiting on the owner.
New-Job "Evening wrap" "30 18 * * 1-5" `
  "Give a short end-of-day wrap: tasks completed today, PRs opened/merged, replies still awaiting my approval, content drafts ready to publish, anything blocked. Use session_search for today's sessions. Keep it to one phone screen. If nothing happened, respond [SILENT]." `
  @("--deliver", $Deliver)

# Monday 08:15 — weekly competitor/peer digest (material news only).
New-Job "Weekly competitor digest" "15 8 * * 1" `
  "Load the competitor-news-monitor skill. If no watch contract exists under HERMES_HOME/competitor-watches, create one from the trend contract's competitor list and then run the tick. Deliver the cited digest or [SILENT]." `
  @("--skill", "competitor-news-monitor", "--continuity", "--deliver", $Deliver)

# Sunday 20:00 — content plan for the week from the last 7 days of trend output (drafts only, Level 0).
New-Job "Weekly content plan" "0 20 * * 0" `
  "Read the last 7 trend hunter outputs (cron notepad / session_search). Propose 3 posts for the coming week with topic, angle, platform and suggested day. Do not generate images yet. Ask which to produce." `
  @("--skill", "jarvis-trend-hunter", "--deliver", $Deliver)

Write-Host "`nAll jobs:" -ForegroundColor Cyan
& $HermesExe cron list
Write-Host "`nTest one now:  hermes cron run `"Morning brief`"   (runs on the next scheduler tick)" -ForegroundColor DarkGray
