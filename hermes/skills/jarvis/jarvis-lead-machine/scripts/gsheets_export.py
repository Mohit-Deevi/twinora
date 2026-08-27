#!/usr/bin/env python3
"""Stage 5 — write the sales-team tracker to Google Sheets (with a CSV fallback that always works).

Two backends:
  --backend gws     uses the bundled `google-workspace` skill's stored OAuth credentials
                    (<HERMES_HOME>/google_token.json). Creates a spreadsheet, writes the tabs,
                    freezes headers and applies basic formatting.
  --backend csv     writes the same tabs as .csv files next to the run. Always available, zero setup.
                    Use this to check the columns before wiring Google.

Tabs produced:
  Leads      one row per lead — the sheet the sales team actually works in, with owner/status/next-step
             columns left blank for them to fill
  Drafts     the exact copy queued for each lead, so nobody has to guess what was sent
  ICP        the profile the pull was based on, for auditing why a lead is in the list
  Summary    counts, provider, campaign id, run timestamp

Usage:
  python gsheets_export.py --run <run-dir> --backend csv
  python gsheets_export.py --run <run-dir> --backend gws --title "Turgo outbound — test mohit"
  python gsheets_export.py --run <run-dir> --backend gws --share tech@growstack.ai --role writer
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import die, hermes_home, http_json, log, read_json, write_json  # noqa: E402

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_API = "https://www.googleapis.com/drive/v3/files"
TOKEN_URL = "https://oauth2.googleapis.com/token"

LEAD_COLUMNS = [
    ("first_name", "First name"), ("last_name", "Last name"), ("email", "Email"),
    ("job_title", "Title"), ("company_name", "Company"), ("website", "Website"),
    ("industry", "Industry"), ("employee_count", "Employees"), ("location", "Location"),
    ("linkedin_url", "LinkedIn"), ("_signal", "Personalisation signal"), ("_status", "Draft status"),
]
# blank columns the sales team fills in themselves
SALES_COLUMNS = ["Owner", "Status", "Last touch", "Next step", "Notes"]


def rows_leads(drafts: list[dict]) -> list[list]:
    head = [h for _, h in LEAD_COLUMNS] + SALES_COLUMNS
    out = [head]
    for d in drafts:
        dr = d.get("draft", {})
        row = []
        for key, _ in LEAD_COLUMNS:
            if key == "_signal":
                row.append(dr.get("signal_used", ""))
            elif key == "_status":
                row.append(dr.get("status", ""))
            else:
                row.append(str(d.get(key, "") or ""))
        row += [""] * len(SALES_COLUMNS)
        out.append(row)
    return out


def rows_drafts(drafts: list[dict]) -> list[list]:
    out = [["Email", "Company", "Subject", "Opener", "Body", "Status", "Problems"]]
    for d in drafts:
        dr = d.get("draft", {})
        out.append([d.get("email", ""), d.get("company_name", ""), dr.get("subject", ""),
                    dr.get("opener", ""), dr.get("body", ""), dr.get("status", ""),
                    "; ".join(dr.get("problems", []) or [])])
    return out


def rows_icp(icp: dict) -> list[list]:
    d = icp.get("draft_icp", {})
    out = [["Field", "Value"],
           ["Source URL", icp.get("source_url", "")],
           ["Company", icp.get("company", {}).get("name", "")],
           ["Promise", icp.get("company", {}).get("promise", "")],
           ["Departments", ", ".join(d.get("departments", []))],
           ["Titles", ", ".join(d.get("titles_include", []))],
           ["Seniority", ", ".join(d.get("seniority", []))],
           ["Employee count", ", ".join(d.get("employee_count", []))],
           ["Locations", ", ".join(d.get("locations", []))],
           ["Industries", ", ".join(d.get("industries_include", []))],
           ["", ""], ["Proof points", ""]]
    for p in icp.get("proof_points", [])[:15]:
        out.append(["", p])
    return out


def rows_summary(meta: dict) -> list[list]:
    return [["Field", "Value"]] + [[k, str(v)] for k, v in meta.items()]


# --------------------------------------------------------------------------- csv backend

def export_csv(run: pathlib.Path, tabs: dict[str, list[list]]) -> list[str]:
    written = []
    for name, rows in tabs.items():
        p = run / f"tracker-{name.lower()}.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows(rows)
        written.append(str(p))
        log(f"  {p.name}  ({len(rows)-1} rows)")
    return written


# --------------------------------------------------------------------------- google backend

def _token_form(refresh: str, cid: str, csec: str) -> str:
    """Exchange a refresh token for an access token (form-encoded, not JSON)."""
    import urllib.parse
    import urllib.request
    data = urllib.parse.urlencode({"client_id": cid, "client_secret": csec,
                                   "refresh_token": refresh, "grant_type": "refresh_token"}).encode()
    req = urllib.request.Request(TOKEN_URL, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())["access_token"]


def get_token() -> str:
    tok_path = hermes_home() / "google_token.json"
    if not tok_path.exists():
        die(f"{tok_path} not found.\n"
            f"  Authorise the google-workspace skill first:\n"
            f"    python <skills>/productivity/google-workspace/scripts/setup.py --services email,calendar,drive\n"
            f"  Or use --backend csv, which needs no setup at all.")
    tok = read_json(tok_path) or {}
    cid, csec = tok.get("client_id"), tok.get("client_secret")
    if not (cid and csec):
        inst = (read_json(hermes_home() / "google_client_secret.json") or {}).get("installed", {})
        cid, csec = cid or inst.get("client_id"), csec or inst.get("client_secret")
    if not (tok.get("refresh_token") and cid and csec):
        die("google_token.json is missing refresh_token/client_id/client_secret — re-run the skill's setup.py")
    return _token_form(tok["refresh_token"], cid, csec)


def export_gsheets(title: str, tabs: dict[str, list[list]], share: str, role: str, live: bool) -> dict:
    if not live:
        log("  [dry-run] would create a spreadsheet titled: " + title)
        for name, rows in tabs.items():
            log(f"  [dry-run]   tab '{name}': {len(rows)-1} rows x {len(rows[0])} cols")
        return {"_dry_run": True, "title": title}

    token = get_token()
    h = {"Authorization": f"Bearer {token}"}

    sheet = http_json(SHEETS_API, "POST", h, {
        "properties": {"title": title},
        "sheets": [{"properties": {"title": n, "gridProperties": {"frozenRowCount": 1}}} for n in tabs],
    }, timeout=90)
    sid = sheet["spreadsheetId"]
    log(f"  spreadsheet: https://docs.google.com/spreadsheets/d/{sid}")

    data = [{"range": f"'{name}'!A1", "majorDimension": "ROWS", "values": rows}
            for name, rows in tabs.items()]
    http_json(f"{SHEETS_API}/{sid}/values:batchUpdate", "POST", h,
              {"valueInputOption": "RAW", "data": data}, timeout=120)

    # bold the header row of every tab
    reqs = [{"repeatCell": {
        "range": {"sheetId": s["properties"]["sheetId"], "startRowIndex": 0, "endRowIndex": 1},
        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
        "fields": "userEnteredFormat.textFormat.bold"}} for s in sheet["sheets"]]
    http_json(f"{SHEETS_API}/{sid}:batchUpdate", "POST", h, {"requests": reqs}, timeout=90)

    out = {"spreadsheet_id": sid, "url": f"https://docs.google.com/spreadsheets/d/{sid}"}
    if share:
        http_json(f"{DRIVE_API}/{sid}/permissions", "POST", h,
                  {"type": "user", "role": role, "emailAddress": share}, timeout=60)
        out["shared_with"] = share
        log(f"  shared with {share} as {role}")
    return out


# --------------------------------------------------------------------------- cli

def main() -> None:
    ap = argparse.ArgumentParser(description="Export the lead tracker to Google Sheets or CSV")
    ap.add_argument("--run", required=True, help="run directory containing icp.json / leads.json / drafts.json")
    ap.add_argument("--backend", default="csv", choices=["csv", "gws"])
    ap.add_argument("--title", default=None)
    ap.add_argument("--share", default="", help="email address to share the sheet with")
    ap.add_argument("--role", default="writer", choices=["reader", "commenter", "writer"])
    ap.add_argument("--live", action="store_true", help="gws backend: actually create the sheet")
    ap.add_argument("--out")
    a = ap.parse_args()

    run = pathlib.Path(a.run)
    icp = read_json(run / "icp.json") or {}
    leads_doc = read_json(run / "leads.json") or {}
    drafts_doc = read_json(run / "drafts.json") or {}
    drafts = drafts_doc.get("drafts") or [{**l, "draft": {}} for l in leads_doc.get("leads", [])]
    if not drafts:
        die(f"no leads or drafts found in {run}")

    campaign = read_json(run / "campaign.json") or {}
    meta = {
        "Generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "Source site": icp.get("source_url", ""),
        "Lead provider": leads_doc.get("provider", ""),
        "Leads": len(drafts),
        "Drafts ready": drafts_doc.get("ready", ""),
        "Needs review": drafts_doc.get("needs_review", ""),
        "Campaign": campaign.get("campaign_name", ""),
        "Campaign id": campaign.get("campaign_id", ""),
        "Campaign live": campaign.get("live", False),
    }

    tabs = {"Leads": rows_leads(drafts), "Drafts": rows_drafts(drafts),
            "ICP": rows_icp(icp), "Summary": rows_summary(meta)}

    log(f"building tracker from {run}")
    if a.backend == "csv":
        files = export_csv(run, tabs)
        result = {"backend": "csv", "files": files}
    else:
        title = a.title or f"Outbound tracker — {icp.get('company', {}).get('name', 'run')} — " \
                           f"{datetime.datetime.now().strftime('%d %b %Y')}"
        result = {"backend": "gws", **export_gsheets(title, tabs, a.share, a.role, a.live)}

    write_json(pathlib.Path(a.out or run / "tracker.json"), result)
    log(f"\nwrote {a.out or run / 'tracker.json'}")


if __name__ == "__main__":
    main()
