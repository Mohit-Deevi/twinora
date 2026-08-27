---
name: jarvis-lead-machine
description: "Site to ICP to leads to campaign to sheet to WhatsApp."
version: 1.0.0
author: Growstack (tech@growstack.ai), Hermes Agent
license: MIT
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [Outbound, Leads, ICP, Instantly, Apollo, Google-Sheets, Sales, Jarvis]
    related_skills: [jarvis-outreach, google-workspace, jarvis-job-hunter, scrapling]
---

# Jarvis Lead Machine Skill

Turns one sentence — *"scrape turgo.ai, find the right ICP, get 100 leads, personalise the outreach, run
it in Instantly, track it in Sheets, send me the result"* — into six auditable stages. Every stage writes
a JSON artifact, **dry-run is the default**, and no email is ever sent without a separate explicit approval.

## When to Use

- "Scrape <site>, build the ICP, find N leads, draft outreach, set up the campaign, put it in a sheet."
- Re-running one stage after fixing the ICP or the copy.
- Any outbound motion where the sales team needs a tracker they can work in.

Don't use for: replying to inbound (that's `jarvis-morning-brief`), job applications (`jarvis-job-hunter`),
or one-off personal emails.

## Prerequisites

| Need | Where it comes from | Required? |
|---|---|---|
| `INSTANTLY_API_KEY` | Instantly → Settings → Integrations → API keys (v2) | yes — lead source + campaigns |
| `APOLLO_API_KEY` | Apollo → Settings → Integrations → API | optional, **paid plan only** (see below) |
| Google OAuth | `google-workspace` skill setup → `google_token.json` | only for `--sheet gws` |
| Sending mailboxes | already connected inside Instantly | only to actually send |
| Hermes gateway running | `hermes gateway status` | only for WhatsApp delivery |

All keys live in `<HERMES_HOME>/.env` (on this machine: `C:\Users\Growstack\AppData\Local\hermes\.env`).
Scripts read them directly and redact them from every log line.

:::warning Apollo Free plan
Apollo's Free plan returns 403 for `mixed_people/api_search`, `mixed_companies/search`, `people/match`,
`people/bulk_match`, `people/show` and `organizations/show` — i.e. **all search and enrichment**. Run
`lead_sources.py --provider apollo --preflight` to confirm. The default provider is Instantly Supersearch,
which needs no Apollo plan at all.
:::

## How to Run

```
PY="C:/Users/Growstack/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"
cd <skill>/scripts

# 1. full dry run — touches nothing, costs nothing
$PY run_pipeline.py --url https://turgo.ai --limit 100 --name "test mohit"

# 2. read the ICP, fix what the scraper could not know
notepad %LOCALAPPDATA%\hermes\jarvis\leadgen\turgo-ai\icp.json

# 3. live run: real leads, real campaign (PAUSED), sheet, WhatsApp
$PY run_pipeline.py --url https://turgo.ai --limit 100 --name "test mohit" --live \
    --senders you@yourdomain.com --sheet gws --share tech@growstack.ai --notify 919866614377

# 4. only after reading the campaign in Instantly
jarvis approve campaign-activate
$PY instantly_campaign.py --campaign-id <id> --activate --live
```

## Quick Reference

| Stage | Script | What it does | Sends anything? |
|---|---|---|---|
| 1 icp | `icp_builder.py` | scrape site → positioning, proof points, draft ICP | no (free) |
| 2 leads | `lead_sources.py` | count → preview → enrich via Instantly Supersearch (or Apollo) | no; `--live` spends credits |
| 3 drafts | `personalize.py` | one verifiable signal per lead → opener + body, audited | no |
| 4 campaign | `instantly_campaign.py` | create campaign + 3-step sequence, load leads, **leave paused** | no |
| 5 sheet | `gsheets_export.py` | Leads / Drafts / ICP / Summary tabs → Google Sheet or CSV | no |
| 6 notify | `notify.py` | run summary to a WhatsApp number via the local bridge | yes, to the owner only |

Artifacts: `<HERMES_HOME>/jarvis/leadgen/<slug>/{icp,leads,drafts,campaign,tracker}.json` + `report.md`.

Approval keys used: **`lead-pull`** (credit-consuming enrichment), **`campaign-activate`** (starts sending).

## Procedure

### 1. Build the ICP, then correct it
Run stage 1 and **read `icp.json` before anything else**. The scraper can infer departments, titles and
company size from page text; it cannot know which countries or industries the owner actually sells to, so
`locations` and `industries_include` are deliberately left empty. Ask the owner for those two, plus any
exclusions, and either edit `icp.json` or pass `--overrides overrides.json`. A pull with an empty location
filter returns a global pool and wastes credits.

### 2. Source leads cheaply first
Stage 2 always runs `count` and `preview` before `enrich`. Show the owner the pool size and five sample
records. Only then run with `--live`, which requires the `lead-pull` ticket because enrichment consumes
credits. `skip_owned_leads` and `show_one_lead_per_company` are on by default so you never re-pull someone
already in the workspace or hit five people at one company.

### 3. Personalise on evidence, not tokens
Stage 3 picks the strongest **verifiable** signal per lead — company description sentence, title+company,
industry, headcount — and writes a one-three-one email around it. The auditor rejects banned phrases, more
than one link, over-long bodies, and **any number that does not appear in the ICP's proof points**. A lead
with no usable signal is marked `needs_review` rather than getting a generic line. `--mode llm` routes the
writing through the local Hermes API server and falls back to templates on any error.

### 4. Build the campaign paused
Stage 4 creates a 3-step sequence (day 0, +3, +7) with `stop_on_reply`, `stop_for_company`, unsubscribe
header on, and link/open tracking **off** (tracking pixels hurt cold-domain deliverability). Leads are added
with `skip_if_in_workspace` / `skip_if_in_campaign` so re-runs never double-contact. The campaign stays
paused. Activation is a separate command with its own approval.

### 5. Hand the sales team a real tracker
Stage 5 writes four tabs: **Leads** (with blank Owner / Status / Last touch / Next step / Notes columns for
the team), **Drafts** (exact copy per lead), **ICP** (why these leads), **Summary** (counts, campaign id).
`--backend csv` always works; `--backend gws` needs the google-workspace OAuth and can `--share` the sheet.

### 6. Report
Stage 6 sends a WhatsApp summary through the local bridge: counts, campaign state, tracker link, one sample
opener, and the reminder that nothing has been sent. `report.md` holds the same thing for the record.

## Pitfalls

- Running `--live` before reading `icp.json` — you will pull a global, unqualified list and burn credits.
- Assuming Apollo works. On a Free key every search endpoint 403s; run `--preflight` first.
- Activating the campaign in the same breath as creating it. Read the sequence in Instantly first.
- Creating a campaign with no `--senders`: it will exist but cannot send until a mailbox is attached.
- Putting a number in the copy that is not in `proof_points` — the auditor flags it; do not override it.
- Emailing without a real unsubscribe path and a physical identity; keep `insert_unsubscribe_header` on and
  respect CAN-SPAM / GDPR / India DPDP. Opt-outs are honoured immediately, permanently.

## Verification

Done when: `icp.json` has owner-confirmed locations and industries; `leads.json` count matches the requested
limit with a dedupe pass; every ready draft cites a signal and passes the audit; the campaign exists and is
**paused** with the expected step count; the tracker opens and has one row per lead; the WhatsApp summary
arrived; and `report.md` states exactly what was and was not sent.
