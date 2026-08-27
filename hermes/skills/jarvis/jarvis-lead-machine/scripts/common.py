#!/usr/bin/env python3
"""Shared helpers for the Jarvis lead machine.

Design rules every script here follows:
  * **Dry-run is the default.** Nothing reaches a third-party API unless --live is passed.
  * **Secrets never printed.** Keys are read from <HERMES_HOME>/.env and redacted in every log line.
  * **No irreversible step happens implicitly.** Sending and campaign activation are gated separately.
  * Every stage writes a JSON artifact to the run directory so the next stage is resumable and auditable.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

# --------------------------------------------------------------------------- paths / env

def hermes_home() -> pathlib.Path:
    if os.environ.get("HERMES_HOME"):
        return pathlib.Path(os.environ["HERMES_HOME"])
    if os.name == "nt":
        return pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home())) / "hermes"
    return pathlib.Path.home() / ".hermes"


def env_value(name: str, default: str = "") -> str:
    """Read a secret from the process env, falling back to <HERMES_HOME>/.env."""
    v = os.environ.get(name, "").strip()
    if v:
        return v
    p = hermes_home() / ".env"
    if p.exists():
        m = re.search(rf"(?m)^{re.escape(name)}=(.*)$", p.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return default


def require_env(name: str, how: str) -> str:
    v = env_value(name)
    if not v:
        die(f"{name} is not set.\n  Add it to {hermes_home() / '.env'} as a line:  {name}=...\n  {how}")
    return v


def run_dir(slug: str, create: bool = True) -> pathlib.Path:
    d = hermes_home() / "jarvis" / "leadgen" / slug
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- output

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|hf_[A-Za-z0-9]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,}|[A-Za-z0-9_\-]{22,}:[a-f0-9]{24,})")


def redact(text: str) -> str:
    return _SECRET_RE.sub("<redacted>", str(text))


def log(msg: str) -> None:
    print(redact(msg), flush=True)


def die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[valid-type]
    print("ERROR: " + redact(msg), file=sys.stderr, flush=True)
    raise SystemExit(code)


def write_json(path: pathlib.Path, data) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_json(path: pathlib.Path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


# --------------------------------------------------------------------------- http

# Cloudflare fronts several of these APIs and returns "Error 1010: Access denied" to the default
# Python-urllib User-Agent. A normal browser UA is required or every call 403s before auth is even read.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        self.status, self.body, self.url = status, body, url
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")

    @property
    def cloudflare_blocked(self) -> bool:
        return "error 1010" in (self.body or "").lower() or "cloudflare" in (self.body or "").lower()


def http_json(url: str, method: str = "GET", headers: dict | None = None, body=None,
              timeout: int = 60, retries: int = 3, dry_run: bool = False):
    """One JSON HTTP call with retry on 429/5xx.

    When dry_run is True nothing is sent — the request is returned for inspection instead.
    """
    payload = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json", "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9", **(headers or {})}
    if payload is not None:
        hdrs.setdefault("Content-Type", "application/json")

    if dry_run:
        log(f"  [dry-run] {method} {url}")
        if body is not None:
            preview = json.dumps(body, ensure_ascii=False)
            log(f"  [dry-run] body: {preview[:600]}{'…' if len(preview) > 600 else ''}")
        return {"_dry_run": True, "url": url, "method": method, "body": body}

    last: BaseException = RuntimeError(f"no attempt made for {url}")
    for attempt in range(retries):
        req = urllib.request.Request(url, data=payload, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            last = ApiError(e.code, detail, url)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = 2 ** attempt * 3
                log(f"  retry in {wait}s (HTTP {e.code})")
                time.sleep(wait)
                continue
            raise last
        except Exception as e:  # network-level
            last = RuntimeError(f"{type(e).__name__}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise last
    raise last  # pragma: no cover


# --------------------------------------------------------------------------- misc

def chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def slugify(text: str, maxlen: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:maxlen] or "run").strip("-")


def approval_ticket(key: str, consume: bool = True) -> bool:
    """True when the owner has issued `jarvis approve <key>` (single use) or a standing approval.

    Mirrors hermes/agent-hooks/policy-guard.py so scripts fail the same way the guard does.
    """
    appr = hermes_home() / "jarvis" / "approvals"
    standing, ticket = appr / f"{key}.standing", appr / f"{key}.ok"
    if standing.exists():
        return True
    if ticket.exists():
        try:
            if time.time() - ticket.stat().st_mtime > 30 * 60:
                ticket.unlink(missing_ok=True)
                return False
            if consume:
                ticket.unlink(missing_ok=True)
            return True
        except Exception:
            return False
    return False


def require_approval(key: str, what: str) -> None:
    if not approval_ticket(key):
        die(f"BLOCKED: {what}\n"
            f"  This is a Level 2 action and needs the owner's approval.\n"
            f"  Ask them to run:  jarvis approve {key}            (one use, 30 min)\n"
            f"                or: jarvis approve {key} standing   (until revoked)\n"
            f"  Then run the same command again. Do not route around this.")
