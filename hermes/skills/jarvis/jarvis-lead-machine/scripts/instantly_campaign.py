#!/usr/bin/env python3
"""Stage 4 — build the Instantly campaign and push the leads in.

Safety model (the whole point of this file):
  * Creating a campaign and adding leads is reversible -> allowed with --live.
  * **Activating a campaign starts sending real email to real people.** That is irreversible, so it
    needs `--activate` AND the `campaign-activate` approval ticket. Default behaviour leaves the
    campaign PAUSED so the owner can open Instantly and read it before anything goes out.
  * Leads are added with skip_if_in_workspace / skip_if_in_campaign so a re-run never double-contacts.

Sequence shape follows the v2 schema exactly:
  sequences[0].steps[] with {type, delay, delay_unit, variants[{subject, body}]}
  campaign_schedule.schedules[] with {name, timing{from,to}, days{0..6}, timezone}

Usage:
  python instantly_campaign.py --drafts <run>/drafts.json --name "test mohit"                 # dry-run
  python instantly_campaign.py --drafts <run>/drafts.json --name "test mohit" --live          # create + add, paused
  python instantly_campaign.py --campaign-id <id> --activate --live                           # start sending (gated)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (ApiError, chunked, die, http_json, log, read_json,  # noqa: E402
                    require_approval, require_env, write_json)

BASE = "https://api.instantly.ai/api/v2"
HOW = "Instantly → Settings → Integrations → API keys (v2). Paste it into <HERMES_HOME>/.env"

DAYS_MON_FRI = {"0": False, "1": True, "2": True, "3": True, "4": True, "5": True, "6": False}


def headers() -> dict:
    return {"Authorization": f"Bearer {require_env('INSTANTLY_API_KEY', HOW)}"}


def build_schedule(tz: str, start: str | None, end: str | None,
                   from_hh: str, to_hh: str) -> dict:
    sched = {"schedules": [{"name": "Business hours",
                            "timing": {"from": from_hh, "to": to_hh},
                            "days": DAYS_MON_FRI,
                            "timezone": tz}]}
    if start:
        sched["start_date"] = start
    if end:
        sched["end_date"] = end
    return sched


def build_sequence(subject: str, followups: list[tuple[int, str, str]]) -> list[dict]:
    """Step 1 uses the per-lead {{personalization}} variable; follow-ups are shared copy."""
    steps = [{
        "type": "email", "delay": 0, "delay_unit": "days",
        "variants": [{
            "subject": subject,
            "body": ("{{personalization}}\n\n"
                     "Happy to send a 90-second walkthrough instead of a call if that is easier.\n\n"
                     "If this is not your area, tell me who to speak to and I will stop emailing you."),
        }],
    }]
    for delay, subj, body in followups:
        steps.append({"type": "email", "delay": delay, "delay_unit": "days",
                      "variants": [{"subject": subj, "body": body}]})
    return [{"steps": steps}]


DEFAULT_FOLLOWUPS = [
    (3, "", "Bumping this once in case it slipped.\n\n"
            "One line is enough — worth a look, or not right now?"),
    (7, "", "Last one from me.\n\n"
            "If the timing is wrong I will close the loop and stop here. "
            "If it is worth a look later, say when and I will come back then."),
]


def create_campaign(name: str, sequence: list[dict], schedule: dict, sender_emails: list[str],
                    daily_limit: int, live: bool) -> dict:
    body = {
        "name": name,
        "campaign_schedule": schedule,
        "sequences": sequence,
        "email_list": sender_emails,
        "daily_limit": daily_limit,
        "stop_on_reply": True,           # never keep emailing someone who answered
        "stop_on_auto_reply": True,
        "link_tracking": False,          # link tracking hurts deliverability on cold domains
        "open_tracking": False,
        "text_only": True,
        "insert_unsubscribe_header": True,
        "stop_for_company": True,        # one conversation per company, not per person
        "prioritize_new_leads": True,
    }
    return http_json(f"{BASE}/campaigns", "POST", headers(), body, dry_run=not live)


def add_leads(campaign_id: str, drafts: list[dict], live: bool, batch: int = 20) -> dict:
    """One lead per call (v2 POST /leads), batched with a running tally."""
    added, skipped, failed = 0, 0, []
    ready = [d for d in drafts if d.get("draft", {}).get("status") == "ok" and d.get("email")]
    log(f"  {len(ready)} of {len(drafts)} drafts are ready and have an email")

    for group in chunked(ready, batch):
        for d in group:
            payload = {
                "campaign": campaign_id,
                "email": d["email"],
                "first_name": d.get("first_name") or "",
                "last_name": d.get("last_name") or "",
                "company_name": d.get("company_name") or "",
                "job_title": d.get("job_title") or "",
                "website": d.get("website") or "",
                "personalization": d["draft"]["body"],
                "skip_if_in_workspace": True,
                "skip_if_in_campaign": True,
                "verify_leads_on_import": True,
                "custom_variables": {
                    "opener": d["draft"].get("opener", ""),
                    "signal": d["draft"].get("signal_used", ""),
                    "industry": d.get("industry", ""),
                    "linkedin_url": d.get("linkedin_url", ""),
                },
            }
            try:
                res = http_json(f"{BASE}/leads", "POST", headers(), payload, dry_run=not live)
                if isinstance(res, dict) and res.get("skipped"):
                    skipped += 1
                else:
                    added += 1
            except ApiError as e:
                failed.append({"email": d["email"], "status": e.status, "detail": e.body[:160]})
    return {"added": added, "skipped": skipped, "failed": failed}


def activate(campaign_id: str, live: bool) -> dict:
    require_approval("campaign-activate",
                     f"activating Instantly campaign {campaign_id} — this starts sending real email")
    return http_json(f"{BASE}/campaigns/{campaign_id}/activate", "POST", headers(), {}, dry_run=not live)


def main() -> None:
    ap = argparse.ArgumentParser(description="Create an Instantly campaign and load leads (paused by default)")
    ap.add_argument("--drafts", help="drafts.json from stage 3")
    ap.add_argument("--name", default="test mohit")
    ap.add_argument("--subject", default="{{companyName}} + Turgo")
    ap.add_argument("--senders", default="", help="comma-separated sending mailboxes already connected in Instantly")
    ap.add_argument("--timezone", default="Asia/Kolkata")
    ap.add_argument("--from-hour", default="09:00")
    ap.add_argument("--to-hour", default="18:00")
    ap.add_argument("--start-date", default=None, help="YYYY-MM-DD")
    ap.add_argument("--daily-limit", type=int, default=30)
    ap.add_argument("--campaign-id", help="skip creation and act on an existing campaign")
    ap.add_argument("--activate", action="store_true", help="start sending (needs approval + --live)")
    ap.add_argument("--live", action="store_true", help="actually call the API (default is dry-run)")
    ap.add_argument("--out")
    a = ap.parse_args()

    senders = [s.strip() for s in a.senders.split(",") if s.strip()]
    result: dict = {"live": a.live, "campaign_name": a.name}

    campaign_id = a.campaign_id
    if not campaign_id:
        if not a.drafts:
            die("--drafts is required unless --campaign-id is given")
        if not senders:
            log("  ! no --senders given. Instantly needs at least one connected mailbox to send from;\n"
                "    the campaign will be created but cannot send until you attach one in the UI.")
        schedule = build_schedule(a.timezone, a.start_date, None, a.from_hour, a.to_hour)
        sequence = build_sequence(a.subject, DEFAULT_FOLLOWUPS)
        log(f"creating campaign '{a.name}' ({len(sequence[0]['steps'])} steps, "
            f"{a.daily_limit}/day, {a.timezone} {a.from_hour}-{a.to_hour})")
        res = create_campaign(a.name, sequence, schedule, senders, a.daily_limit, a.live)
        campaign_id = (res or {}).get("id") or (res or {}).get("campaign_id")
        result["create_response"] = res
        result["campaign_id"] = campaign_id
        if a.live and not campaign_id:
            die(f"campaign creation returned no id: {json.dumps(res)[:300]}")
        log(f"  campaign_id = {campaign_id or '(dry-run)'}")

    if a.drafts:
        drafts = (read_json(pathlib.Path(a.drafts)) or {}).get("drafts", [])
        log(f"\nadding leads to {campaign_id or '(dry-run)'}")
        result["leads"] = add_leads(campaign_id or "DRY_RUN_CAMPAIGN", drafts, a.live)
        log(f"  added={result['leads']['added']} skipped={result['leads']['skipped']} "
            f"failed={len(result['leads']['failed'])}")
        for f in result["leads"]["failed"][:5]:
            log(f"    ! {f['email']}: HTTP {f['status']} {f['detail'][:80]}")

    if a.activate:
        if not campaign_id:
            die("--activate needs a campaign id")
        log(f"\nactivating {campaign_id}")
        result["activate_response"] = activate(campaign_id, a.live)
        log("  campaign ACTIVE — email is now going out on the schedule above")
    else:
        log("\n  campaign left PAUSED. Review it in Instantly, then run with "
            "--campaign-id <id> --activate --live (needs `jarvis approve campaign-activate`).")

    if a.out:
        write_json(pathlib.Path(a.out), result)
        log(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
