---
name: jarvis-job-hunter
description: "Find roles and hirers, tailor applications, track them."
version: 1.0.0
author: Growstack (tech@growstack.ai), Hermes Agent
license: MIT
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [Career, Jobs, LinkedIn, Applications, Research, Email, Jarvis]
    related_skills: [jarvis-outreach, google-workspace, grounded-citations, one-three-one-rule, pdf, docx]
---

# Jarvis Job Hunter Skill

Runs the owner's job search like a disciplined recruiter working for him: builds a profile from his résumé and
LinkedIn, finds matching roles and the people who can hire him, discovers real company contact addresses, writes
tailored applications, and tracks every touch. Research, matching and drafting are Level 0. **Sending an
application email is Level 2** (`email-send` ticket or standing approval). **LinkedIn actions are performed in
the owner's own browser session, one at a time, with the owner watching or pre-approving each batch** — bulk
automation of LinkedIn gets accounts restricted, so the skill never does it.

## When to Use

- "Find 5 prospects on LinkedIn who could hire me and message them about my capabilities."
- "Find companies hiring freshers for AI automation / agents / backend AI engineer roles paying 70–80k; find their
  emails and apply on my behalf."
- "Any replies to my applications?" / "Where do my applications stand?"

Don't use for: client outreach (use `jarvis-outreach`) or writing the résumé from scratch (do that first with
the owner, then save it).

## Prerequisites

- Profile: `jarvis/career/PROFILE.md` built from the owner's résumé (PDF/DOCX via `pdf`/`docx` skills) and his
  LinkedIn profile (opened with `browser_*` in his logged-in session, read-only) — skills, projects with proof
  links, education, preferred roles, locations, notice period, salary band, work authorisation.
- `google-workspace` authorised (Gmail search + send) for applications and reply tracking.
- Playwright Chromium installed (for LinkedIn and job boards that need a browser).
- Tracker: `jarvis/career/applications.csv` — company, role, source URL, contact, email, status, sent_at,
  next_follow_up, thread_id, notes. People tracker: `jarvis/career/people.csv`.

## How to Run

Invoked by the owner with a criteria prompt. Follow-ups and reply checks can run from cron
(`"Load jarvis-job-hunter and check application replies and due follow-ups"`, daily 09:30).

## Quick Reference

| Action | Level | Cap |
|---|---|---|
| Search, match, enrich, draft | 0 | — |
| Send application emails | 2 (`email-send`) | 15 / day |
| LinkedIn connection request or message | 2 (owner says yes per batch) + owner's browser session | 10 / day, ≥ 90 s apart |
| Follow-up emails | 2 (standing approval allowed) | 1 per application per week, max 2 |

Salary band interpretation: ask once whether "70k–80k" means INR per month, INR lakhs per annum, or USD per year,
and store the answer in PROFILE.md.

## Procedure

### 1. Build or refresh the profile
If `PROFILE.md` is missing: ask the owner for the résumé file (or path) and open his LinkedIn profile with
`browser_navigate` in his session; extract and write PROFILE.md. Confirm the four filters with `clarify` or a
question: target roles, level (fresher/junior), locations/remote, salary band + currency.

### 2. Find roles — several sources, never one
`web_search` with role + "hiring" + location + recency; open results with `web_extract`. Sources to cover:
LinkedIn Jobs and company career pages via `browser_*`, Wellfound/AngelList, Naukri, Instahyre, Cutshort,
YC jobs, Indeed, Google Jobs, GitHub "hiring" issues and discussions, and funding/launch news (companies that just
raised or launched hire). Record: company, role, URL, location, salary if shown, posted date, requirements.
Discard postings older than 30 days or mismatching the level.

### 3. Score the match
For each role score 0–5 on: skills overlap (from PROFILE.md), level fit, salary fit, location fit, company signal
(recent funding/launch, AI-agent focus). Keep ≥ 16/25. Keep 1–2 "stretch" roles explicitly labelled.

### 4. Find the people and the address
For each kept company: identify 1–2 likely hirers (founder/CTO/head of engineering/recruiter) from the company
site, the posting, and public pages; note their LinkedIn URL. Discover the application address in this order:
the posting's own apply email → careers page → `careers@` / `jobs@` / `hr@` / `hiring@` on the company domain
(verify the domain exists and the site mentions the address) → a named contact's published address. Never guess
personal addresses by pattern and never use scraped/purchased lists. If no address can be verified, route the
application through the posting's apply link (browser) or the LinkedIn "Easy Apply" with the owner present.

### 5. Tailor the application
One email per company, ≤ 180 words: subject "Application — <role> — <owner name>"; opening line referencing
something specific about the company; three lines mapping PROFILE.md proof (Turgo, voice agents, unified inbox,
automation nodes, repos) to their requirements; salary/availability line only if they asked; a clear close.
Attach the résumé PDF. Save to `jarvis/career/outbox/<date>-<company>.md`.

### 6. LinkedIn prospects (when asked)
Pick 5 people from step 4 with the strongest fit. For each: a connection note ≤ 300 characters (who he is, one
proof point, why them), and a follow-up message (≤ 90 words) to send after they accept. Show the batch. On the
owner's yes, use `browser_*` in his logged-in session: open the profile, screenshot, click Connect → Add note →
paste → screenshot → Send; wait ≥ 90 s between people; stop at 10/day or at any LinkedIn warning. Log each in
`people.csv`.

### 7. Send and track
On approval (ticket or standing): send via `google-workspace`, read back the message id, set `status: applied`,
`next_follow_up: +7d`. Daily: search Gmail for replies on tracked threads → `status: replied/interview/rejected`;
send at most two polite follow-ups a week apart; never contact anyone who declined.

### 8. Report
Table: company · role · fit score · contact · channel · status. Then the 3 best opportunities with why, and what
needs the owner (interview slots, salary answer, documents).

## Pitfalls

- Guessing email addresses or scraping contact lists — reputational risk and often illegal; verify or use the apply link.
- Automating LinkedIn in the background — account restriction; always the owner's session, slowly, with his yes.
- One generic cover letter for everyone — the tailoring is the whole point.
- Applying to postings older than 30 days or above the stated level.
- Forgetting the attachment or sending from the wrong address.

## Verification

Done when every application row has a verified source URL, a verified address or apply path, a tailored draft,
an approval trace for each send, and a follow-up date.
