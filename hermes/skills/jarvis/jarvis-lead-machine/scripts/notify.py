#!/usr/bin/env python3
"""Stage 6 — send the run summary to a WhatsApp number through the Hermes gateway.

Uses the WhatsApp bridge the gateway already runs (no Twilio, no extra cost). The bridge exposes
POST /send on 127.0.0.1:<bridge_port> — the port comes from config.yaml
(gateway.platforms.whatsapp.extra.bridge_port, 3777 on this machine).

The recipient must be reachable by the bridge: either the number is in WHATSAPP_ALLOWED_USERS or it is
a chat the linked account can message. Sending to an arbitrary stranger is intentionally not supported.

Usage:
  python notify.py --to 919866614377 --run <run-dir>              # dry-run, prints the message
  python notify.py --to 919866614377 --run <run-dir> --live       # actually send
  python notify.py --to 919866614377 --text "..." --live
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ApiError, die, env_value, hermes_home, http_json, log, read_json  # noqa: E402


def bridge_port() -> int:
    """Read the WhatsApp bridge port out of config.yaml without needing a YAML parser."""
    cfg = hermes_home() / "config.yaml"
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"bridge_port:\s*(\d+)", text)
        if m:
            return int(m.group(1))
    return int(env_value("WHATSAPP_BRIDGE_PORT", "3000") or 3000)


def normalise_number(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        die("--to must contain digits, e.g. 919866614377 (country code, no +)")
    if len(digits) == 10:
        log(f"  ! {digits} looks like a local number; assuming India and prefixing 91")
        digits = "91" + digits
    return digits


def summary_text(run: pathlib.Path) -> str:
    icp = read_json(run / "icp.json") or {}
    leads = read_json(run / "leads.json") or {}
    drafts = read_json(run / "drafts.json") or {}
    camp = read_json(run / "campaign.json") or {}
    track = read_json(run / "tracker.json") or {}

    company = icp.get("company", {}).get("name", "run")
    where = track.get("url") or (track.get("files") or [""])[0]
    lines = [
        f"*Outbound run — {company}*",
        "",
        f"Leads sourced: *{leads.get('count', 0)}* ({leads.get('with_email', 0)} with email) "
        f"via {leads.get('provider', '—')}",
        f"Drafts ready: *{drafts.get('ready', 0)}*  ·  need review: {drafts.get('needs_review', 0)}",
        f"Campaign: *{camp.get('campaign_name', '—')}* — "
        f"{'ACTIVE' if camp.get('activate_response') else 'created, PAUSED'}",
    ]
    if where:
        lines.append(f"Tracker: {where}")
    sample = next((d for d in drafts.get("drafts", []) if d.get("draft", {}).get("status") == "ok"), None)
    if sample:
        body = (sample["draft"].get("body") or "").split("\n\n")[0]
        lines += ["", "_Sample opener:_", f"“{body[:200]}”"]
    lines += ["", "Nothing has been sent yet. Reply *approve campaign* to review and start sending."]
    return "\n".join(lines)


def send(port: int, to: str, text: str, live: bool) -> dict:
    url = f"http://127.0.0.1:{port}/send"
    payload = {"chatId": f"{to}@s.whatsapp.net", "message": text}
    if not live:
        log(f"  [dry-run] POST {url}")
        log(f"  [dry-run] to {to}, {len(text)} chars")
        return {"_dry_run": True}
    try:
        return http_json(url, "POST", {"Content-Type": "application/json"}, payload, timeout=45)
    except ApiError as e:
        die(f"bridge refused the send (HTTP {e.status}): {e.body[:200]}\n"
            f"  Is the gateway running? Check: hermes gateway status")
    except Exception as e:
        die(f"could not reach the WhatsApp bridge on port {port} ({type(e).__name__}).\n"
            f"  Start the gateway first:  hermes gateway restart")


def main() -> None:
    ap = argparse.ArgumentParser(description="Send a run summary over WhatsApp via the Hermes bridge")
    ap.add_argument("--to", required=True, help="number with country code, digits only")
    ap.add_argument("--run", help="run directory to summarise")
    ap.add_argument("--text", help="send this literal text instead of a run summary")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()

    if not a.run and not a.text:
        die("give --run or --text")
    to = normalise_number(a.to)
    text = a.text or summary_text(pathlib.Path(a.run))
    port = bridge_port()

    log(f"bridge port {port} -> {to}")
    log("\n--- message ---\n" + text + "\n---------------")
    send(port, to, text, a.live)
    log("\nsent" if a.live else "\n[dry-run] not sent — add --live")


if __name__ == "__main__":
    main()
