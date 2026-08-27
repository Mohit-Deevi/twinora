#!/usr/bin/env python3
"""Cinematic still images with automatic fallback across every available backend.

Tries, in order, and uses the first that works:

  1. **Hugging Face ZeroGPU** (free) — current-generation models: Z-Image Turbo, FLUX.2, Qwen-Image-2512.
     Needs a free HF token or the anonymous quota runs out in a handful of images.
     Free, no credit card: https://huggingface.co/join -> https://huggingface.co/settings/tokens (type: Read)
     Then put  HF_TOKEN=hf_xxxx  in <HERMES_HOME>/.env
  2. **fal.ai** (paid, cents) — nano-banana-pro / seedream-4. Only used with --allow-paid.
  3. **Pollinations** (free, no key at all) — last resort. Caps at 576x1024 and looks soft; fine for
     placeholders, not for a finished ad.

Measured on this machine 2026-08-24: HF anonymous quota died after ~6 images with
"You have exceeded your ZeroGPU quota ... Try again in 23:36". Every good free model sits behind it.
A free token is the difference between 6 images a day and a usable pipeline.

Usage:
  python free_image.py --prompt "..." --out shot.png [--aspect 9:16] [--model z-image] [--allow-paid]
  python free_image.py --check
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
import urllib.parse
import urllib.request

HF_MODELS = {
    "z-image":  {"space": "Tongyi-MAI/Z-Image-Turbo", "api": "/generate", "max": 2048},
    "flux2":    {"space": "black-forest-labs/FLUX.2-klein-9B", "api": "/generate", "max": 1024},
    "flux2-dev": {"space": "black-forest-labs/FLUX.2-dev", "api": "/generate", "max": 1024},
    "qwen":     {"space": "Qwen/Qwen-Image-2512", "api": "/infer", "max": 1536},
}
ASPECT_WH = {"9:16": (1152, 2048), "16:9": (2048, 1152), "1:1": (1536, 1536), "3:4": (1296, 1728), "4:3": (1728, 1296)}
Z_RES = {"9:16": "1152x2048 ( 9:16 )", "16:9": "2048x1152 ( 16:9 )", "1:1": "1536x1536 ( 1:1 )",
         "3:4": "1296x1728 ( 3:4 )", "4:3": "1728x1296 ( 4:3 )"}


def hermes_home() -> pathlib.Path:
    if os.environ.get("HERMES_HOME"):
        return pathlib.Path(os.environ["HERMES_HOME"])
    if os.name == "nt":
        return pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home())) / "hermes"
    return pathlib.Path.home() / ".hermes"


def env_value(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    p = hermes_home() / ".env"
    if p.exists():
        m = re.search(rf"(?m)^{name}=(.*)$", p.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1).strip()
    return ""


def media_path(node, depth=0):
    """Find a local image path in any Gradio response shape."""
    if depth > 8 or node is None:
        return None
    if isinstance(node, str):
        return node if node.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) else None
    if isinstance(node, dict):
        for k in ("path", "image", "url", "name"):
            if k in node:
                got = media_path(node[k], depth + 1)
                if got:
                    return got
        for v in node.values():
            got = media_path(v, depth + 1)
            if got:
                return got
        return None
    if isinstance(node, (list, tuple)):
        for v in node:
            got = media_path(v, depth + 1)
            if got:
                return got
    return None


def try_hf(prompt: str, aspect: str, model: str, out: pathlib.Path) -> str | None:
    try:
        from gradio_client import Client
    except ImportError:
        return None
    tok = env_value("HF_TOKEN") or None
    order = [model] + [m for m in HF_MODELS if m != model]
    for name in order:
        cfg = HF_MODELS[name]
        w, h = ASPECT_WH.get(aspect, ASPECT_WH["9:16"])
        cap = cfg["max"]
        if max(w, h) > cap:                      # keep the ratio, fit inside the model's cap
            scale = cap / max(w, h)
            w, h = int(w * scale) // 16 * 16, int(h * scale) // 16 * 16
        try:
            c = Client(cfg["space"], token=tok, verbose=False)
            if name == "z-image":
                r = c.predict(prompt=prompt, resolution=Z_RES.get(aspect, Z_RES["9:16"]), seed=42,
                              steps=8, shift=3.0, random_seed=True, gallery_images=[], api_name=cfg["api"])
            elif name == "qwen":
                r = c.predict(prompt=prompt, seed=0, randomize_seed=True, aspect_ratio=aspect,
                              guidance_scale=4.0, num_inference_steps=16, prompt_enhance=False, api_name=cfg["api"])
            else:
                r = c.predict(prompt=prompt, input_images=[], mode_choice="Distilled (4 steps)", seed=0,
                              randomize_seed=True, width=w, height=h, num_inference_steps=4,
                              guidance_scale=1.0, prompt_upsampling=False, api_name=cfg["api"])
            p = media_path(r)
            if p:
                shutil.copy(p, out)
                return f"huggingface:{name}"
        except Exception as e:
            msg = str(e)
            if "quota" in msg.lower() or "runs limit" in msg.lower():
                print(f"  hf/{name}: quota exhausted{' (no HF_TOKEN set!)' if not tok else ''}")
            else:
                print(f"  hf/{name}: {type(e).__name__} {msg[:90]}")
    return None


def try_fal(prompt: str, aspect: str, out: pathlib.Path) -> str | None:
    key = env_value("FAL_KEY")
    if not key:
        return None
    ep = "fal-ai/nano-banana-pro"
    body = json.dumps({"prompt": prompt, "aspect_ratio": aspect}).encode()
    hdr = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(f"https://queue.fal.run/{ep}", data=body, headers=hdr, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            q = json.load(r)
        t0 = time.time()
        while time.time() - t0 < 300:
            with urllib.request.urlopen(urllib.request.Request(q["status_url"], headers=hdr), timeout=60) as r:
                st = json.load(r)
            if st.get("status") == "COMPLETED":
                with urllib.request.urlopen(urllib.request.Request(q["response_url"], headers=hdr), timeout=60) as r:
                    res = json.load(r)
                url = (res.get("images") or [{}])[0].get("url")
                if url:
                    urllib.request.urlretrieve(url, out)
                    return f"fal:{ep} (~$0.04)"
                return None
            if st.get("status") in ("FAILED", "ERROR"):
                return None
            time.sleep(4)
    except Exception as e:
        print(f"  fal: {type(e).__name__} {str(e)[:90]}")
    return None


def try_pollinations(prompt: str, aspect: str, out: pathlib.Path) -> str | None:
    w, h = {"9:16": (576, 1024), "16:9": (1024, 576), "1:1": (1024, 1024)}.get(aspect, (576, 1024))
    url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt[:900])}"
           f"?width={w}&height={h}&model=flux&nologo=true&enhance=true")
    try:
        urllib.request.urlretrieve(url, out)
        if out.stat().st_size > 5000:
            return "pollinations (free, low-res ~576x1024)"
    except Exception as e:
        print(f"  pollinations: {type(e).__name__}")
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt")
    ap.add_argument("--out", default="shot.png")
    ap.add_argument("--aspect", default="9:16", choices=list(ASPECT_WH))
    ap.add_argument("--model", default="z-image", choices=list(HF_MODELS))
    ap.add_argument("--allow-paid", action="store_true", help="permit the fal fallback (~$0.04/image)")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    if a.check:
        print(f"HF_TOKEN : {'set' if env_value('HF_TOKEN') else 'NOT SET  <- free at huggingface.co/settings/tokens'}")
        print(f"FAL_KEY  : {'set (paid fallback available with --allow-paid)' if env_value('FAL_KEY') else 'not set'}")
        print(f"models   : {', '.join(HF_MODELS)}")
        return
    if not a.prompt:
        sys.exit("--prompt required (or --check)")

    out = pathlib.Path(a.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"generating {out.name} [{a.aspect}]")
    src = try_hf(a.prompt, a.aspect, a.model, out)
    if not src and a.allow_paid:
        src = try_fal(a.prompt, a.aspect, out)
    if not src:
        src = try_pollinations(a.prompt, a.aspect, out)
    if not src:
        sys.exit("all backends failed — add HF_TOKEN (free) or pass --allow-paid")
    print(json.dumps({"out": str(out), "backend": src, "bytes": out.stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
