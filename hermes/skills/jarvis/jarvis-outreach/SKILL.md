---
name: jarvis-outreach
description: "Find leads, draft personal outreach, track replies."
version: 1.0.0
author: Growstack (tech@growstack.ai), Hermes Agent
license: MIT
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [Outreach, Sales, Leads, Email, Research, Jarvis]
    related_skills: [google-workspace, email-inbox-triage, grounded-citations, one-three-one-rule]
---

# Jarvis Outreach Skill

Research-first outbound: find people who match the owner's ideal customer profile, learn something true about each
one, write a short personal message, and track what happens. Drafting and research are Level 0. **Sending is
Level 2** — a single-use `email-send` ticket per batch, or a standing approval for a sequence the owner has read
and signed off. Quality over volume: a hard cap of 20 new contacts per day.

## When to Use

- "Find 10 SaaS founders in Bangalore who might need X and draft intros."
- "Follow up with everyone who didn't reply last week."
- "Who replied to the outreach? Summarise."

Don't use for: newsletters/broadcasts (not personal), or anyone who has opted out.

## Prerequisites

- `google-workspace` authorised (Gmail send + search) — or `himalaya`.
- ICP contract at `jarvis/outreach/icp.json` (created on first run via `clarify`): who, where, company size,
  triggers (hiring, funding, launches), exclusions, the offer in one sentence, tone, and the owner's signature.
- Optional lead sources: Apollo MCP server (`hermes mcp add …`) if the owner has an account; otherwise
  `web_search` + company sites + public directories. LinkedIn only through the owner's own browser session via
  `browser_*` tools, read-only, respecting rate limits — never scraped in bulk.
- State: `jarvis/outreach/leads.csv` (one row per person: name, role, company, source URL, trigger, status,
  last_touch, next_touch, thread_id) and `jarvis/outreach/outbox/<date>-<slug>.md` drafts.

## How to Run

Invoked by the owner, or by cron for follow-ups (`"Load jarvis-outreach and process due follow-ups"`, daily 10:00).

## Quick Reference

Message rule: **one-three-one** — 1 sentence why them (specific, verifiable), 3 sentences of value (what, proof,
why now), 1 question as the ask. Under 120 words. No attachments, one link at most, no marketing adjectives.

| Step | Level |
|---|---|
| Research, enrich, draft, log | 0 |
| Send a batch / schedule follow-ups | 2 (`email-send` ticket or standing approval) |
| Opt-out handling | 1 — immediate, always |

## Procedure

### 1. Load or create the ICP contract
Missing → ask the owner once (one message, all fields). Write `icp.json`. Re-read on every run.

### 2. Source candidates
Use at least two modalities: `web_search` with trigger phrases ("hiring", "raised", "launched", "looking for"),
company newsrooms/blogs via `web_extract`, directories the ICP names, Apollo (if configured). Stop at 3× the
daily cap. Deduplicate against `leads.csv` (email or name+company).

### 3. Enrich and qualify
For each candidate open ≥1 primary source (their site, post, announcement). Record the specific trigger with URL.
Discard anyone without a verifiable trigger — "seems relevant" is not a trigger.

### 4. Draft
One-three-one, in the owner's voice (brand kit + memory). Reference the trigger in sentence one. Subject line ≤ 6
words, no clickbait. Save to `outbox/` and mark `status: drafted`.

### 5. Approval and send
Present the batch (name, company, trigger, subject, first line) and ask. On approval (ticket or standing): send
via `google-workspace`, read back the sent message id, set `status: sent`, `last_touch`, `next_touch = +3d`.
Never exceed 20 sends/day; never send between 21:00 and 08:00 recipient-local time.

### 6. Follow-ups and replies
Daily: `google-workspace` search for replies on tracked threads → `status: replied` and summarise for the owner.
Due follow-ups get one short nudge (≤ 3 sentences) at +3d and a final one at +7d; then `status: closed`.
Any reply that says stop/unsubscribe/not interested → `status: opted_out`, never contact again.

### 7. Report
Counts (sourced / qualified / drafted / sent / replied), the best reply verbatim, and the next action.

## Pitfalls

- Generic first lines ("I came across your profile") — if it could be sent to anyone, rewrite it.
- Sending without a ticket; the policy guard blocks it — ask, don't route around.
- Scraping LinkedIn or buying lists — reputational and legal risk; not permitted.
- Ignoring local law: include the owner's real identity and honour opt-outs (CAN-SPAM / GDPR / India DPDP).
- Follow-ups that add nothing new.

## Verification

Done when every contacted person has a `leads.csv` row with source URL, trigger, status and thread id, and every
send can be traced to an approval.
