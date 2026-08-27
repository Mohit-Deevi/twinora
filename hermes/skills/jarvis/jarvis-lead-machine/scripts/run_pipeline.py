#!/usr/bin/env python3
"""Orchestrator — one command runs the whole lead machine, stage by stage.

    site  ->  ICP  ->  leads  ->  personalised drafts  ->  Instantly campaign (paused)  ->  tracker  ->  WhatsApp

Everything is **dry-run by default**. Each stage writes a JSON artifact into the run directory, so you
can stop after any stage, inspect the file, fix something, and resume with --from without redoing work.

  <HERMES_HOME>/jarvis/leadgen/<slug>/
      icp.json  leads.json  drafts.json  campaign.json  tracker.json  report.md

Stages: icp -> leads -> drafts -> campaign -> sheet -> notify

Usage:
  # full dry run, nothing touched
  python run_pipeline.py --url https://turgo.ai --limit 100 --name "test mohit"

  # for real, up to the paused campaign + sheet + WhatsApp
  python run_pipeline.py --url https://turgo.ai --limit 100 --name "test mohit" \
      --live --senders you@yourdomain.com --sheet gws --notify 919866614377

  # resume from one stage
  python run_pipeline.py --slug turgo-ai --from drafts --live

Sending is never started here. Activating the campaign is a separate, explicitly approved command
printed at the end of the run.
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import die, hermes_home, log, read_json, run_dir, slugify  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
STAGES = ["icp", "leads", "drafts", "campaign", "sheet", "notify"]


def python_exe() -> str:
    """Prefer the Hermes venv interpreter so dependencies match the rest of the system."""
    venv = hermes_home() / "hermes-agent" / "venv" / ("Scripts" if sys.platform == "win32" else "bin") / \
        ("python.exe" if sys.platform == "win32" else "python")
    return str(venv) if venv.exists() else sys.executable


def run(script: str, args: list[str]) -> int:
    cmd = [python_exe(), str(HERE / script), *args]
    log(f"\n$ {script} {' '.join(args)}")
    proc = subprocess.run(cmd, text=True)
    if proc.returncode != 0:
        log(f"  ! {script} exited {proc.returncode}")
    return proc.returncode


def report(rd: pathlib.Path, name: str, live: bool) -> str:
    icp = read_json(rd / "icp.json") or {}
    leads = read_json(rd / "leads.json") or {}
    drafts = read_json(rd / "drafts.json") or {}
    camp = read_json(rd / "campaign.json") or {}
    track = read_json(rd / "tracker.json") or {}

    lines = [
        f"# Outbound run — {icp.get('company', {}).get('name', name)}",
        f"_{datetime.datetime.now().strftime('%d %b %Y %H:%M')} · {'LIVE' if live else 'DRY RUN'}_",
        "",
        f"**Source** {icp.get('source_url', '—')}",
        f"**Provider** {leads.get('provider', '—')}  ·  **Leads** {leads.get('count', 0)} "
        f"({leads.get('with_email', 0)} with email)",
        f"**Drafts** {drafts.get('ready', 0)} ready, {drafts.get('needs_review', 0)} need review",
        f"**Campaign** {camp.get('campaign_name', '—')} `{camp.get('campaign_id', '—')}` "
        f"({'ACTIVE' if camp.get('activate_response') else 'paused'})",
        f"**Tracker** {track.get('url') or ', '.join(track.get('files', [])) or '—'}",
        "",
        "## ICP used",
    ]
    d = icp.get("draft_icp", {})
    for k in ("departments", "titles_include", "seniority", "employee_count", "locations", "industries_include"):
        v = d.get(k)
        if v:
            lines.append(f"- **{k}**: {', '.join(map(str, v))}")
    sample = next((x for x in drafts.get("drafts", []) if x.get("draft", {}).get("status") == "ok"), None)
    if sample:
        lines += ["", "## Sample message", "",
                  f"**To** {sample.get('first_name')} {sample.get('last_name')} — "
                  f"{sample.get('job_title')} @ {sample.get('company_name')}",
                  f"**Subject** {sample['draft'].get('subject','')}", "",
                  "```", sample["draft"].get("body", ""), "```"]
    lines += ["", "## Next step", "",
              "Nothing has been sent. To start sending after reviewing the campaign in Instantly:", "",
              "```", "jarvis approve campaign-activate",
              f"python instantly_campaign.py --campaign-id {camp.get('campaign_id', '<id>')} --activate --live",
              "```"]
    text = "\n".join(lines)
    (rd / "report.md").write_text(text, encoding="utf-8")
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the whole lead machine")
    ap.add_argument("--url", help="company site to derive the ICP from")
    ap.add_argument("--slug", help="reuse an existing run directory")
    ap.add_argument("--name", default="test mohit", help="Instantly campaign name")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--provider", default="instantly", choices=["instantly", "apollo"])
    ap.add_argument("--mode", default="template", choices=["template", "llm"])
    ap.add_argument("--sender", default="Mohit")
    ap.add_argument("--company", default="Turgo")
    ap.add_argument("--senders", default="", help="Instantly sending mailboxes, comma separated")
    ap.add_argument("--timezone", default="Asia/Kolkata")
    ap.add_argument("--daily-limit", type=int, default=30)
    ap.add_argument("--sheet", default="csv", choices=["csv", "gws", "none"])
    ap.add_argument("--share", default="", help="email to share the Google Sheet with")
    ap.add_argument("--notify", default="", help="WhatsApp number to send the summary to (digits, with country code)")
    ap.add_argument("--overrides", help="JSON file of ICP overrides (locations, industries, titles…)")
    ap.add_argument("--from", dest="from_stage", default="icp", choices=STAGES)
    ap.add_argument("--only", default="", help="comma-separated subset of stages to run")
    ap.add_argument("--live", action="store_true", help="let stages call real APIs (still never activates)")
    a = ap.parse_args()

    if not a.url and not a.slug:
        die("give --url (new run) or --slug (resume)")
    slug = a.slug or slugify(a.url.replace("https://", "").replace("http://", ""))
    rd = run_dir(slug)
    log(f"run directory: {rd}")
    log(f"mode: {'LIVE' if a.live else 'DRY RUN (nothing is sent or charged)'}")

    wanted = [s.strip() for s in a.only.split(",") if s.strip()] or STAGES[STAGES.index(a.from_stage):]
    log(f"stages: {' -> '.join(wanted)}")

    if "icp" in wanted:
        if not a.url:
            die("--url is required to build the ICP (or --from leads to skip it)")
        if run("icp_builder.py", ["--url", a.url, "--out", str(rd / "icp.json")]):
            die("ICP stage failed")
        log("\n  >>> Read icp.json now. locations/industries are deliberately empty — "
            "fill them in an overrides file and pass --overrides, or the pull will be global.")

    if "leads" in wanted:
        args = ["--icp", str(rd / "icp.json"), "--out", str(rd / "leads.json"),
                "--provider", a.provider, "--limit", str(a.limit), "--list-name", a.name]
        if a.overrides:
            args += ["--overrides", a.overrides]
        if a.live:
            args.append("--live")
        if run("lead_sources.py", args):
            die("lead stage failed")

    if "drafts" in wanted:
        args = ["--icp", str(rd / "icp.json"), "--leads", str(rd / "leads.json"),
                "--out", str(rd / "drafts.json"), "--mode", a.mode,
                "--sender", a.sender, "--company", a.company]
        if run("personalize.py", args):
            die("draft stage failed")

    if "campaign" in wanted:
        args = ["--drafts", str(rd / "drafts.json"), "--name", a.name,
                "--timezone", a.timezone, "--daily-limit", str(a.daily_limit),
                "--out", str(rd / "campaign.json")]
        if a.senders:
            args += ["--senders", a.senders]
        if a.live:
            args.append("--live")
        if run("instantly_campaign.py", args):
            log("  ! campaign stage failed — continuing so the tracker still gets built")

    if "sheet" in wanted and a.sheet != "none":
        args = ["--run", str(rd), "--backend", a.sheet]
        if a.share:
            args += ["--share", a.share]
        if a.live:
            args.append("--live")
        run("gsheets_export.py", args)

    text = report(rd, a.name, a.live)
    log("\n" + "=" * 70)
    log(text)
    log("=" * 70)

    if "notify" in wanted and a.notify:
        args = ["--to", a.notify, "--run", str(rd)]
        if a.live:
            args.append("--live")
        run("notify.py", args)

    log(f"\nartifacts in {rd}")


if __name__ == "__main__":
    main()
