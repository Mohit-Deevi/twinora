#!/usr/bin/env python3
"""Jarvis Video Studio — free short-form video assembly (no GPU, no API key).

Turns a storyboard JSON into an MP4: per scene an image (or a generated colour card) with Ken Burns motion,
an Edge TTS voice-over, styled burned-in captions, optional background music, fades, and a cover frame.
Runs entirely on ffmpeg + edge-tts (both already installed on this machine).

Usage (run with the Hermes venv python so edge_tts is importable):
  python make_video.py storyboard.json --out reel.mp4 [--format vertical|square|landscape]
                       [--voice en-IN-NeerjaNeural] [--music path.mp3] [--music-gain -18]
                       [--no-captions] [--font "C:/Windows/Fonts/bahnschrift.ttf"]

Storyboard JSON:
{
  "title": "Why agents need permission levels",
  "voice": "en-IN-PrabhatNeural",            # optional, overrides --voice
  "brand": {"bg": "#04080F", "accent": "#39D6FF", "text": "#FFFFFF"},   # optional
  "scenes": [
    {"text": "Your AI agent just merged to main. Did you say yes?", "image": "cover.png", "caption": "Did you say yes?"},
    {"text": "Most agents have two settings: off, and reckless.", "image": null},
    {"text": "Here is the fix: four permission levels.", "image": "levels.png", "min_seconds": 4}
  ]
}
`text` is spoken; `caption` (optional) is what appears on screen (defaults to `text`). `image` may be null —
a branded colour card with the caption is generated instead. Output also writes <out>.json with timings.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

FORMATS = {"vertical": (1080, 1920), "square": (1080, 1080), "landscape": (1920, 1080)}
DEFAULT_VOICE = "en-IN-PrabhatNeural"
DEFAULT_FONT = next((p for p in [r"C:/Windows/Fonts/bahnschrift.ttf", r"C:/Windows/Fonts/segoeuib.ttf",
                                 r"C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf"] if Path(p).exists()), None)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if cp.returncode != 0:
        sys.stderr.write(cp.stderr[-3000:])
        raise SystemExit(f"command failed: {' '.join(cmd[:3])} …")
    return cp


def ffprobe_duration(path: Path) -> float:
    cp = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    return float(cp.stdout.strip() or 0)


def hex_to_ass(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def hex_to_ff(hex_color: str) -> str:
    return "0x" + hex_color.lstrip("#")


async def tts(text: str, voice: str, out: Path) -> None:
    import edge_tts  # provided by the Hermes venv ([voice] extras)
    await edge_tts.Communicate(text, voice, rate="+4%").save(str(out))


def ass_time(t: float) -> str:
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def ff_escape_path(p: Path) -> str:
    # ffmpeg filter option paths: forward slashes, escape the drive colon.
    return str(p).replace("\\", "/").replace(":", "\\:")


def build_captions(scenes: list[dict], timings: list[tuple[float, float]], size: tuple[int, int], brand: dict, font: str | None) -> str:
    w, h = size
    fontsize = int(w * 0.062) if w < h else int(h * 0.052)
    margin_v = int(h * 0.16)
    fontname = Path(font).stem if font else "Arial"
    header = textwrap.dedent(f"""\
        [Script Info]
        ScriptType: v4.00+
        PlayResX: {w}
        PlayResY: {h}
        WrapStyle: 0

        [V4+ Styles]
        Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
        Style: Cap,{fontname},{fontsize},{hex_to_ass(brand.get('text', '#FFFFFF'))},&H000000FF,&H00101010,&HA0000000,-1,0,0,0,100,100,0.5,0,1,3,1,2,80,80,{margin_v},1
        Style: Accent,{fontname},{int(fontsize * 0.7)},{hex_to_ass(brand.get('accent', '#39D6FF'))},&H000000FF,&H00101010,&HA0000000,-1,0,0,0,100,100,2,0,1,2,0,8,80,80,{int(h * 0.06)},1

        [Events]
        Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        """)
    events = []
    title = scenes[0].get("title") or ""
    for sc, (start, end) in zip(scenes, timings):
        cap = (sc.get("caption") or sc.get("text") or "").strip()
        # chunk long captions into ~2-line pieces spread across the scene
        words = cap.split()
        chunks = [" ".join(words[i:i + 7]) for i in range(0, len(words), 7)] or [cap]
        per = (end - start) / max(1, len(chunks))
        for i, ch in enumerate(chunks):
            s, e = start + i * per, start + (i + 1) * per
            events.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,{{\\fad(120,120)}}{ch}")
    if title:
        events.append(f"Dialogue: 1,{ass_time(0)},{ass_time(timings[-1][1])},Accent,,0,0,0,,{title.upper()}")
    return header + "\n".join(events) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("storyboard")
    ap.add_argument("--out", default="reel.mp4")
    ap.add_argument("--format", choices=FORMATS, default="vertical")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--music", default=None)
    ap.add_argument("--music-gain", type=float, default=-18.0)
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg/ffprobe not on PATH")
    sb = json.loads(Path(args.storyboard).read_text(encoding="utf-8"))
    scenes = sb["scenes"]
    if not scenes:
        raise SystemExit("storyboard has no scenes")
    voice = args.voice or sb.get("voice") or DEFAULT_VOICE
    brand = sb.get("brand") or {}
    bg, accent = brand.get("bg", "#04080F"), brand.get("accent", "#39D6FF")
    W, H = FORMATS[args.format]
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    base = Path(args.storyboard).resolve().parent
    scenes[0].setdefault("title", sb.get("title", ""))

    with tempfile.TemporaryDirectory(prefix="jarvis-video-") as td:
        tmp = Path(td)
        clips, timings, t = [], [], 0.0
        for i, sc in enumerate(scenes):
            # 1) voice
            audio = tmp / f"v{i}.mp3"
            asyncio.run(tts(sc["text"], voice, audio))
            dur = max(float(sc.get("min_seconds", 0)), ffprobe_duration(audio) + 0.45)
            # 2) visual: image with Ken Burns, or a branded colour card
            img = sc.get("image")
            img_path = (base / img) if img and not Path(img).is_absolute() else (Path(img) if img else None)
            clip = tmp / f"c{i}.mp4"
            frames = int(dur * args.fps)
            if img_path and img_path.exists():
                zoom_dir = "zoom+0.0008" if i % 2 == 0 else "if(eq(on,1),1.12,zoom-0.0008)"
                vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                      f"zoompan=z='{zoom_dir}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={args.fps},"
                      f"format=yuv420p")
                run(["ffmpeg", "-y", "-loop", "1", "-i", str(img_path), "-i", str(audio), "-vf", vf, "-t", f"{dur:.3f}",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-shortest", str(clip)])
            else:
                # colour card with a soft accent vignette; caption is burned later
                vf = (f"color=c={hex_to_ff(bg)}:s={W}x{H}:r={args.fps},"
                      f"drawbox=x=0:y=ih-ih*0.012:w=iw:h=ih*0.012:color={hex_to_ff(accent)}@0.9:t=fill,"
                      f"vignette=PI/4,format=yuv420p")
                run(["ffmpeg", "-y", "-f", "lavfi", "-i", vf, "-i", str(audio), "-t", f"{dur:.3f}",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-shortest", str(clip)])
            clips.append(clip); timings.append((t, t + dur)); t += dur

        # 3) concat
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
        joined = tmp / "joined.mp4"
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])

        # 4) captions + fades (+ music)
        filters = []
        if not args.no_captions:
            ass = tmp / "caps.ass"
            ass.write_text(build_captions(scenes, timings, (W, H), brand, args.font), encoding="utf-8")
            fontsdir = ff_escape_path(Path(args.font).parent) if args.font else None
            filters.append(f"subtitles='{ff_escape_path(ass)}'" + (f":fontsdir='{fontsdir}'" if fontsdir else ""))
        total = timings[-1][1]
        filters.append(f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(0, total - 0.5):.2f}:d=0.5")
        cmd = ["ffmpeg", "-y", "-i", str(joined)]
        if args.music and Path(args.music).exists():
            cmd += ["-stream_loop", "-1", "-i", str(Path(args.music).resolve())]
            cmd += ["-filter_complex", f"[0:v]{','.join(filters)}[v];[1:a]volume={args.music_gain}dB,afade=t=out:st={max(0, total - 2):.2f}:d=2[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=2[a]",
                    "-map", "[v]", "-map", "[a]"]
        else:
            cmd += ["-vf", ",".join(filters), "-c:a", "copy"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)]
        run(cmd)
        # 5) cover frame
        cover = out.with_suffix(".cover.jpg")
        run(["ffmpeg", "-y", "-ss", "0.6", "-i", str(out), "-frames:v", "1", "-q:v", "2", str(cover)])

    meta = {"out": str(out), "cover": str(cover), "format": args.format, "size": [W, H], "voice": voice,
            "duration_s": round(timings[-1][1], 2), "scenes": [{"start": round(s, 2), "end": round(e, 2), "text": sc["text"]} for sc, (s, e) in zip(scenes, timings)]}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
