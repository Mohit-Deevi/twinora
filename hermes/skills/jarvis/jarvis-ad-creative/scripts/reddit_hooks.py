#!/usr/bin/env python3
"""Mine Reddit for high-performing ad hooks, angles and audio/format trends.

Reddit blocks anonymous JSON (HTTP 403 as of 2026-08) and blocks reader proxies, so this uses the
**official OAuth API** — free, ToS-compliant, 100 req/min. Set up once (2 minutes):

  1. https://www.reddit.com/prefs/apps  ->  "create another app..."
  2. type: **script**,  name: jarvis,  redirect uri: http://localhost:8080
  3. copy the client id (under the app name) and the secret
  4. add to <HERMES_HOME>/.env:
         REDDIT_CLIENT_ID=...
         REDDIT_CLIENT_SECRET=...

Without credentials the script exits with a clear message and a `--fallback-queries` list the agent can
feed to `web_search` instead (site:reddit.com), so the pipeline still runs.

Usage:
  python reddit_hooks.py --subs advertising marketing PPC copywriting --time week --limit 60 --out hooks.json
  python reddit_hooks.py --query "hook that converted" --time month --out hooks.json
  python reddit_hooks.py --check          # just verify credentials
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "windows:jarvis-ad-creative:v1.0 (by /u/jarvis-agent)"
DEFAULT_SUBS = ["advertising", "marketing", "PPC", "copywriting", "SaaS", "Entrepreneur",
                "socialmedia", "TikTokCreators", "InstagramMarketing", "videography"]
# Signals that a comment/post is actually about a hook rather than chatter
HOOK_HINTS = re.compile(
    r"\b(hook|first 3 seconds|first three seconds|scroll[- ]stop|thumb[- ]stop|opening line|"
    r"cold open|pattern interrupt|CTR|conversion|convert(?:ed|ing)?|retention|watch time|"
    r"UGC|creative test|winning ad|best performing)\b", re.I)


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


def token() -> str:
    cid, secret = env_value("REDDIT_CLIENT_ID"), env_value("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        return ""
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    r = urllib.request.Request("https://www.reddit.com/api/v1/access_token", data=data,
                               headers={"Authorization": f"Basic {auth}", "User-Agent": UA,
                                        "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.load(resp).get("access_token", "")
    except urllib.error.HTTPError as e:
        sys.exit(f"reddit auth failed ({e.code}): {e.read().decode()[:200]}")


def api(path: str, tok: str, **params) -> dict:
    url = "https://oauth.reddit.com" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    r = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}", "User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(r, timeout=45) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    return {}


def harvest(children: list, source: str) -> list[dict]:
    out = []
    for c in children:
        d = c.get("data", {})
        title = (d.get("title") or "").strip()
        body = (d.get("selftext") or "")[:1500]
        if not title:
            continue
        score = d.get("score", 0)
        out.append({
            "source": source,
            "subreddit": d.get("subreddit"),
            "title": title,
            "body": body,
            "score": score,
            "comments": d.get("num_comments", 0),
            "url": "https://reddit.com" + (d.get("permalink") or ""),
            "created_utc": d.get("created_utc"),
            "hook_signal": bool(HOOK_HINTS.search(title + " " + body)),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subs", nargs="*", default=DEFAULT_SUBS)
    ap.add_argument("--query", default="")
    ap.add_argument("--time", default="week", choices=["day", "week", "month", "year", "all"])
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--out", default="hooks.json")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    tok = token()
    if not tok:
        queries = [f'site:reddit.com/r/{s} "hook" OR "first 3 seconds" ad' for s in a.subs[:5]]
        print(json.dumps({
            "ok": False,
            "reason": "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set in <HERMES_HOME>/.env",
            "setup": "https://www.reddit.com/prefs/apps -> create app -> type: script -> copy id+secret",
            "fallback_queries": queries,
        }, indent=2))
        sys.exit(3)
    if a.check:
        me = api("/api/v1/me", tok) if False else {"ok": True}
        print(json.dumps({"ok": True, "auth": "client_credentials"}, indent=2))
        return

    items: list[dict] = []
    if a.query:
        d = api("/search", tok, q=a.query, sort="top", t=a.time, limit=min(a.limit, 100), type="link")
        items += harvest(d.get("data", {}).get("children", []), f"search:{a.query}")
    for sub in a.subs:
        try:
            d = api(f"/r/{sub}/top", tok, t=a.time, limit=min(a.limit, 100))
            items += harvest(d.get("data", {}).get("children", []), f"r/{sub}")
        except urllib.error.HTTPError as e:
            print(f"  ! r/{sub} -> HTTP {e.code}", file=sys.stderr)
        time.sleep(0.6)

    seen, uniq = set(), []
    for it in sorted(items, key=lambda x: -x["score"]):
        k = it["title"].lower()[:80]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)

    hooks = [i for i in uniq if i["hook_signal"]]
    out = {"ok": True, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "window": a.time,
           "subs": a.subs, "total": len(uniq), "hook_relevant": len(hooks),
           "top_hook_posts": hooks[:25], "top_posts": uniq[:25]}
    pathlib.Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": a.out, "total": len(uniq), "hook_relevant": len(hooks),
                      "sample": [h["title"][:80] for h in hooks[:5]]}, indent=2))


if __name__ == "__main__":
    main()
