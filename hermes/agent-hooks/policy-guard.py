#!/usr/bin/env python3
"""Jarvis policy guard — a Hermes `pre_tool_call` shell hook.

Enforces the permission-level model from SOUL.md at the machine level, so it holds
even if the model is tricked by a prompt injection:

  Level 3  -> always blocked (irreversible / financial / credential access)
  Level 2  -> blocked unless the owner has issued a fresh, single-use approval
              ticket:  `jarvis approve <key>`  (creates  <HERMES_HOME>/jarvis/approvals/<key>.ok)
  Level 0/1 -> allowed

Wire protocol (see Hermes docs, "Shell hooks"): JSON payload on stdin, optional JSON on stdout.
Any uncaught failure blocks the call (we are a security gate; we fail closed).

Stdlib only. Works on Windows, macOS, Linux.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

TICKET_TTL_SECONDS = 30 * 60  # an approval ticket is valid for 30 minutes, single use


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "hermes"
    return Path.home() / ".hermes"


HOME = hermes_home()
APPROVALS_DIR = HOME / "jarvis" / "approvals"
LOG_FILE = HOME / "jarvis" / "policy.log"

# ---------------------------------------------------------------------------
# Level 3 — never. (regex, case-insensitive, matched against the whole command)
# ---------------------------------------------------------------------------
LEVEL3 = [
    (r"\bgh\s+repo\s+delete\b|\bglab\s+repo\s+delete\b", "delete a repository"),
    (r"\bgh\s+release\s+delete\b|\bglab\s+release\s+delete\b", "delete a release"),
    (r"\bgit\s+push\b[^\n]*(\s--force\b|\s-f\b|\s--force-with-lease\b)", "force-push"),
    (r"\bgit\s+push\b[^\n]*\s--delete\b", "delete a remote branch"),
    (r"\bgit\s+branch\s+-D\s+(main|master|develop)\b", "delete a protected branch"),
    (r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(/|~|\$HOME|[A-Za-z]:[\\/]?)(\s|$)", "recursive delete of a root path"),
    (r"\bRemove-Item\b[^\n]*-Recurse[^\n]*(\s[A-Za-z]:\\?(\s|$)|\$HOME|\$env:USERPROFILE\s)", "recursive delete of a root path"),
    (r"\b(rmdir|rd)\s+/s\b[^\n]*\s[A-Za-z]:\\?(\s|$)", "recursive delete of a drive root"),
    (r"\b(format|diskpart|mkfs(\.\w+)?)\b", "disk format"),
    (r"\bdd\s+if=", "raw disk write"),
    (r"\bdrop\s+(database|table|schema)\b", "destructive SQL"),
    (r"\btruncate\s+table\b", "destructive SQL"),
    (r"\b(stripe|paypal|razorpay|wise)\b[^\n]*\b(charge|payout|transfer|refund|pay)\b", "move money"),
    (r"\bhermes\s+uninstall\b", "uninstall Hermes"),
    (r"\bgh\s+auth\s+logout\b", "log out of GitHub"),
    (r"(^|[\s;&|])(cat|type|Get-Content|more|less|head|tail|bat)\s+[^\n]*(\.env\b|auth\.json|credentials\.json|google_token\.json|client_secret|id_rsa|\.xurl)", "read a credential file"),
    (r"\b(curl|wget|Invoke-WebRequest|iwr)\b[^\n]*\|\s*(sh|bash|powershell|pwsh|iex)\b", "pipe remote script to shell"),
]

# ---------------------------------------------------------------------------
# Level 2 — needs an approval ticket. key -> (regex, human description)
# ---------------------------------------------------------------------------
LEVEL2 = {
    "merge":       (r"\bgh\s+pr\s+merge\b|\bglab\s+mr\s+merge\b|\bgit\s+merge\b[^\n]*\b(main|master)\b", "merge a pull/merge request or merge into main"),
    "push-main":   (r"\bgit\s+push\b[^\n]*\b(origin|upstream)\s+(main|master|HEAD:main|HEAD:master)\b", "push directly to main"),
    "release":     (r"\bgh\s+release\s+create\b|\bnpm\s+publish\b|\bpypi\b|\btwine\s+upload\b", "publish a release or package"),
    "deploy":      (r"\bvercel\b[^\n]*--prod\b|\bnetlify\s+deploy\b[^\n]*--prod\b|\bdocker\s+push\b|\bkubectl\s+(apply|rollout|delete)\b|\bterraform\s+(apply|destroy)\b|\bfly\s+deploy\b|\bgcloud\s+run\s+deploy\b|\baws\s+\S+\s+(deploy|update-function-code|put-object)\b", "deploy to production"),
    "email-send":  (r"\bhimalaya\b[^\n]*\b(send|reply|forward)\b|\bgws\s+gmail\b[^\n]*\b(send|messages\s+send)\b|google_api\.py\b[^\n]*\bsend\b|\bsendmail\b|\bSend-MailMessage\b", "send an email"),
    "social-post": (r"\bxurl\b[^\n]*\b(post|tweet|reply|dm)\b|/2/tweets\b|\bxurl\s+-X\s+POST\b|linkedin[^\n]*\b(ugcPosts|shares|posts)\b|\bbuffer\b[^\n]*\bpost\b|\btypefully\b", "publish to social media"),
    "issue-close": (r"\bgh\s+issue\s+(close|delete)\b|\bgh\s+pr\s+close\b|\bglab\s+(issue|mr)\s+close\b", "close an issue or PR/MR"),
    # Anything that bills a paid media API. Video renders cost dollars per clip, so they are never automatic.
    "media-spend": (r"fal_media\.py[^\n]*\bvideo\b|queue\.fal\.run|\bfal-ai/|\bbytedance/seedance|"
                    r"\breplicate\.com/|\bapi\.runwayml\.com|\bapi\.klingai\.com|\bapi\.heygen\.com|\bapi\.elevenlabs\.io/v1/(video|dubbing)",
                    "spend money on a paid media generation API"),
}

# Files the agent may never write to (write_file / patch)
PROTECTED_WRITE = re.compile(
    r"(^|[\\/])(\.env(\..*)?|auth\.json|credentials\.json|google_token\.json|google_client_secret\.json|id_rsa|id_ed25519|\.xurl|shell-hooks-allowlist\.json)$",
    re.IGNORECASE,
)


def log(line: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except Exception:
        pass


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def allow() -> None:
    print("{}")
    sys.exit(0)


def consume_ticket(key: str) -> bool:
    """Return True if the owner has approved `key`.

    Two forms: a *standing* approval (`<key>.standing`, no expiry, not consumed — for routines the owner has
    delegated, e.g. the 09:00 social post or an approved outreach sequence) or a *single-use* ticket
    (`<key>.ok`, deleted on use, 30-minute TTL). Both can only be created by a human on this machine.
    """
    standing = APPROVALS_DIR / f"{key}.standing"
    try:
        if standing.exists():
            return True
    except Exception:
        pass
    ticket = APPROVALS_DIR / f"{key}.ok"
    try:
        if not ticket.exists():
            return False
        age = time.time() - ticket.stat().st_mtime
        if age > TICKET_TTL_SECONDS:
            ticket.unlink(missing_ok=True)
            return False
        ticket.unlink(missing_ok=True)  # single use
        return True
    except Exception:
        return False


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        block("policy guard could not parse the hook payload (failing closed)")

    if payload.get("hook_event_name") != "pre_tool_call":
        allow()

    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}

    # ---- file writes -------------------------------------------------------
    if tool in ("write_file", "patch"):
        path = str(tool_input.get("path") or tool_input.get("file_path") or "")
        if PROTECTED_WRITE.search(path):
            log(f"BLOCK L3 write {tool} -> {path}")
            block(f"Level 3: writing to a credential/config file ({os.path.basename(path)}) is not permitted. "
                  "Ask the owner to change it by hand.")
        allow()

    if tool != "terminal":
        allow()

    cmd = str(tool_input.get("command") or "")
    flat = " ".join(cmd.split())  # collapse whitespace/newlines for matching

    for pattern, what in LEVEL3:
        if re.search(pattern, flat, re.IGNORECASE):
            log(f"BLOCK L3 ({what}): {flat[:200]}")
            block(f"Level 3 (never): this command would {what}. Refuse it and offer the owner a safe alternative. "
                  "There is no approval path for Level 3 actions.")

    for key, (pattern, what) in LEVEL2.items():
        if re.search(pattern, flat, re.IGNORECASE):
            if consume_ticket(key):
                log(f"ALLOW L2 ticket '{key}' consumed: {flat[:200]}")
                allow()
            log(f"BLOCK L2 '{key}' (no ticket): {flat[:200]}")
            block(
                f"Level 2 (ask first): this command would {what}. It is blocked until the owner issues an approval ticket. "
                f"Tell the owner exactly this: \"Run `jarvis approve {key}` to authorise it (valid 30 min, single use)\", "
                "then retry the same command once they confirm. Do not try an alternative route."
            )

    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail closed
        log(f"ERROR {exc!r}")
        print(json.dumps({"decision": "block", "reason": f"policy guard crashed ({exc.__class__.__name__}); failing closed"}))
