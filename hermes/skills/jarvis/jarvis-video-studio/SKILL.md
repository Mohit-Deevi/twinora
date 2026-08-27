---
name: jarvis-video-studio
description: "Free short-form video: images + voice + captions via ffmpeg."
version: 1.0.0
author: Growstack (tech@growstack.ai), Hermes Agent
license: MIT
platforms: [windows, linux, macos]
prerequisites:
  commands: [ffmpeg, ffprobe]
metadata:
  hermes:
    tags: [Video, Short-Form, Reels, UGC, TTS, ffmpeg, Free, Jarvis]
    related_skills: [jarvis-creative-director, jarvis-daily-content, short-video-agent-kit, tiktok, ugc, comfyui]
---

# Jarvis Video Studio Skill

Produces publish-ready short-form videos with zero GPU and zero API cost: a storyboard of scenes → Edge TTS
voice-over → images with Ken Burns motion (or branded colour cards) → styled burned-in captions → fades, optional
music, cover frame. It is the default video path on this machine (no NVIDIA GPU, so open models like Wan 2.2 /
LTX-Video cannot run locally). When a `video_gen` backend key exists (FAL / DeepInfra / xAI), generated clips can
be used as scene inputs instead of stills.

## When to Use

- "Make a 30-second reel about …" / "turn today's post into a video".
- UGC-style talking-point videos, listicles, hook → 3 beats → CTA, product walkthrough slideshows.
- The video step of `jarvis-daily-content` and `jarvis-creative-director`.

Don't use for: photoreal text-to-video (needs a hosted model via `video_generate`), or anything longer than ~90 s.

## Prerequisites

- `ffmpeg`/`ffprobe` on PATH (installed via winget on this machine) with `subtitles`, `zoompan`, `libx264`.
- Edge TTS (`edge_tts` in the Hermes venv — installed with the `[voice]` extras). Free, no key.
- Optional images from `image_generate` (needs FAL_KEY or Nous Tool Gateway) — otherwise colour cards are used.
- Script: `scripts/make_video.py` (stdlib + ffmpeg + edge_tts). Run with the Hermes venv python:
  `C:/Users/Growstack/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe`.

## How to Run

```
<venv-python> <skill-dir>/scripts/make_video.py storyboard.json --out jarvis/content/<slug>/reel.mp4 --format vertical --voice en-IN-PrabhatNeural
```
Formats: `vertical` 1080×1920 (Reels/Shorts/TikTok), `square` 1080×1080 (LinkedIn/IG feed), `landscape` 1920×1080.
Voices worth using: `en-IN-PrabhatNeural` (male, Indian English), `en-IN-NeerjaNeural` (female),
`en-GB-RyanNeural`, `en-US-GuyNeural`. Add `--music path.mp3 --music-gain -18` for a bed; `--no-captions` to skip text.

## Quick Reference

Storyboard shape (6–8 scenes for 30–45 s; each scene ≤ 18 spoken words):

| Field | Meaning |
|---|---|
| `title` | small accent label shown top-centre for the whole video |
| `voice`, `brand.bg/accent/text` | optional overrides (hex colours) |
| `scenes[].text` | spoken line (also the caption unless `caption` is set) |
| `scenes[].caption` | on-screen text (shorter, punchier than the spoken line) |
| `scenes[].image` | PNG/JPG path (relative to the storyboard) or `null` for a colour card |
| `scenes[].min_seconds` | hold a scene longer than the voice-over |

## Procedure

### 1. Script it
From the angle/copy: hook (≤ 8 words, scene 1), 3 beats (one idea each), proof (a number or example), CTA.
Write spoken lines in first person, conversational; write captions shorter than the spoken line.

### 2. Visuals
Per scene either generate an image with `image_generate` (prompt: subject, brand hex colours, "no text",
aspect matching the format) and pass the vision gate from `jarvis-creative-director`, or use `null` for a
clean colour card. Mixed is fine — cards for text-heavy beats, images for the hook and the proof.

### 3. Render
Write `storyboard.json` in the package folder; run the script; read the printed JSON (`duration_s`, cover path).
Watch for: duration 25–60 s, captions readable (≤ 2 lines), no scene under 2 s.

### 4. Quality gate
Extract 3 frames (`ffmpeg -ss … -frames:v 1`) and run `vision_analyze`: captions legible, no clipped text,
brand colours, cover frame strong. Re-render with shorter captions or a different image if it fails.

### 5. Hand off
Attach `reel.mp4` + cover to the content package manifest (`files`), set `video: true`, and return the path.
Publishing is `jarvis-social-publisher`'s job (Level 2).

## Pitfalls

- Long spoken lines → captions chunk awkwardly. Keep ≤ 18 words per scene.
- Stock-looking or text-bearing generated images; keep text out of images (captions carry the text).
- Forgetting `--format square` for LinkedIn feed.
- Running with system Python (no `edge_tts`) — use the Hermes venv interpreter.
- Music louder than voice; keep `--music-gain` at −16 to −22 dB.

## Verification

Done when `reel.mp4`, `reel.cover.jpg` and `reel.json` exist, the duration is within target, and three sampled
frames passed the vision check.
