# Jarvis on Hermes — Growstack's personal operating system

Hermes Agent is the orchestrator (brain, memory, policy, scheduler). Claude Code is the coding worker.
Gmail, GitHub, web research, image/video generation and social tools are the other workers.
This repo holds everything that turns a stock Hermes install into **Jarvis**: persona, permission model,
skills, automations, and the runbook.

**Start here → `docs/jarvis-runbook.html`** (also published as a private artifact; link in the chat where this was built).

## The Jarvis console (the interface)

```powershell
cd $env:USERPROFILE\Desktop\hermes-project
.\interface\start.ps1          # → http://127.0.0.1:7788  (Chrome/Edge for voice)
```

A floating, futuristic HUD wired to the live Hermes: arc-reactor core = push-to-talk mic, "Hey Jarvis"
wake word, spoken replies, streaming transcript with tool-activity chips, Systems / Approvals / Schedule /
Activity panels, Memory · Drafts · Sessions drawers, and an emergency **Halt**. One-click approval tickets
replace `jarvis approve <key>`. Backend: [interface/server.py](interface/server.py) (stdlib only, loopback only)
talking to Hermes' OpenAI-compatible API server (`API_SERVER_ENABLED=true` in `.env`, port 8642).

## Layout

```
hermes-project/
├── README.md
├── docs/jarvis-runbook.html          the step-by-step runbook (phases 0–8)
├── bin/jarvis.ps1                    owner controls: approve tickets, status, log, pause/resume
├── setup/
│   ├── 01-foundation.ps1             gh, Claude Code CLI, Playwright, PATH, work dirs
│   ├── 02-install-into-hermes.ps1    persona + policy guard + skills + config → %LOCALAPPDATA%\hermes
│   ├── 03-cron-jobs.ps1              morning brief, trend hunter, PR shepherd, digests
│   └── merge-config.py               safe deep-merge into config.yaml (backup first)
└── hermes/                           what gets installed into HERMES_HOME
    ├── SOUL.md                       the Jarvis persona + permission levels
    ├── config.additions.yaml         approvals, hook, delegation, guardrails, memory, terminal, bridge port
    ├── agent-hooks/policy-guard.py   pre_tool_call hook: Level 3 blocked, Level 2 needs a ticket
    ├── jarvis/OWNER.md               owner profile + onboarding interview (Jarvis stores answers in memory)
    └── skills/jarvis/
        ├── jarvis-coding-dispatcher  request → Claude Code → verified PR (never merges)
        ├── jarvis-morning-brief      Gmail + Calendar + GitHub + trends → one phone-sized brief
        ├── jarvis-trend-hunter       multi-source sweep, dedupe, score, angle
        ├── jarvis-creative-director  brief → copy + on-brand image (vision-checked) + manifest
        ├── jarvis-social-publisher   approved package → publish/schedule → read back → ledger
        ├── jarvis-daily-content      the 09:00 post: angle → copy + image (+ UGC video) → virality checklist
        ├── jarvis-outreach           ICP → verified leads → one-three-one emails → follow-ups (send = Level 2)
        ├── jarvis-job-hunter         roles + hirers → verified addresses → tailored applications → tracker
        ├── jarvis-video-studio       FREE short-form video: images/colour cards + Edge TTS + captions via ffmpeg
        └── jarvis-ad-creative        scrape brand → mine hooks → Seedance 2.5 ad (30s, native audio) + captions
```

**Media generation (fal.ai, live).** `FAL_KEY` is set; the `fal` image and video plugins are enabled, so Hermes'
own `image_generate` / `video_generate` tools work, and `jarvis-ad-creative/scripts/fal_media.py` drives
Seedance 2.5 directly (the bundled plugin doesn't know Seedance and rejects it).

| Use | Model | Cost |
|---|---|---|
| Images | `fal-ai/nano-banana-pro` (best text), `seedream-4`, `flux-schnell` (drafts) | cents |
| Video | `bytedance/seedance-2.5/{text,image,reference}-to-video` — 4–30 s, 480p/720p, **native synced audio**, lip-sync for quoted dialogue, up to 50 refs | $0.22/s at 480p, $0.47/s at 720p |

⚠️ The video endpoint owner is `bytedance/…`, **not** `fal-ai/bytedance/…` (the latter queues then 404s).

**Video, honestly.** Higgsfield is a paid platform (not open source). Open models (Wan 2.2, LTX-Video,
HunyuanVideo) need an NVIDIA GPU with 6–24 GB VRAM; this laptop has an AMD iGPU, so they can't run locally.
`jarvis-video-studio` makes real reels today for free; hosted open models become available through
`video_generate` the moment a `FAL_KEY` (or DeepInfra/xAI key) is added.

Hub skills installed from the Hermes Skills Hub (official unless noted): `social-media-content-calendar`,
`meme-generation`, `scrapling`, `one-three-one-rule`, `kanban-video-orchestrator`, `page-agent`,
`duckduckgo-search`, community `ugc`, `content-calendar`, `short-video-agent-kit`, `tiktok`. Community skills
that failed Hermes' security scan (`outreach`, `oo-linkedin`, `heygen`, `lead-generation`) were deliberately not
installed — the Jarvis skills cover those jobs.

**Standing approvals.** `jarvis approve social-post standing` (or the "standing" checkbox in the console) lets a
routine run unattended — e.g. the 09:00 post — until `jarvis revoke social-post`. Single-use tickets remain the
default for everything else.

## Order of operations

1. `.\setup\01-foundation.ps1` then the printed manual logins (`gh auth login`, `claude`, `hermes model`, `hermes fallback`).
2. `.\setup\02-install-into-hermes.ps1` — installs persona, guard, skills, config; restarts the gateway.
3. Talk to Jarvis once to seed memory (script in the runbook, Phase 2).
4. `.\setup\03-cron-jobs.ps1` — automations.
5. Follow the runbook's Phase 4 per worker (email OAuth, image provider, publishing path) as you need them.

## Permission levels (short form)

| Level | Rule | Enforced by |
|---|---|---|
| 0 | read / draft / test / open PR — just do it | SOUL.md |
| 1 | low-risk reversible writes — do it, then report | SOUL.md |
| 2 | merge / publish / send / deploy — ask, then `jarvis approve <key>` | SOUL.md + `policy-guard.py` ticket |
| 3 | money / deletes / force-push / secrets — never | `policy-guard.py` + `approvals.deny` + Hermes hardline blocklist |

Hermes home on this machine: `C:\Users\Growstack\AppData\Local\hermes` (native Windows install, not WSL).
