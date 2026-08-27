---
name: jarvis-trend-hunter
description: "Find and score trends worth a post in your niche."
version: 1.0.0
author: Growstack (tech@growstack.ai), Hermes Agent
license: MIT
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [Trends, Research, Social-Media, Content-Strategy, Monitoring, Jarvis]
    related_skills: [competitor-news-monitor, blogwatcher, grounded-citations, jarvis-creative-director]
---

# Jarvis Trend Hunter Skill

Continuously scans the owner's niche for topics with momentum, scores them against a written niche contract,
dedupes against what was already covered, and outputs a short ranked list with a post angle for each. It
recommends; it never publishes. Runs daily from cron with `--continuity` so each run knows what it said before.

## When to Use

- Daily cron tick ("run the trend hunter").
- "What should I post about this week?" / "Anything trending in AI agents today?"
- As the first step before `jarvis-creative-director`.

Don't use for: one-off company research (use `web_search` + `web_extract`) or plain feed reading (`blogwatcher`).

## Prerequisites

- Web search configured (`web` toolset; default `ddgs` works, a paid backend such as Exa/Firecrawl or the Nous
  Tool Gateway gives better recall).
- Optional: `x_search` (xAI) for X/Twitter velocity; Playwright Chromium for `browser_*` (Google Trends, Product Hunt).
- Niche contract at `C:/Users/Growstack/AppData/Local/hermes/jarvis/trends/contract.json` — created on first run.

## How to Run

Cron (created by setup): daily 06:45 with `--continuity` and `--deliver local` (the morning brief reads it);
optionally also `--deliver whatsapp` for a standalone digest.

## Quick Reference

Score 0–5 on each: **velocity** (growing this week), **relevance** (matches contract topics/audience),
**novelty** (not covered by us in the last 30 days), **angle** (can the owner add a genuinely useful take),
**evidence** (≥2 independent sources). Surface only items with total ≥ 17 and evidence ≥ 3.

## Procedure

### 1. Load or create the niche contract
If `contract.json` is missing, ask the owner with `clarify` (one message): core topics (3–6), audiences,
competitors/peers to watch (5–10), preferred platforms (LinkedIn/X/YouTube), hard exclusions, and the owner's
point of view in one sentence. Write the file. On later runs just read it.

### 2. Sweep — several ways, not one
Run in parallel with `delegate_task` where it helps:
- `web_search`: 6–10 queries from the contract topics with recency words ("this week", "launch", "announces").
- Peer/competitor newsrooms and blogs via `web_extract` (reuse `competitor-news-monitor` source hierarchy).
- Communities: Hacker News front page and "new", Product Hunt today, GitHub trending (via `web_extract`).
- `x_search` for the same topics, if configured — velocity signal only, not sole evidence.
- Feeds the owner already follows (`blogwatcher`).
Record for each candidate: topic, one-line what-happened, date, canonical URL(s), source type.

### 3. Deduplicate by underlying event
Collapse syndications, rewrites and reposts into one event; keep the best primary source plus one corroboration.

### 4. Remove what we already covered
Check continuity context (previous runs' output) and `session_search` for the topic in the last 30 days; also
the content ledger at `jarvis/content/ledger.jsonl` if present. Drop repeats unless there is a genuinely new
development — then mark it "update".

### 5. Score and rank
Apply the Quick Reference rubric. For each survivor write **the angle**: the specific, useful take the owner
could offer (a contrarian view, a how-to, a comparison, a lesson from their own work). Generic "X is exciting"
angles are not angles — discard.

### 6. Output
Top 5 maximum:
```
1. <topic> — <what happened, 1 line> (score 21/25)
   Angle: <the take>    Format: <LinkedIn post | X thread | short video>
   Evidence: <url> · <url>
```
Then a one-line "watching" list (items below threshold but rising). If nothing clears the bar, output
`[SILENT]` plus the watching list in the notepad.

### 7. Persist
Append the run's surfaced topics and timestamp to the cron notepad (or the continuity output) so tomorrow's run
dedupes correctly.

## Pitfalls

- One search engine, one query — recall collapses. Sweep multiple modalities.
- Treating a single viral post as a trend. Require independent corroboration.
- Re-surfacing the same topic daily because the dedupe step was skipped.
- Recommending topics the owner excluded in the contract.
- Writing angles that any account could post.

## Verification

Done when every surfaced item has ≥2 evidence URLs that were actually opened, a rubric score, an angle, and the
run's topics are persisted for the next tick.
