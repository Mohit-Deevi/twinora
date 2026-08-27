---
name: jarvis-creative-director
description: "Turn a post brief into on-brand visuals and copy."
version: 1.0.0
author: Growstack (tech@growstack.ai), Hermes Agent
license: MIT
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [Content, Copywriting, Image-Generation, Video, Brand, Jarvis]
    related_skills: [jarvis-trend-hunter, jarvis-social-publisher, humanizer, design-md]
---

# Jarvis Creative Director Skill

Takes a topic + angle (usually from the trend hunter or the owner) and produces a complete, approved-ready
content package: platform-specific copy, one or more generated images checked for quality, an optional short
video, and a manifest. Everything is Level 0 (drafting); publishing belongs to `jarvis-social-publisher`.

## When to Use

- "Make a LinkedIn post about autonomous agents" / "Turn trend #2 into a post with an image."
- Weekly content batch from the trend hunter's top picks.

Don't use for: long-form articles (draft those as documents) or publishing (separate skill).

## Prerequisites

- Image generation configured: `FAL_KEY` **or** Nous Tool Gateway (`hermes tools` → Image) **or** BFL key.
  `hermes doctor` must show `image_gen` available.
- Optional video: `video_gen` toolset (FAL / xAI) — skip gracefully if unavailable.
- `vision_analyze` for quality checks (available by default with a vision-capable model).
- Brand kit at `C:/Users/Growstack/AppData/Local/hermes/jarvis/brand/BRAND.md` — palette (hex), fonts, logo
  path, voice rules, banned words, example posts the owner liked. Created on first run via `clarify`.

## How to Run

Invoked by Jarvis after the trend hunter, or directly. Output lands in
`C:/Users/Growstack/AppData/Local/hermes/jarvis/content/<YYYY-MM-DD>-<slug>/`.

## Quick Reference

| Platform | Copy shape | Image |
|---|---|---|
| LinkedIn | Hook line (≤ 12 words) → 3–6 short paragraphs → 1 question or CTA → 3–5 hashtags | 1200×1200 or 1200×627 |
| X | 1 post (≤ 280 chars) or thread of 4–7; hook first, evidence middle, takeaway last | 1600×900 |
| Short video | 30–45s script: hook 3s, 3 beats, CTA | 1080×1920 |

## Procedure

### 1. Load the brand kit
Read `BRAND.md`. If missing, ask the owner once for palette, voice (3 adjectives + 3 anti-adjectives), logo,
and 2 example posts; write the file. Never invent a brand.

### 2. Concept — three options, pick one
From the topic + angle produce three concepts in one message: the claim, the proof point, the visual metaphor.
Choose the strongest unless the owner asked to pick; note the other two in the manifest.

### 3. Write the copy
Per target platform, following the Quick Reference shapes and the brand voice. Rules: specific over clever,
no emoji soup, no "game-changer", numbers and named examples where true, one idea per post. Run the text through
`humanizer` if loaded. Cite sources in a final line when the post states facts.

### 4. Generate the visual
Write an explicit image prompt: subject, composition, style that matches the brand, palette hex values,
"no text" (render text yourself later or keep it minimal), aspect ratio. Call `image_generate`. Save to the
package folder with a descriptive filename.

### 5. Quality gate (mandatory)
`vision_analyze` each image against a checklist: on-brand colours, no garbled text, no extra limbs/artifacts,
subject matches the concept, safe for a professional feed. Regenerate up to 2 times with a corrected prompt;
if still failing, ship copy-only and say why.

### 6. Optional video
If the angle benefits from motion: script it (Quick Reference) and render with `jarvis-video-studio` — free,
no GPU: your generated images (or colour cards) + Edge TTS voice-over + burned-in captions. If a `video_gen`
backend is configured, generate clips with `video_generate` and pass them as scene inputs. Run the same quality
gate on three sampled frames. Otherwise skip — do not force video.

### 7. Package
Write `post.md` (copy per platform), images, optional video, and `manifest.json`:
`{topic, angle, platforms, files, sources, concept_alternatives, brand_version, created_at, status: "draft"}`.

### 8. Hand off
Reply with the copy inline, attach the image, and say: "Approve to publish? (`approve post <slug>`)". Publishing
is handled by `jarvis-social-publisher` after the owner's yes.

## Pitfalls

- Generating images before the concept is chosen — wasted credits.
- Text rendered inside generated images (usually garbled). Keep text out or verify with vision.
- Off-palette visuals; always pass hex values in the prompt and check them.
- Copy that could be anyone's. If the angle is missing, go back to the trend hunter.
- Treating a skipped video as a failure — it's a choice.

## Verification

Done when the package folder exists with `post.md`, at least one image that passed the vision checklist (or an
explicit copy-only note), and a manifest with `status: "draft"`.
