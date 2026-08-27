#!/usr/bin/env python3
"""Growstack LLM Router shim — makes the router usable by streaming agent frameworks.

WHY THIS EXISTS
---------------
The router streams plain text correctly, but it does not serialise `delta.tool_calls` into the SSE
stream. Measured 2026-08-25 on azure-gpt-5-mini, identical request, only `stream` differing:

    non-streaming :  89 completion tokens,  finish_reason="tool_calls",  tool_calls present
    streaming     : 153 completion tokens,  finish_reason="stop",        tool_calls MISSING, 0 chars delivered

With `tool_choice:"required"` the streamed response is completely empty — tokens are generated, billed,
and discarded. Every model and every upstream provider on the router behaves the same way, so the defect
is in the router's own SSE layer, not any backend.

Hermes hard-codes `stream=True`, and Jarvis is entirely tool-driven, so the router is unusable directly.

WHAT THIS DOES
--------------
Sits on 127.0.0.1:8799 and speaks the OpenAI API to Hermes:

    Hermes --stream:true--> shim --stream:false--> router
                             |  full JSON reply incl. tool_calls
    Hermes <---proper SSE----+  re-emitted as spec-correct chunks

Everything else (GET /v1/models, non-streaming POSTs) is proxied untouched. When the router's SSE is
fixed, set PASSTHROUGH_STREAM=1 (or delete the shim and point Hermes straight at the router).

TRADE-OFF: the reply arrives as one block after the model finishes — no token-by-token typing. Tool
calls, multi-step agent loops, cron and the lead machine all work normally.

RUN
    python router_shim.py                     # 127.0.0.1:8799 -> https://dev-llm-router.growstack.ai
    python router_shim.py --port 8799 --verbose
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_UPSTREAM = "https://dev-llm-router.growstack.ai"
# Cloudflare fronts the router and 403s the default Python-urllib User-Agent ("Error 1010").
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CHUNK_CHARS = 24          # size of synthesised content deltas, so the UI still animates a little
# The router's edge gateway 504s on long non-streaming generations. Cap our own wait below that so
# we fail fast and let Hermes' fallback take over instead of the user staring at a dead socket.
UPSTREAM_TIMEOUT = int(os.environ.get("SHIM_UPSTREAM_TIMEOUT", "150"))
VERBOSE = False


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
            return m.group(1).strip().strip('"').strip("'")
    return ""


def log(msg: str) -> None:
    if VERBOSE:
        print(f"  {msg}", flush=True)


class Shim(BaseHTTPRequestHandler):
    upstream = DEFAULT_UPSTREAM
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):        # quiet by default
        if VERBOSE:
            sys.stderr.write("%s\n" % (fmt % args))

    # ---------------------------------------------------------------- helpers
    def _auth(self) -> str:
        """Prefer the caller's Authorization header; fall back to the key in .env."""
        got = self.headers.get("Authorization")
        if got:
            return got
        key = env_value("GROWSTACK_ROUTER_KEY")
        return f"Bearer {key}" if key else ""

    def _upstream(self, path: str, body: bytes | None, method: str, timeout: int = 600):
        req = urllib.request.Request(
            self.upstream + path, data=body, method=method,
            headers={"Authorization": self._auth(), "Content-Type": "application/json",
                     "Accept": "application/json", "User-Agent": UA},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()

    def _send(self, code: int, payload: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _sse_open(self) -> None:
        # No Content-Length is possible for a stream, and we are not chunk-framing, so the response
        # must be delimited by EOF: announce Connection: close and actually close after [DONE].
        # Without this the client blocks waiting for a body that never terminates.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

    def _sse_done(self) -> None:
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

    def _sse(self, obj) -> None:
        self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
        self.wfile.flush()

    # ---------------------------------------------------------------- routes
    def do_GET(self):
        try:
            status, body = self._upstream(self.path, None, "GET", timeout=60)
            self._send(status, body)
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read())
        except Exception as e:
            self._send(502, json.dumps({"error": {"message": f"shim upstream error: {e}"}}).encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except Exception:
            self._send(400, json.dumps({"error": {"message": "shim: body is not JSON"}}).encode())
            return

        wants_stream = bool(payload.get("stream")) and not os.environ.get("PASSTHROUGH_STREAM")
        if not wants_stream:
            try:
                status, body = self._upstream(self.path, raw, "POST")
                self._send(status, body)
            except urllib.error.HTTPError as e:
                self._send(e.code, e.read())
            except Exception as e:
                self._send(502, json.dumps({"error": {"message": f"shim upstream error: {e}"}}).encode())
            return

        # --- the whole point: ask upstream WITHOUT stream, re-emit as correct SSE ---
        downgraded = {k: v for k, v in payload.items() if k not in ("stream", "stream_options")}
        model = downgraded.get("model", "?")
        n_tools = len(downgraded.get("tools") or [])
        t0 = time.time()
        log(f"stream->nonstream  model={model} tools={n_tools}")

        # Open the stream and start a keepalive immediately. Non-streaming upstream means no bytes
        # for the whole generation; without this the client sees a silent socket, its stall watchdog
        # fires, and the UI reports "N seconds with no output".
        self._sse_open()
        stop_ka = threading.Event()

        def keepalive():
            while not stop_ka.wait(5.0):
                try:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                except Exception:
                    return
        ka = threading.Thread(target=keepalive, daemon=True)
        ka.start()

        data = None
        last_err = None
        try:
            # The router sits behind a gateway that 504s on long non-streaming generations
            # (measured: fast calls 4-6s; large tool payloads time out). Retry once, then fail
            # fast so Hermes' own fallback engages instead of the user waiting minutes.
            for attempt in range(2):
                try:
                    _, body = self._upstream(self.path, json.dumps(downgraded).encode(), "POST",
                                             timeout=UPSTREAM_TIMEOUT)
                    data = json.loads(body)
                    break
                except urllib.error.HTTPError as e:
                    detail = e.read().decode("utf-8", "replace")
                    last_err = f"router HTTP {e.code}"
                    log(f"upstream HTTP {e.code} (attempt {attempt+1}): {detail[:120]}")
                    if e.code in (502, 503, 504) and attempt == 0:
                        time.sleep(2)
                        continue
                    break
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"
                    log(f"upstream {last_err} (attempt {attempt+1})")
                    if attempt == 0:
                        time.sleep(2)
                        continue
                    break
        finally:
            stop_ka.set()

        if data is None:
            # Emit an OpenAI-shaped error so the client fails over immediately rather than
            # treating an empty stream as a valid empty answer.
            log(f"giving up after {time.time()-t0:.0f}s: {last_err}")
            self._sse({"error": {"message": f"{last_err} (shim, {time.time()-t0:.0f}s)",
                                 "type": "upstream_error"}})
            self._sse_done()
            return

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []
        finish = choice.get("finish_reason") or ("tool_calls" if tool_calls else "stop")
        cid = data.get("id") or f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = data.get("created") or int(time.time())
        base = {"id": cid, "object": "chat.completion.chunk", "created": created,
                "model": data.get("model", model)}

        def frame(delta, finish_reason=None, usage=None):
            f = dict(base)
            f["choices"] = [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
            if usage:
                f["usage"] = usage
            return f

        self._sse_open()
        self._sse(frame({"role": "assistant"}))

        for i in range(0, len(content), CHUNK_CHARS):
            self._sse(frame({"content": content[i:i + CHUNK_CHARS]}))

        # tool_calls: announce id+name first, then the arguments — the shape SDKs expect
        for idx, tc in enumerate(tool_calls):
            fn = tc.get("function") or {}
            self._sse(frame({"tool_calls": [{
                "index": idx,
                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:20]}",
                "type": "function",
                "function": {"name": fn.get("name", ""), "arguments": ""},
            }]}))
            args = fn.get("arguments") or ""
            if not isinstance(args, str):
                args = json.dumps(args)
            for i in range(0, len(args), 96) or [0]:
                self._sse(frame({"tool_calls": [{"index": idx, "function": {"arguments": args[i:i + 96]}}]}))
            if not args:
                self._sse(frame({"tool_calls": [{"index": idx, "function": {"arguments": ""}}]}))

        self._sse(frame({}, finish_reason=finish, usage=data.get("usage")))
        self._sse_done()
        log(f"  -> {finish} | {len(content)} chars | {len(tool_calls)} tool_call(s) | {time.time()-t0:.1f}s")


def main() -> None:
    global VERBOSE
    ap = argparse.ArgumentParser(description="OpenAI-compatible shim that restores tool_calls in streamed responses")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    VERBOSE = a.verbose
    Shim.upstream = a.upstream.rstrip("/")

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    key = env_value("GROWSTACK_ROUTER_KEY")
    srv = ThreadingHTTPServer((a.host, a.port), Shim)
    srv.daemon_threads = True
    print(f"router shim   ->  http://{a.host}:{a.port}/v1")
    print(f"upstream      ->  {Shim.upstream}")
    print(f"router key    ->  {'loaded from .env' if key else 'NOT FOUND (callers must send Authorization)'}")
    print("streaming     ->  downgraded to non-streaming upstream, re-emitted with tool_calls intact")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
