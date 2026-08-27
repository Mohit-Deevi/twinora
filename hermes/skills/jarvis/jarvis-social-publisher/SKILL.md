---
name: jarvis-social-publisher
description: "Schedule or publish approved posts with an audit trail."
version: 1.0.0
author: Growstack (tech@growstack.ai), Hermes Agent
license: MIT
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [Social-Media, LinkedIn, X, Publishing, Scheduling, Jarvis]
    related_skills: [jarvis-creative-director, xurl, browser]
---

# Jarvis Social Publisher Skill

Publishes (or schedules) a content package that the owner approved, reads back the live post, and records it in
a ledger. Publishing is **Level 2**: it needs the owner's explicit yes for this package in the current
conversation and the machine-side ticket `jarvis approve social-post` (single use, 30 min). Without both, the
skill only stages.

## When to Use

- "Approve post <slug>" / "Publish the agents post on LinkedIn at 9am."
- Weekly scheduling of an approved batch.

Don't use for: drafting (creative director) or replying to comments (separate task, ask first).

## Prerequisites

Pick one publishing path per platform and record it in memory (`publishing_paths`):

| Platform | Recommended path on Windows native | Notes |
|---|---|---|
| X | `xurl` CLI (official X API) | Bundled skill is gated to linux/macos; run it via the Docker terminal backend or WSL, or install `npm i -g @xdevplatform/xurl` and test. Owner does `xurl auth oauth2` outside the agent. |
| LinkedIn | A scheduler with an API (Typefully / Buffer / Postiz / Publer) | LinkedIn's own API needs app approval; a scheduler is faster and keeps a human preview step. |
| Either | `browser_*` tools (Playwright) | Fallback; fragile; always screenshot-verify. |

Credentials live in `.env` or the tool's own store — never in chat.

## How to Run

Called after `jarvis-creative-director` produced a package at `jarvis/content/<date>-<slug>/`.

## Quick Reference

`stage → confirm approval → publish/schedule → read back → ledger → report`. Any step without evidence stops
the chain.

## Procedure

### 1. Stage
Read `manifest.json` and `post.md`. Confirm `status` is `draft` or `approved`, files exist, and the copy fits
the platform limits (recount characters). Show the final text and image filename to the owner once more if
anything changed since approval.

### 2. Confirm approval (both keys)
- Conversation: the owner's message must name this package ("approve post <slug>" or equivalent).
- Machine: the `terminal` publish command will be blocked by the policy guard until the owner has run
  `jarvis approve social-post`. If blocked, relay the exact instruction and wait. Never route around it.
Scheduling for later is also Level 2 — same rules.

### 3. Publish or schedule
Use the platform's recorded path. For X via `xurl`: upload media first, then create the post with the media id;
for threads, post sequentially replying to the previous id. For schedulers: create the post with the scheduled
time in the owner's timezone (Asia/Kolkata unless memory says otherwise). For the browser fallback: navigate,
compose, attach, **screenshot before clicking Post**, then post.

### 4. Read back
Fetch the live post (API GET or open the URL) and capture: post id/URL, timestamp, platform, first 80 chars.
If the read-back fails, report "publish attempted, not confirmed" — do not guess.

### 5. Ledger
Append one JSON line to `jarvis/content/ledger.jsonl`:
`{slug, platform, url, posted_at, scheduled_for, topic, angle, files}`; update the package `manifest.json`
`status` to `published` or `scheduled`.

### 6. Report
One message: platform, URL, time, and what comes next (e.g. "I'll check engagement in the Friday brief").

## Pitfalls

- Publishing from a package that was edited after approval — re-show it.
- Posting the same package twice after a timeout — check the ledger first.
- Using `--verbose` or printing auth output from CLIs (leaks tokens).
- Browser automation without a pre-click screenshot.
- Treating a scheduler's "queued" as "published".

## Verification

Done when the post URL was fetched back live, the ledger line exists, and the manifest status changed.
