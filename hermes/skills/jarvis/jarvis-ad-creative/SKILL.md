---
name: jarvis-ad-creative
description: "Research a brand, then build a Seedance video ad."
version: 1.0.0
author: Growstack (tech@growstack.ai), Hermes Agent
license: MIT
platforms: [windows, linux, macos]
prerequisites:
  commands: [ffmpeg, ffprobe]
metadata:
  hermes:
    tags: [Ads, Video, Seedance, fal, Creative, Research, UGC, Jarvis]
    related_skills: [jarvis-video-studio, jarvis-creative-director, jarvis-trend-hunter, scrapling, page-agent]
---

# Jarvis Ad Creative Skill

Turns a brand URL into a finished short-form video ad: scrape the site (and LinkedIn, in the owner's own
browser session), mine current ad-hook patterns, write a timed shot list, generate it with **Seedance 2.5**
(up to 30 s, native synchronised audio), then burn sound-off captions and check the result. Every generation
costs real money, so the skill always estimates first, drafts at 480p, and renders 720p only on the owner's go.

## When to Use

- "Scrape <brand>.ai, analyse it, and make a 30-second ad."
- "Make an ad for Turgo using trending hooks."
- The video step of `jarvis-daily-content` when the angle deserves a produced ad rather than a slideshow.

Don't use for: slideshow/UGC-style videos with your own voice-over (that is `jarvis-video-studio`, free), or
static image posts (`jarvis-creative-director`).

## Prerequisites

- `FAL_KEY` in `<HERMES_HOME>/.env` (set). Scripts read it themselves — never print it.
- `scripts/fal_media.py` — images (`nano-banana-pro`, `flux-schnell`, `seedream-4`) and video
  (Seedance 2.5 text-/image-/reference-to-video). Run with the Hermes venv python.
- `scripts/reddit_hooks.py` — Reddit hook mining. Needs `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`
  (free "script" app at reddit.com/prefs/apps); without them it prints `fallback_queries` for `web_search`.
- `ffmpeg` for captions, cover frame and any stitching. `jarvis-video-studio` for caption styling.

## How to Run

```
PY="C:/Users/Growstack/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"
$PY <skill>/scripts/fal_media.py estimate --seconds 30 --resolution 720p
$PY <skill>/scripts/fal_media.py image --prompt "..." --out shot.png --model nano-banana-pro --aspect 9:16
$PY <skill>/scripts/fal_media.py video --prompt "$(cat prompt.txt)" --out ad.mp4 --seconds 30 --resolution 720p --aspect 9:16
$PY <skill>/scripts/reddit_hooks.py --subs advertising marketing PPC --time week --out hooks.json
```

## Quick Reference

**Cost — check before every render** (Seedance 2.5, fal token pricing):

| Length | 480p (draft) | 720p (final) |
|---|---|---|
| 8 s | $1.76 | $3.78 |
| 15 s | $3.31 | $7.10 |
| 30 s | $6.62 | $14.19 |

Reference-to-video *with video references* is ×0.6. Image generation is cents.

**2026 hook benchmarks** — hook must land inside **1.7 s** (LinkedIn median scroll); product UI on screen by
**4 s** (else ~40% drop-off); CTA in the final 5 s; strong hook rate ≥30% Meta / ≥40% TikTok; **80% watch
without sound**, so captions are mandatory; 9:16 gets ~22% lower CPC on professional networks.

**Seedance 2.5 facts:** 4–30 s whole seconds, 480p/720p, 24 fps, aspect `21:9 16:9 4:3 1:1 3:4 9:16`,
`generate_audio` on by default, dialogue **in double quotes** is lip-synced, reference mode takes up to 50 files
(30 images / 10 videos / 10 audio) addressed in the prompt as `[Image1]`, `[Video1]`, `[Audio1]`.

## Procedure

### 1. Research the brand (evidence, not guesses)
Fetch the site with `web_extract` (or `terminal` + curl when a page is JS-heavy) and pull: the exact H1/H2 copy,
meta description, product names, the customer promise, and **every hard number** (leads, %, time saved). Read the
pricing and case-study pages too. Save to `research.json`. For LinkedIn: public pages return HTTP 999 to
scrapers, so open the company page with `browser_navigate` in the owner's logged-in session, screenshot, and read
the last ~10 posts for voice and recurring themes. Never bulk-scrape LinkedIn.

### 2. Mine hooks
Run `reddit_hooks.py` for the current window; if credentials are missing, run its `fallback_queries` through
`web_search` and open the best threads with `web_extract`. Also check the brand's own top-performing posts.
Extract 8–12 hook patterns; keep the 3 that fit this brand's proof points.

### 3. Write the ad
Structure for 30 s: **0–4 s hook** (pain or stat, from step 1's numbers), **4–10 s the shift**, **10–17 s product
in motion**, **17–24 s proof**, **24–30 s CTA**. Use Seedance's **timed shot list** format — one line per range,
each naming camera, subject, action, lighting, the spoken line in double quotes, and concrete audio (clicks,
ring, bass swell). Save as `prompt.txt` next to the output.

**Killing garbled text (learned the hard way, 2026-08-24).** Saying "no on-screen text" is *not enough*. The
model renders letter-shaped noise into anything you call a dashboard, UI, spreadsheet, CRM or document — the
first Turgo cut came back with a wall screen reading "Baxtes Pumites". What actually works:

- Never use the words *dashboard, UI, interface, menu, label, spreadsheet, document, tab* for anything on camera.
  Describe screens as **abstract geometry**: "a single glowing cyan line graph", "smooth bars rising",
  "concentric rings", "a pulsing waveform", "streaming dots", "drifting particles".
- Put an **ABSOLUTE RULE** line at the top of the prompt *and* repeat it as the last line: no letters, numbers,
  words, labels, buttons, logos or captions in any frame.
- Push background screens **out of focus**: "heavily blurred bokeh, only soft coloured glows, no readable content".
- Leave deliberate **negative space** in the final shot so the real logo and CTA can be burned in cleanly.
- All real words — captions, stats, logo, CTA — are added afterwards with ffmpeg, where spelling is under
  your control. Assume every model-rendered glyph is wrong.

### 4. Estimate, then draft
Print the estimate. Render at **480p** first unless the owner explicitly asked for the final. Watch for: does the
hook land by 1.7 s, is the product visible by 4 s, is the voice-over intelligible, does the pacing match the
timed list.

### 5. Render the final and finish it
On the owner's go, re-render at 720p with the same prompt (add `--seed` from the draft's JSON to keep the look).
Then: burn captions with `jarvis-video-studio`'s caption styling (sound-off viewers), overlay the logo if the
brand kit has one, extract a cover frame, and write `manifest.json` (prompt, seed, cost, sources, hooks used).

### 6. Verify
Sample frames at 1 s, 5 s, 15 s and 28 s with `vision_analyze`: on-brand colour, no garbled text, no artifacts,
the product legible. Check `ffprobe` reports an audio stream. Confirm the claims in the voice-over match
`research.json` — never let the model invent a statistic.

### 7. Deliver
One message: the video, the hook line, the three claims used with their source URLs, the cost spent, and the
next option (720p re-render, other aspect ratios, A/B hook variants). Publishing is Level 2 —
`jarvis-social-publisher` with an approval.

## Pitfalls

- Rendering 30 s at 720p to test an idea — that is $14 a try. Draft at 480p.
- Describing screens as "dashboards" or "CRM" — you will get gibberish glyphs. Abstract geometry only; see step 3.
- Shipping before checking frames at 1/5/15/20/28 s for garbled text. Always look before you send it.
- Voice-over claims that are not in `research.json` — invented stats are the fastest way to lose trust.
- Using `fal-ai/bytedance/seedance-2.5/...` — that queues then 404s. The owner prefix is `bytedance/…`.
- Bulk LinkedIn scraping (HTTP 999 / account risk). Owner's session, read-only, or skip it.
- Forgetting captions: most of the audience watches muted.

## Verification

Done when the MP4 exists with an audio stream, sampled frames pass the vision check, every spoken claim traces to
a scraped source, `manifest.json` records prompt + seed + cost, and the owner has the file and the numbers.
