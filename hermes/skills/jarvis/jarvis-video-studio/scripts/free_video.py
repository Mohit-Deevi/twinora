#!/usr/bin/env python3
"""FREE AI video generation — no GPU on this machine, no API key, no credit card.

Runs open-source video models on Hugging Face **ZeroGPU** (shared H200/A100, time-sliced) through the
public Gradio APIs of community Spaces. Verified working from this laptop on 2026-08-24: a 3-second
704x512 clip generated in 12 seconds at zero cost with no account at all.

**Get a free Hugging Face token — it is the single biggest upgrade.** Without one you are last in the
ZeroGPU queue and the good Spaces reject you with "No GPU was available after 60s". With a free account
(no card) you get priority and can use LTX-2.3 at 1024x1536 — higher resolution than a $14 Seedance render.

  1. https://huggingface.co/join            (free, no credit card)
  2. https://huggingface.co/settings/tokens  -> New token -> type "Read"
  3. add to <HERMES_HOME>/.env:   HF_TOKEN=hf_xxxxxxxx

Usage:
  python free_video.py --list
  python free_video.py --prompt "..." --out clip.mp4 [--model ltx-2.3] [--seconds 4] [--vertical]
  python free_video.py --prompt "..." --image start.png --out clip.mp4 --model ltx-turbo --camera "Zoom In"

Models (all free): ltx-2.3 (best quality, needs token) | ltx-turbo (camera moves + audio input)
                   hunyuan-1.5 | ltx-distilled (works with no account at all, lower quality)

These clips have no sound. Add a free voice-over and captions with make_video.py in this same skill.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import sys
import time

MODELS = {
    "ltx-2.3": {
        "space": "Lightricks/LTX-2-3", "api": "/generate_video",
        "size": (1024, 1536), "needs_token": True,
        "desc": "LTX-2.3 — best free quality, up to 1024x1536",
    },
    "ltx-turbo": {
        "space": "alexnasa/ltx-2-TURBO", "api": "/generate_video",
        "size": (768, 512), "needs_token": True,
        "desc": "LTX-2 Turbo — camera LoRAs (Zoom In/Slide/Static) + optional audio track",
    },
    "hunyuan-1.5": {
        "space": "multimodalart/Hunyuan-Video-1-5", "api": "/generate",
        "size": (0, 0), "needs_token": True,
        "desc": "HunyuanVideo 1.5",
    },
    "ltx-distilled": {
        "space": "Lightricks/ltx-video-distilled", "api": "/text_to_video",
        "size": (704, 512), "needs_token": False,
        "desc": "LTX distilled — works with NO account; lower quality, good for abstract B-roll",
    },
}
NEG = "worst quality, inconsistent motion, blurry, jittery, distorted, text, letters, words, watermark, logo"


def hermes_home() -> pathlib.Path:
    if os.environ.get("HERMES_HOME"):
        return pathlib.Path(os.environ["HERMES_HOME"])
    if os.name == "nt":
        return pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home())) / "hermes"
    return pathlib.Path.home() / ".hermes"


def hf_token() -> str | None:
    v = os.environ.get("HF_TOKEN", "").strip()
    if v:
        return v
    p = hermes_home() / ".env"
    if p.exists():
        m = re.search(r"(?m)^HF_TOKEN=(.*)$", p.read_text(encoding="utf-8", errors="replace"))
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def extract_path(result) -> str | None:
    """Pull a local file path out of any Gradio response shape.

    Spaces return wildly different shapes: a bare path string, {"video": path}, {"path": path},
    a gallery list of {"image": {"path": ...}}, or nested lists of any of those. Walk it all.
    """
    seen = 0

    def walk(node):
        nonlocal seen
        seen += 1
        if seen > 200 or node is None:
            return None
        if isinstance(node, str):
            return node if any(node.lower().endswith(e) for e in
                               (".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg", ".webp")) else None
        if isinstance(node, dict):
            for k in ("video", "path", "image", "url", "name"):
                if k in node:
                    got = walk(node[k])
                    if got:
                        return got
            for v in node.values():
                got = walk(v)
                if got:
                    return got
            return None
        if isinstance(node, (list, tuple)):
            for v in node:
                got = walk(v)
                if got:
                    return got
        return None

    return walk(result)


def save(result, out: pathlib.Path) -> pathlib.Path:
    src = extract_path(result)
    if not src:
        raise RuntimeError(f"no media in response: {str(result)[:250]}")
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Free AI video via Hugging Face ZeroGPU (no local GPU needed)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--prompt")
    ap.add_argument("--out", default="clip.mp4")
    ap.add_argument("--model", default="ltx-2.3", choices=list(MODELS))
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--image", help="start frame -> image-to-video")
    ap.add_argument("--vertical", action="store_true", help="9:16 instead of the model default")
    ap.add_argument("--camera", default="No LoRA", help="ltx-turbo only: Static|Zoom In|Zoom Out|Slide Left|...")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    if a.list:
        tok = hf_token()
        state = "set" if tok else "NOT SET  <- get a free one (see this script's header)"
        print(f"HF_TOKEN: {state}\n")
        for k, m in MODELS.items():
            need = "free token" if m["needs_token"] else "no account needed"
            print(f"  {k:<15}{m['desc']}\n  {'':<15}({need}, space: {m['space']})")
        return
    if not a.prompt:
        sys.exit("--prompt is required (or use --list)")

    try:
        from gradio_client import Client
    except ImportError:
        sys.exit("gradio_client missing: uv pip install gradio_client --python <hermes venv python>")

    m = MODELS[a.model]
    tok = hf_token()
    if m["needs_token"] and not tok:
        print(f"! {a.model} needs a free HF token or ZeroGPU will refuse you. Falling back to ltx-distilled.\n"
              f"  Get one free (no card) at https://huggingface.co/settings/tokens and put HF_TOKEN=... in .env")
        a.model, m = "ltx-distilled", MODELS["ltx-distilled"]

    w, h = m["size"]
    if a.vertical and w and h:
        w, h = min(w, h), max(w, h)

    print(f"model={a.model} space={m['space']} size={w}x{h} seconds={a.seconds} token={'yes' if tok else 'no'}")
    t0 = time.time()
    client = Client(m["space"], token=tok, verbose=False)

    try:
        if a.model == "ltx-distilled":
            r = client.predict(
                prompt=a.prompt, negative_prompt=NEG,
                input_image_filepath=a.image, input_video_filepath=None,
                height_ui=h or 512, width_ui=w or 704,
                mode="image-to-video" if a.image else "text-to-video",
                duration_ui=a.seconds, ui_frames_to_use=9,
                seed_ui=a.seed or 42, randomize_seed=not a.seed,
                ui_guidance_scale=1, improve_texture_flag=True,
                api_name=m["api"])
        elif a.model == "ltx-2.3":
            r = client.predict(
                input_image=a.image, prompt=a.prompt, duration=a.seconds,
                enhance_prompt=False, seed=a.seed or 10, randomize_seed=not a.seed,
                height=h, width=w, api_name=m["api"])
        elif a.model == "ltx-turbo":
            r = client.predict(
                first_frame=a.image, end_frame=None, prompt=a.prompt, input_video=None,
                generation_mode="Image-to-Video" if a.image else "Text-to-Video",
                enhance_prompt=False, seed=a.seed or 10, randomize_seed=not a.seed,
                height=h, width=w, camera_lora=a.camera, audio_path=None, api_name=m["api"])
        else:  # hunyuan-1.5
            r = client.predict(
                input_image=a.image, prompt=a.prompt, length=int(a.seconds * 15) or 61,
                steps=6, shift=5.0, seed=a.seed or -1, guidance=1.0, do_rewrite=True,
                api_name=m["api"])
    except Exception as e:
        msg = str(e)
        if "No GPU was available" in msg:
            sys.exit(f"ZeroGPU queue was full after {int(time.time()-t0)}s. "
                     f"A free HF token gives you priority — see this script's header. Or retry in a minute.")
        sys.exit(f"generation failed ({type(e).__name__}): {msg[:300]}")

    out = save(r, pathlib.Path(a.out).resolve())
    meta = {"model": a.model, "space": m["space"], "out": str(out), "bytes": out.stat().st_size,
            "seconds_requested": a.seconds, "took_s": int(time.time() - t0), "cost_usd": 0.0,
            "prompt": a.prompt, "note": "no audio - add free voice-over with make_video.py"}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
