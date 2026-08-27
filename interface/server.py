#!/usr/bin/env python3
"""Jarvis console — local backend.

Serves the HUD (index.html) on http://127.0.0.1:7788 and exposes a small JSON API that reads
Hermes state from disk and proxies chat to Hermes' own OpenAI-compatible API server
(API_SERVER_ENABLED=true in .env → http://127.0.0.1:8642).

Loopback only. The Hermes API key never reaches the browser — this process reads it from .env
and attaches it server-side. State-changing calls require the `X-Jarvis: 1` header (blocks
cross-site form posts) and a localhost Host header (blocks DNS rebinding).

Run with the Hermes venv interpreter so PyYAML is available (start.ps1 does this):
  %LOCALAPPDATA%\\hermes\\hermes-agent\\venv\\Scripts\\python.exe interface\\server.py
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import yaml  # available in the Hermes venv
except Exception:  # pragma: no cover
    yaml = None

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("JARVIS_PORT", "7788"))
VALID_KEYS = ["merge", "push-main", "release", "deploy", "email-send", "social-post", "issue-close", "media-spend"]
TICKET_TTL = 30 * 60


def hermes_home() -> Path:
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "hermes"
    return Path.home() / ".hermes"


HOME = hermes_home()
HERMES_EXE = HOME / "hermes-agent" / "bin" / ("hermes.exe" if os.name == "nt" else "hermes")
JARVIS = HOME / "jarvis"
APPROVALS = JARVIS / "approvals"
POLICY_LOG = JARVIS / "policy.log"


# ----------------------------------------------------------------------------- helpers
def read_env() -> dict:
    out: dict[str, str] = {}
    p = HOME / ".env"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def read_config() -> dict:
    p = HOME / "config.yaml"
    if yaml is None or not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def api_base() -> tuple[str, str]:
    env = read_env()
    host = env.get("API_SERVER_HOST", "127.0.0.1") or "127.0.0.1"
    port = env.get("API_SERVER_PORT", "8642") or "8642"
    return f"http://{host}:{port}", env.get("API_SERVER_KEY", "")


def http_json(url: str, timeout: float = 2.5, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def run_hermes(*args: str, timeout: int = 60) -> dict:
    if not HERMES_EXE.exists():
        return {"ok": False, "error": f"hermes not found at {HERMES_EXE}"}
    try:
        cp = subprocess.run([str(HERMES_EXE), *args], capture_output=True, text=True, timeout=timeout,
                            encoding="utf-8", errors="replace")
        return {"ok": cp.returncode == 0, "code": cp.returncode, "stdout": cp.stdout[-4000:], "stderr": cp.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}


def file_age(p: Path) -> float | None:
    try:
        return time.time() - p.stat().st_mtime
    except Exception:
        return None


# ----------------------------------------------------------------------------- data
def status() -> dict:
    cfg = read_config()
    env = read_env()
    gw = read_json(HOME / "gateway_state.json") or {}
    lifecycle = read_json(HOME / "state" / "gateway.lifecycle.json") or {}
    hb_age = file_age(HOME / "state" / "gateway.heartbeat")
    gateway_running = (hb_age is not None and hb_age < 180) or gw.get("gateway_state") == "running"
    platforms = {k: v.get("state") for k, v in (gw.get("platforms") or {}).items()}

    bridge_port = (((cfg.get("gateway") or {}).get("platforms") or {}).get("whatsapp") or {}).get("extra", {}).get("bridge_port", 3000)
    bridge = None
    try:
        bridge = http_json(f"http://127.0.0.1:{bridge_port}/health", timeout=1.5)
    except Exception:
        bridge = None

    base, key = api_base()
    api_ok = False
    api_model = None
    if key:
        try:
            m = http_json(f"{base}/v1/models", timeout=2.5, headers={"Authorization": f"Bearer {key}"})
            api_ok = True
            data = m.get("data") or []
            api_model = data[0].get("id") if data else None
        except Exception:
            api_ok = False

    model = cfg.get("model") or {}
    jobs = read_json(HOME / "cron" / "jobs.json") or []
    if isinstance(jobs, dict):
        jobs = jobs.get("jobs", [])
    drafts = list((JARVIS / "content").glob("*/manifest.json")) if (JARVIS / "content").exists() else []
    mem = HOME / "memories" / "MEMORY.md"
    usr = HOME / "memories" / "USER.md"
    return {
        "time": time.time(),
        "gateway": {"running": gateway_running, "pid": gw.get("pid") or lifecycle.get("pid"), "heartbeat_age_s": hb_age,
                    "platforms": platforms, "started_at": lifecycle.get("started_at")},
        "whatsapp": {"connected": bool(bridge and bridge.get("status") == "connected"), "port": bridge_port,
                     "uptime_s": (bridge or {}).get("uptime"), "allowed": env.get("WHATSAPP_ALLOWED_USERS", "")[-4:].rjust(4, "•")},
        "api": {"ok": api_ok, "base": base, "model_name": api_model, "enabled": env.get("API_SERVER_ENABLED", "").lower() == "true"},
        "model": {"default": model.get("default"), "provider": model.get("provider")},
        "counts": {"cron": len(jobs), "approvals": len(list_tickets()), "drafts": len(drafts),
                   "policy_blocks_24h": sum(1 for e in policy_log(500) if e["kind"] == "BLOCK" and time.time() - e["ts"] < 86400)},
        "memory": {"memory_bytes": mem.stat().st_size if mem.exists() else 0, "user_bytes": usr.stat().st_size if usr.exists() else 0},
        "paused": (HOME / "state" / "paused").exists() or (HOME / ".paused").exists(),
        "home": str(HOME),
    }


def list_tickets() -> list[dict]:
    out = []
    if not APPROVALS.exists():
        return out
    for f in APPROVALS.glob("*.standing"):
        out.append({"key": f.stem, "standing": True, "age_s": int(file_age(f) or 0), "valid": True, "expires_in_s": None})
    for f in APPROVALS.glob("*.ok"):
        age = file_age(f) or 0
        out.append({"key": f.stem, "standing": False, "age_s": int(age), "valid": age < TICKET_TTL, "expires_in_s": max(0, int(TICKET_TTL - age))})
    return sorted(out, key=lambda t: (not t["standing"], t["age_s"]))


LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (BLOCK|ALLOW|ERROR) ?(L[23])?\s*(?:'(?P<key>[\w-]+)'|\((?P<what>[^)]*)\)|write\s+(?P<wtool>\w+)\s*->\s*(?P<wpath>.*))?[^:]*:?\s*(?P<cmd>.*)$")


def policy_log(limit: int = 200) -> list[dict]:
    if not POLICY_LOG.exists():
        return []
    try:
        lines = POLICY_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except Exception:
        return []
    out = []
    for ln in lines:
        m = LOG_RE.match(ln)
        if not m:
            continue
        try:
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except Exception:
            ts = 0
        out.append({"ts": ts, "kind": m.group(2), "level": m.group(3) or "", "key": m.group("key") or "",
                    "what": m.group("what") or "", "cmd": (m.group("cmd") or m.group("wpath") or "").strip()})
    return out


def cron_jobs() -> list[dict]:
    raw = read_json(HOME / "cron" / "jobs.json") or []
    if isinstance(raw, dict):
        raw = raw.get("jobs", [])
    def sched_text(j: dict) -> str:
        # Hermes stores schedule as {"kind": "cron", "expr": "...", "display": "..."} in newer versions.
        s = j.get("schedule") or j.get("cron") or j.get("schedule_display")
        if isinstance(s, dict):
            return str(s.get("display") or s.get("expr") or s.get("kind") or "")
        return str(s or "")

    out = []
    for j in raw:
        if not isinstance(j, dict):
            continue
        out.append({
            "id": j.get("id") or j.get("job_id"),
            "name": j.get("name") or (j.get("prompt") or "")[:40],
            "schedule": sched_text(j),
            "next_run": j.get("next_run") or j.get("next_run_at"),
            "last_run": j.get("last_run") or j.get("last_run_at"),
            "last_status": j.get("last_status") or j.get("status"),
            "enabled": j.get("enabled", not j.get("paused", False)),
            "deliver": j.get("deliver"),
            "skills": j.get("skills") or [],
        })
    return out


def sessions(limit: int = 12) -> list[dict]:
    p = HOME / "state.db"
    if not p.exists():
        return []
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
        rows = c.execute(
            "select id, source, title, model, message_count, tool_call_count, started_at, last_activity_at, "
            "estimated_cost_usd, last_activity_description from sessions order by coalesce(last_activity_at, started_at) desc limit ?",
            (limit,)).fetchall()
        c.close()
    except Exception:
        return []
    keys = ["id", "source", "title", "model", "messages", "tool_calls", "started_at", "last_activity_at", "cost_usd", "last_activity"]
    return [dict(zip(keys, r)) for r in rows]


def memory() -> dict:
    def rd(name):
        p = HOME / "memories" / name
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    return {"memory": rd("MEMORY.md"), "user": rd("USER.md")}


def content() -> list[dict]:
    out = []
    root = JARVIS / "content"
    if not root.exists():
        return out
    for m in sorted(root.glob("*/manifest.json"), reverse=True):
        d = read_json(m) or {}
        d["slug"] = m.parent.name
        d["files"] = [f.name for f in m.parent.iterdir() if f.is_file()]
        post = m.parent / "post.md"
        d["post"] = post.read_text(encoding="utf-8", errors="replace")[:4000] if post.exists() else ""
        out.append(d)
    return out


# ----------------------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisConsole/1.0"

    def log_message(self, fmt, *args):  # quieter
        if "/api/status" in (args[0] if args else ""):
            return
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- plumbing
    def _json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _local_host(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost", "[::1]", "::1")

    def _guard_write(self) -> bool:
        if not self._local_host() or self.headers.get("X-Jarvis") != "1":
            self._json({"error": "forbidden"}, 403)
            return False
        return True

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # -- GET
    def do_GET(self):
        if not self._local_host():
            return self._json({"error": "forbidden"}, 403)
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)
        if path in ("/", "/index.html"):
            return self._static("index.html", "text/html; charset=utf-8")
        if path == "/api/status":
            return self._json(status())
        if path == "/api/approvals":
            return self._json({"keys": VALID_KEYS, "tickets": list_tickets()})
        if path == "/api/policy-log":
            return self._json(list(reversed(policy_log(int(q.get("limit", ["120"])[0])))))
        if path == "/api/cron":
            return self._json(cron_jobs())
        if path == "/api/sessions":
            return self._json(sessions())
        if path == "/api/memory":
            return self._json(memory())
        if path == "/api/content":
            return self._json(content())
        return self._json({"error": "not found"}, 404)

    def _static(self, name: str, ctype: str):
        p = HERE / name
        if not p.exists():
            return self._json({"error": f"{name} missing"}, 404)
        body = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    # -- POST / DELETE
    def do_POST(self):
        if not self._guard_write():
            return
        path = urlparse(self.path).path
        body = self._body()
        if path == "/api/approvals":
            key = str(body.get("key", ""))
            if key not in VALID_KEYS:
                return self._json({"error": f"unknown key; valid: {VALID_KEYS}"}, 400)
            APPROVALS.mkdir(parents=True, exist_ok=True)
            ext = "standing" if body.get("standing") else "ok"
            (APPROVALS / f"{key}.{ext}").write_text(f"{'standing approval' if ext == 'standing' else 'approved'} via Jarvis console at {time.strftime('%Y-%m-%dT%H:%M:%S')}\n", encoding="utf-8")
            return self._json({"ok": True, "tickets": list_tickets()})
        if path == "/api/cron/run":
            name = str(body.get("name", ""))[:120]
            if not name:
                return self._json({"error": "name required"}, 400)
            return self._json(run_hermes("cron", "run", name, timeout=60))
        if path == "/api/pause":
            return self._json(run_hermes("pause", timeout=60))
        if path == "/api/resume":
            return self._json(run_hermes("resume", timeout=60))
        if path == "/api/chat":
            return self._chat(body)
        return self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        if not self._guard_write():
            return
        path = urlparse(self.path).path
        m = re.match(r"^/api/approvals/([\w-]+)$", path)
        if m:
            key = m.group(1)
            for ext in ("ok", "standing"):
                t = APPROVALS / f"{key}.{ext}"
                if t.exists():
                    t.unlink()
            return self._json({"ok": True, "tickets": list_tickets()})
        return self._json({"error": "not found"}, 404)

    # -- chat proxy (SSE passthrough to Hermes /v1/responses)
    def _chat(self, body: dict):
        base, key = api_base()
        if not key:
            return self._json({"error": "API_SERVER_KEY missing in .env — run interface/start.ps1"}, 503)
        text = str(body.get("input", "")).strip()
        if not text:
            return self._json({"error": "input required"}, 400)
        payload = {
            "model": "hermes-agent",
            "input": text,
            "conversation": str(body.get("conversation") or "jarvis-console")[:64],
            "store": True,
            "stream": True,
        }
        req = urllib.request.Request(
            f"{base}/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        try:
            upstream = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:800]
            return self._json({"error": f"hermes api {e.code}", "detail": detail}, 502)
        except Exception as e:
            return self._json({"error": f"hermes api unreachable: {e}"}, 502)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            while True:
                chunk = upstream.read(1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass


def main():
    host = "127.0.0.1"
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    srv = ThreadingHTTPServer((host, PORT), Handler)
    srv.daemon_threads = True
    base, key = api_base()
    print(f"Jarvis console  ->  http://{host}:{PORT}")
    print(f"Hermes home     ->  {HOME}")
    print(f"Hermes API      ->  {base}  ({'key loaded' if key else 'NO KEY - chat disabled'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
