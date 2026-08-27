#!/usr/bin/env python3
"""Stage 3 — draft a personalised opener for every lead.

Personalisation here means **one verifiable, lead-specific fact** in the first line, never a mail-merge
of {{first_name}}. Two modes:

  --mode template   deterministic, free, no LLM. Picks the strongest available signal per lead
                    (job title + company, industry, headcount, a phrase from their company description)
                    and slots it into a one-three-one frame. Fully auditable.
  --mode llm        asks the local Hermes API server to write the opener (better prose, needs the
                    gateway running with API_SERVER_ENABLED=true). Falls back to template on any error.

Output feeds two places:
  * Instantly custom variables, so the sequence body can use {{personalization}}
  * the Google Sheet, so the sales team can read exactly what each person was sent

Rules enforced in code, not left to the model:
  * no claim that is not in icp.json's proof_points
  * opener <= 220 chars, whole email <= 120 words, one link maximum
  * a lead with no usable signal is marked needs_review instead of getting a generic line

Usage:
  python personalize.py --icp <run>/icp.json --leads <run>/leads.json --out <run>/drafts.json
  python personalize.py ... --mode llm --sender "Mohit" --company "Turgo"
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import textwrap

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ApiError, env_value, http_json, log, read_json, write_json  # noqa: E402

MAX_OPENER = 220
MAX_WORDS = 120

BANNED = ["game-changer", "revolutionary", "synergy", "leverage our", "circle back", "touch base",
          "i hope this email finds you well", "quick question", "just following up"]


# --------------------------------------------------------------------------- signals

def signals(lead: dict) -> list[tuple[str, str]]:
    """Ranked, verifiable facts about this lead. Strongest first."""
    out: list[tuple[str, str]] = []
    desc = (lead.get("company_description") or "").strip()
    if desc:
        first = re.split(r"(?<=[.!?])\s", desc)[0].strip().rstrip(".")
        if 20 < len(first) < 180:
            out.append(("description", first))
    if lead.get("job_title") and lead.get("company_name"):
        out.append(("role", f"you run {lead['job_title'].lower()} at {lead['company_name']}"))
    if lead.get("industry") and lead.get("company_name"):
        out.append(("industry", f"{lead['company_name']} is in {lead['industry'].lower()}"))
    ec = str(lead.get("employee_count") or "").strip()
    if ec and ec not in ("0", "?"):
        out.append(("size", f"a {ec}-person team"))
    return out


def opener_from_signal(kind: str, value: str, lead: dict) -> str:
    first = (lead.get("first_name") or "there").strip()
    company = (lead.get("company_name") or "your team").strip()
    if kind == "description":
        return f"Hi {first} — saw that {company} {value[0].lower() + value[1:]}."
    if kind == "role":
        return f"Hi {first} — {value}, so this is probably your call more than anyone's."
    if kind == "industry":
        return f"Hi {first} — {value}, which is exactly where this tends to bite."
    if kind == "size":
        return f"Hi {first} — with {value} at {company}, headcount is the constraint, not ambition."
    return f"Hi {first} —"


# --------------------------------------------------------------------------- body

def pick_proof(icp: dict, n: int = 2) -> list[str]:
    pts = [p.strip() for p in icp.get("proof_points", []) if 15 < len(p) < 160]
    return pts[:n]


def template_email(lead: dict, icp: dict, sender: str, company: str, cta: str) -> dict:
    sigs = signals(lead)
    if not sigs:
        return {"status": "needs_review", "reason": "no verifiable signal for this lead",
                "opener": "", "subject": "", "body": ""}
    kind, value = sigs[0]
    opener = opener_from_signal(kind, value, lead)[:MAX_OPENER]

    promise = (icp.get("company", {}).get("promise") or "").strip().rstrip(".")
    proof = pick_proof(icp, 2)
    proof_line = proof[0] if proof else ""

    body = "\n\n".join(x for x in [
        opener,
        f"{company} {promise[0].lower() + promise[1:] if promise else 'does the work of a full GTM team'}.",
        proof_line,
        cta,
        f"— {sender}",
    ] if x)

    subject = f"{lead.get('company_name','your team')} + {company}"[:70]
    return {"status": "ok", "signal_used": kind, "signal_value": value,
            "opener": opener, "subject": subject, "body": trim_words(body, MAX_WORDS)}


def trim_words(text: str, limit: int) -> str:
    words = text.split()
    return text if len(words) <= limit else " ".join(words[:limit]).rstrip(",;") + "…"


# --------------------------------------------------------------------------- llm mode

def llm_email(lead: dict, icp: dict, sender: str, company: str, cta: str) -> dict | None:
    """Ask the local Hermes API server for a better opener. Returns None on any problem."""
    key = env_value("API_SERVER_KEY")
    if not key:
        return None
    base = f"http://{env_value('API_SERVER_HOST', '127.0.0.1')}:{env_value('API_SERVER_PORT', '8642')}"
    facts = {
        "lead": {k: lead.get(k) for k in ("first_name", "job_title", "company_name", "industry",
                                          "employee_count", "company_description", "location")},
        "our_company": company,
        "our_promise": icp.get("company", {}).get("promise", ""),
        "allowed_proof_points": pick_proof(icp, 4),
        "cta": cta,
        "sender": sender,
    }
    prompt = (
        "Write one cold outreach email. Return ONLY minified JSON: "
        '{"subject":"...","opener":"...","body":"..."}\n'
        "Rules: opener must reference ONE specific verifiable fact about THIS lead from the data below; "
        "no greeting cliches; body under 120 words; use at most one claim and it MUST come verbatim in "
        "meaning from allowed_proof_points; plain sentences, no markdown, no emoji; end with the cta then "
        f"'— {sender}'. If there is no specific fact worth citing, return "
        '{"subject":"","opener":"","body":"","skip":true}\n\nDATA:\n'
        + json.dumps(facts, ensure_ascii=False)
    )
    try:
        res = http_json(f"{base}/v1/responses", "POST",
                        {"Authorization": f"Bearer {key}"},
                        {"model": "hermes-agent", "input": prompt, "store": False}, timeout=120)
    except (ApiError, Exception):
        return None
    text = ""
    for item in (res.get("output") or []):
        if item.get("type") == "message":
            text = "".join(c.get("text", "") for c in item.get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    if d.get("skip") or not d.get("body"):
        return None
    return {"status": "ok", "signal_used": "llm", "signal_value": "",
            "opener": (d.get("opener") or "")[:MAX_OPENER],
            "subject": (d.get("subject") or "")[:70],
            "body": trim_words(d.get("body", ""), MAX_WORDS)}


# --------------------------------------------------------------------------- checks

def audit(draft: dict, icp: dict) -> list[str]:
    problems = []
    text = f"{draft.get('subject','')} {draft.get('body','')}".lower()
    for phrase in BANNED:
        if phrase in text:
            problems.append(f"banned phrase: {phrase}")
    if len(draft.get("body", "").split()) > MAX_WORDS:
        problems.append("body over word limit")
    if draft.get("body", "").count("http") > 1:
        problems.append("more than one link")
    # every % / number in the body must appear in a proof point
    allowed = " ".join(icp.get("proof_points", []))
    for num in set(re.findall(r"\b\d[\d,.]*%?", draft.get("body", ""))):
        if len(num) > 1 and num not in allowed:
            problems.append(f"unverified number: {num}")
    return problems


# --------------------------------------------------------------------------- cli

def main() -> None:
    ap = argparse.ArgumentParser(description="Draft personalised outreach per lead")
    ap.add_argument("--icp", required=True)
    ap.add_argument("--leads", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", default="template", choices=["template", "llm"])
    ap.add_argument("--sender", default="Mohit")
    ap.add_argument("--company", default="Turgo")
    ap.add_argument("--cta", default="Worth a 15-minute look this week?")
    ap.add_argument("--limit", type=int, default=0, help="only draft the first N (0 = all)")
    a = ap.parse_args()

    icp = read_json(pathlib.Path(a.icp)) or {}
    leads = (read_json(pathlib.Path(a.leads)) or {}).get("leads", [])
    if a.limit:
        leads = leads[:a.limit]
    if not leads:
        log("no leads to draft"); return

    drafts, ok, review = [], 0, 0
    for i, lead in enumerate(leads, 1):
        d = None
        if a.mode == "llm":
            d = llm_email(lead, icp, a.sender, a.company, a.cta)
            if d is None:
                log(f"  [{i}] llm unavailable/skipped -> template")
        if d is None:
            d = template_email(lead, icp, a.sender, a.company, a.cta)
        d["problems"] = audit(d, icp) if d["status"] == "ok" else []
        if d["problems"]:
            d["status"] = "needs_review"
            d["reason"] = "; ".join(d["problems"])
        drafts.append({**lead, "draft": d})
        ok += d["status"] == "ok"
        review += d["status"] != "ok"

    log(f"\n  drafted {len(drafts)}  |  ready {ok}  |  needs_review {review}")
    sample = next((d for d in drafts if d["draft"]["status"] == "ok"), None)
    if sample:
        log("\n  --- sample ---")
        log(f"  to: {sample.get('first_name')} {sample.get('last_name')} ({sample.get('job_title')} @ {sample.get('company_name')})")
        log(f"  subject: {sample['draft']['subject']}")
        for line in textwrap.wrap(sample["draft"]["body"], 96)[:10]:
            log("  " + line)

    write_json(pathlib.Path(a.out), {"mode": a.mode, "count": len(drafts), "ready": ok,
                                     "needs_review": review, "drafts": drafts})
    log(f"\nwrote {a.out}")
    log("  Nothing has been sent. Sending happens only in instantly_campaign.py with --live and an approval.")


if __name__ == "__main__":
    main()
