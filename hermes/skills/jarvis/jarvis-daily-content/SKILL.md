---
name: jarvis-daily-content
description: "Daily 9am post: trend, copy, image, video, approval."
version: 1.0.0
author: Growstack (tech@growstack.ai), Hermes Agent
license: MIT
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [Content, Social-Media, Daily, Image-Generation, Video, UGC, Virality, Jarvis]
    related_skills: [jarvis-trend-hunter, jarvis-creative-director, jarvis-social-publisher, social-media-content-calendar, meme-generation]
---

# Jarvis Daily Content Skill

The 09:00 routine. Picks today's strongest angle from the trend hunter, produces a complete package (platform
copy, a generated image that passes the vision gate, and a short UGC-style video when the angle benefits), scores
it against a virality checklist, and either publishes under a **standing approval** or delivers it for a one-tap
approval. It never posts something that failed the checklist.

## When to Use

- The daily cron tick ("run today's content").
- "Make today's post" / "post something about X today".

Don't use for: long-form articles, replies/comments, or outreach.

## Prerequisites

- Recent `jarvis-trend-hunter` output (cron notepad / continuity / `session_search`). If absent, run the sweep first.
- Image backend configured (`FAL_KEY` or Nous Tool Gateway); `video_gen` optional (FAL / xAI).
- Brand kit at `jarvis/brand/BRAND.md`; content ledger at `jarvis/content/ledger.jsonl`.
- Publishing path recorded in memory (`publishing_paths`) and, for unattended posting, a standing approval:
  `jarvis approve social-post standing` (revocable any time with `jarvis revoke social-post`).

## How to Run

Cron: `hermes cron create "0 9 * * *" "Load jarvis-daily-content and run today's post." --skill jarvis-daily-content --deliver whatsapp`.
Cron sessions cannot receive approvals, so without a standing approval the skill ends by delivering the package.

## Quick Reference

Virality checklist — all must pass, or rewrite once and re-check:

| # | Check |
|---|---|
| 1 | Hook in the first 8 words states a tension, number, or contrarian claim |
| 2 | One idea; a reader can retell it in one sentence |
| 3 | Specific: a named tool, number, example, or lesson from Growstack's own work |
| 4 | Emotional trigger named (curiosity, relief, pride, fear-of-missing, humour) |
| 5 | Native format: LinkedIn ≤ 1,300 chars with line breaks; X ≤ 280 or a 4–7 post thread; video 30–45 s, captions on |
| 6 | Visual passes `vision_analyze` (on-brand, legible, no artifacts) and is not generic stock-looking |
| 7 | Clear CTA (question, save, follow, link in comments) and 3–5 hashtags |
| 8 | Not a repeat of anything in the ledger within 30 days |

## Procedure

### 1. Choose the angle
Read the latest trend hunter output. Take the highest-scoring item whose topic is not in the ledger (30 days).
If nothing clears the trend hunter's bar, fall back to an evergreen angle from the brand kit's pillars —
and say so in the report.

### 2. Produce the package
Load `jarvis-creative-director`: three concepts → pick one → copy per platform → image prompt → `image_generate`
→ vision gate. For UGC-style video: write a 30–45 s script in first person ("I tried…", "here's what broke…"),
3 beats + CTA, then render it with `jarvis-video-studio` (free: images/colour cards + Edge TTS voice + captions via
ffmpeg — the default on this machine) or, when a `video_gen` key exists, generate clips with `video_generate` and
feed them to the studio as scene inputs. Package lands in `jarvis/content/<date>-<slug>/` with `manifest.json`.

### 3. Run the checklist
Score the package against the Quick Reference. Fix failures once; if it still fails, mark
`status: "needs-human"` and stop at step 5.

### 4. Publish or stage
If `jarvis/approvals/social-post.standing` exists **and** `publishing_paths` is recorded: load
`jarvis-social-publisher` and publish for 09:00 local time (or schedule), read back the live URL, update the
ledger. Otherwise set `status: "awaiting-approval"`.

### 5. Report
One WhatsApp message: the copy (first platform), the image attached, the checklist result as one line, and either
the live URL or "Approve to publish? (`approve post <slug>`)". If nothing was produced, say why in two lines.

## Pitfalls

- Posting a topic that was already covered — always read the ledger first.
- Shipping an image that failed the vision gate because "the copy is ready".
- Long, hedged hooks. If the hook needs a second sentence, it is not a hook.
- Treating a scheduler's "queued" as "published".
- Publishing without a standing approval — the policy guard blocks it; do not look for another route.

## Verification

Done when the package folder exists with manifest + checklist result, and either the ledger has a live URL or the
owner has received the approval request.
