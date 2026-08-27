#!/usr/bin/env python3
"""fal.ai media generation for Jarvis — images (FLUX / Nano Banana Pro / Seedream) and
video (Seedance 2.5, the current ByteDance model: up to 30s, native synchronised audio).

Verified working endpoints (probed live 2026-08-24):
  video  bytedance/seedance-2.5/text-to-video       prompt -> video+audio
         bytedance/seedance-2.5/image-to-video      image_url (+ optional end_image_url)
         bytedance/seedance-2.5/reference-to-video  image_urls / video_urls / audio_urls, addressed as [Image1] ...
  image  fal-ai/flux/schnell                        fast + cheap drafts
         fal-ai/nano-banana-pro                     Google Gemini 3 Pro Image — best text rendering
         fal-ai/bytedance/seedream/v4/text-to-image high realism, good for product/UGC frames

NOTE the video owner prefix is `bytedance/…`, NOT `fal-ai/bytedance/…` — the latter queues but 404s on fetch.

Cost (fal, 2026): Seedance 2.5 is billed (h*w*seconds*24)/1024 tokens at $0.0214/1k
  ~= $0.2205 / second at 480p 16:9 and ~$0.4730 / second at 720p 16:9.
  Reference-to-video with video refs is multiplied by 0.6.
  Always draft at 480p; only render the approved cut at 720p.

Usage:
  python fal_media.py image --prompt "..." --out shot1.png [--model nano-banana-pro] [--aspect 9:16]
  python fal_media.py video --prompt "..." --out clip1.mp4 [--seconds 8] [--resolution 480p]
                            [--aspect 9:16] [--image start.png] [--end-image end.png] [--no-audio] [--seed 42]
  python fal_media.py video --prompt "[Image1] walks in ..." --ref-images a.png b.png --ref-audio vo.mp3 --out clip.mp4
  python fal_media.py estimate --seconds 30 --resolution 720p --aspect 9:16

Reads FAL_KEY from the environment or from %LOCALAPPDATA%\\hermes\\.env (never printed).
Local files passed to --image/--ref-* are uploaded to fal's CDN first.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

QUEUE = "https://queue.fal.run"
UPLOAD_INIT = "https://rest.alpha.fal.ai/storage/upload/initiate"

# Video tiers, cheapest first. `budget` is the DEFAULT: 1080p for about a tenth of Seedance 2.5's price.
# Pay for `best` only when the shot genuinely needs native lip-synced dialogue and multi-shot cutting.
VIDEO_TIERS = {
    "budget": {  # LTX-2.3 Fast — cheapest per second and still 1080p. No native voice: pair with free Edge TTS.
        "t2v": "fal-ai/ltx-2.3-22b/text-to-video",
        "i2v": "fal-ai/ltx-2.3-22b/image-to-video",
        "usd_per_s": {"480p": 0.04, "720p": 0.04, "1080p": 0.04},
        "audio": False,
        "note": "cheapest; add voice-over free with jarvis-video-studio (Edge TTS)",
    },
    "mini": {  # Seedance 2.0 Mini — native audio at ~1/6 of 2.5's price.
        "t2v": "bytedance/seedance-2.0/mini/text-to-video",
        "i2v": "bytedance/seedance-2.0/mini/image-to-video",
        "usd_per_s": {"480p": 0.0721, "720p": 0.1547},
        "audio": True,
        "note": "native audio, good default when you want generated voice",
    },
    "fast": {  # Seedance 2.0 Fast — better motion than mini, still under half of 2.5.
        "t2v": "bytedance/seedance-2.0/fast/text-to-video",
        "i2v": "bytedance/seedance-2.0/fast/image-to-video",
        "usd_per_s": {"480p": 0.1129, "720p": 0.2419},
        "audio": True,
        "note": "4-15s only",
    },
    "best": {  # Seedance 2.5 — 30s single-pass, multi-shot, lip-sync. Most expensive by far.
        "t2v": "bytedance/seedance-2.5/text-to-video",
        "i2v": "bytedance/seedance-2.5/image-to-video",
        "ref": "bytedance/seedance-2.5/reference-to-video",
        "usd_per_s": {"480p": 0.2205, "720p": 0.4730},
        "audio": True,
        "note": "only for the final approved cut",
    },
}
VIDEO_MODELS = {
    "seedance-2.5": "bytedance/seedance-2.5/text-to-video",
    "seedance-2.5-i2v": "bytedance/seedance-2.5/image-to-video",
    "seedance-2.5-ref": "bytedance/seedance-2.5/reference-to-video",
}
IMAGE_MODELS = {
    "flux-schnell": "fal-ai/flux/schnell",
    "nano-banana-pro": "fal-ai/nano-banana-pro",
    "seedream-4": "fal-ai/bytedance/seedream/v4/text-to-image",
}
# $ per second of output, 16:9 reference (fal token formula, $0.0214 / 1k tokens)
RATE = {"480p": 0.2205, "720p": 0.4730}
ASPECTS = ["auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]


def log(msg: str) -> None:
    print(msg, flush=True)


def fal_key() -> str:
    k = os.environ.get("FAL_KEY", "").strip()
    if k:
        return k
    home = os.environ.get("HERMES_HOME") or str(
        pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home())) / "hermes"
    )
    envp = pathlib.Path(home) / ".env"
    if envp.exists():
        m = re.search(r"(?m)^FAL_KEY=(.*)$", envp.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1).strip()
    sys.exit("FAL_KEY not found (set it in the environment or in <HERMES_HOME>/.env)")


def _req(url: str, key: str, payload=None, method=None, raw=False, extra_headers=None):
    headers = {"Authorization": f"Key {key}"}
    if payload is not None and not raw:
        headers["Content-Type"] = "application/json"
    headers.update(extra_headers or {})
    data = payload if raw else (json.dumps(payload).encode() if payload is not None else None)
    r = urllib.request.Request(url, data=data, headers=headers, method=method or ("POST" if data else "GET"))
    with urllib.request.urlopen(r, timeout=300) as resp:
        body = resp.read()
    return json.loads(body) if body[:1] in (b"{", b"[") else body


def upload(path: pathlib.Path, key: str) -> str:
    """Upload a local file to fal storage and return its public URL."""
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    init = _req(UPLOAD_INIT, key, {"content_type": ctype, "file_name": path.name})
    put = urllib.request.Request(init["upload_url"], data=path.read_bytes(),
                                 headers={"Content-Type": ctype}, method="PUT")
    with urllib.request.urlopen(put, timeout=300):
        pass
    return init["file_url"]


def as_url(value: str, key: str) -> str:
    if value.startswith(("http://", "https://", "data:")):
        return value
    p = pathlib.Path(value)
    if not p.exists():
        sys.exit(f"file not found: {value}")
    url = upload(p, key)
    log(f"  uploaded {p.name}")
    return url


def submit_and_wait(endpoint: str, payload: dict, key: str, label: str = "", timeout_s: int = 1800) -> dict:
    try:
        q = _req(f"{QUEUE}/{endpoint}", key, payload)
    except urllib.error.HTTPError as e:
        sys.exit(f"submit failed ({e.code}): {e.read().decode()[:400]}")
    rid = q["request_id"]
    log(f"  queued {label or endpoint} [{rid[:8]}]")
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout_s:
        st = _req(q["status_url"], key)
        s = st.get("status")
        if s != last:
            log(f"    {int(time.time() - t0):>4}s {s}")
            last = s
        if s == "COMPLETED":
            return _req(q["response_url"], key)
        if s in ("FAILED", "ERROR"):
            sys.exit(f"generation failed: {json.dumps(st)[:400]}")
        time.sleep(6)
    sys.exit("timed out waiting for fal")


def estimate(seconds: float, resolution: str, aspect: str, video_refs: bool = False, tier: str = "best") -> float:
    rates = VIDEO_TIERS.get(tier, VIDEO_TIERS["best"])["usd_per_s"]
    per_s = rates.get(resolution) or list(rates.values())[-1]
    return round(per_s * float(seconds) * (0.6 if video_refs else 1.0), 3)


def price_table(seconds: int = 30) -> list[dict]:
    rows = [{"tier": "free (jarvis-video-studio)", "model": "ffmpeg + Edge TTS", "usd": 0.0,
             "audio": "free TTS voice", "note": "images/screen recordings + captions"}]
    for name, t in VIDEO_TIERS.items():
        res = "1080p" if name == "budget" else "720p"
        rows.append({"tier": name, "model": t["t2v"], "resolution": res,
                     "usd": round(t["usd_per_s"][res] * seconds, 2),
                     "audio": "native" if t["audio"] else "add free TTS", "note": t["note"]})
    return rows


def cmd_image(a, key: str) -> None:
    ep = IMAGE_MODELS.get(a.model, a.model)
    payload = {"prompt": a.prompt}
    if a.aspect and "flux" in ep:
        payload["image_size"] = {"9:16": "portrait_16_9", "16:9": "landscape_16_9", "1:1": "square_hd"}.get(a.aspect, "square_hd")
    elif a.aspect:
        payload["aspect_ratio"] = a.aspect
    if a.seed is not None:
        payload["seed"] = a.seed
    res = submit_and_wait(ep, payload, key, label=f"image/{a.model}")
    imgs = res.get("images") or []
    if not imgs:
        sys.exit(f"no image returned: {json.dumps(res)[:300]}")
    url = imgs[0]["url"]
    out = pathlib.Path(a.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, out)
    meta = {"kind": "image", "model": ep, "out": str(out), "url": url, "prompt": a.prompt}
    out.with_suffix(out.suffix + ".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))


def spend_guard(est_usd: float, force: bool) -> None:
    """Refuse to bill the owner's fal account without an explicit, human-issued approval.

    A render is allowed only when the owner has issued a ticket (`jarvis approve media-spend`, single use)
    or a standing approval, or passes --i-approve-the-cost interactively. Every allowed spend is appended
    to <HERMES_HOME>/jarvis/spend.log so the total is always auditable.
    """
    home = pathlib.Path(os.environ.get("HERMES_HOME") or
                        (pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home())) / "hermes"))
    appr = home / "jarvis" / "approvals"
    ticket, standing = appr / "media-spend.ok", appr / "media-spend.standing"
    ok = force or standing.exists()
    if not ok and ticket.exists():
        ticket.unlink()          # single use
        ok = True
    if not ok:
        sys.exit(
            f"BLOCKED: this render would cost about ${est_usd} of the owner's fal credit.\n"
            f"No approval found. Ask the owner for one of:\n"
            f"  jarvis approve media-spend            (one render, 30 min)\n"
            f"  jarvis approve media-spend standing   (until revoked)\n"
            f"then run the same command again. Do not route around this."
        )
    try:
        (home / "jarvis").mkdir(parents=True, exist_ok=True)
        with (home / "jarvis" / "spend.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} est_usd={est_usd} approved={'standing' if standing.exists() else ('flag' if force else 'ticket')}\n")
    except Exception:
        pass


def cmd_video(a, key: str) -> None:
    ref_images = [as_url(x, key) for x in (a.ref_images or [])]
    ref_videos = [as_url(x, key) for x in (a.ref_videos or [])]
    ref_audio = [as_url(x, key) for x in (a.ref_audio or [])]
    payload = {
        "prompt": a.prompt,
        "duration": str(int(a.seconds)),
        "resolution": a.resolution,
        "aspect_ratio": a.aspect,
        "generate_audio": not a.no_audio,
    }
    if a.seed is not None:
        payload["seed"] = a.seed

    tier = VIDEO_TIERS[a.tier]
    if ref_images or ref_videos or ref_audio:
        if "ref" not in tier:
            sys.exit(f"tier '{a.tier}' has no reference-to-video mode; use --tier best or drop the references")
        ep = tier["ref"]
        if ref_images:
            payload["image_urls"] = ref_images
        if ref_videos:
            payload["video_urls"] = ref_videos
        if ref_audio:
            payload["audio_urls"] = ref_audio
        mode = "reference-to-video"
    elif a.image:
        ep = tier["i2v"]
        payload["image_url"] = as_url(a.image, key)
        if a.end_image:
            payload["end_image_url"] = as_url(a.end_image, key)
        mode = "image-to-video"
    else:
        ep = tier["t2v"]
        mode = "text-to-video"
    if not tier["audio"]:
        payload.pop("generate_audio", None)

    est = estimate(a.seconds, a.resolution, a.aspect, video_refs=bool(ref_videos), tier=a.tier)
    log(f"  tier={a.tier} mode={mode} {a.seconds}s {a.resolution} {a.aspect} "
        f"audio={'native' if tier['audio'] and not a.no_audio else 'none (add free TTS)'} est=${est}")
    spend_guard(est, a.i_approve_the_cost)
    res = submit_and_wait(ep, payload, key, label=f"video/{mode}")
    v = res.get("video") or {}
    if not v.get("url"):
        sys.exit(f"no video returned: {json.dumps(res)[:300]}")
    out = pathlib.Path(a.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(v["url"], out)
    meta = {"kind": "video", "model": ep, "mode": mode, "out": str(out), "url": v["url"],
            "bytes": v.get("file_size"), "seconds": a.seconds, "resolution": a.resolution,
            "aspect_ratio": a.aspect, "audio": not a.no_audio, "seed": res.get("seed"),
            "est_cost_usd": est, "prompt": a.prompt}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description="fal.ai image + Seedance 2.5 video for Jarvis")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("image")
    i.add_argument("--prompt", required=True)
    i.add_argument("--out", required=True)
    i.add_argument("--model", default="nano-banana-pro", help="|".join(IMAGE_MODELS) + " or a raw fal id")
    i.add_argument("--aspect", default="1:1", choices=ASPECTS)
    i.add_argument("--seed", type=int)

    v = sub.add_parser("video")
    v.add_argument("--prompt", required=True)
    v.add_argument("--out", required=True)
    v.add_argument("--seconds", type=int, default=8, help="4-30 (whole seconds)")
    v.add_argument("--tier", default="budget", choices=list(VIDEO_TIERS),
                   help="budget=LTX-2.3 Fast 1080p (cheapest) | mini | fast | best=Seedance 2.5 (priciest)")
    v.add_argument("--resolution", default="480p", choices=["480p", "720p", "1080p"])
    v.add_argument("--aspect", default="9:16", choices=ASPECTS)
    v.add_argument("--image", help="start frame -> image-to-video")
    v.add_argument("--end-image", dest="end_image")
    v.add_argument("--ref-images", nargs="*", dest="ref_images")
    v.add_argument("--ref-videos", nargs="*", dest="ref_videos")
    v.add_argument("--ref-audio", nargs="*", dest="ref_audio")
    v.add_argument("--no-audio", action="store_true")
    v.add_argument("--seed", type=int)
    v.add_argument("--i-approve-the-cost", action="store_true",
                   help="owner-only: bypass the approval-ticket check for this one render")

    e = sub.add_parser("estimate")
    e.add_argument("--seconds", type=float, default=30)
    e.add_argument("--resolution", default="720p", choices=["480p", "720p", "1080p"])
    e.add_argument("--aspect", default="9:16", choices=ASPECTS)
    e.add_argument("--video-refs", action="store_true")
    e.add_argument("--tier", default=None, choices=list(VIDEO_TIERS))
    e.add_argument("--inr", type=float, default=88.0, help="USD->INR rate for the printed table")

    a = ap.parse_args()
    if a.cmd == "estimate":
        if a.tier:
            usd = estimate(a.seconds, a.resolution, a.aspect, a.video_refs, a.tier)
            print(json.dumps({"tier": a.tier, "seconds": a.seconds, "resolution": a.resolution,
                              "est_cost_usd": usd, "est_cost_inr": round(usd * a.inr)}, indent=2))
        else:
            rows = price_table(int(a.seconds))
            print(f"cost of a {int(a.seconds)}s video (USD->INR at {a.inr:g}):\n")
            print(f"  {'tier':<28}{'res':<8}{'USD':>8}{'INR':>8}   audio")
            for r in rows:
                print(f"  {r['tier']:<28}{r.get('resolution','-'):<8}{r['usd']:>8.2f}{round(r['usd']*a.inr):>8}   {r['audio']}")
        return
    key = fal_key()
    if a.cmd == "image":
        cmd_image(a, key)
    elif a.cmd == "video":
        if not 4 <= a.seconds <= 30:
            sys.exit("--seconds must be between 4 and 30")
        cmd_video(a, key)


if __name__ == "__main__":
    main()
