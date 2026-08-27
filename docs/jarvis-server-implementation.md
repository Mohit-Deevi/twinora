# Jarvis on Hermes — Server Implementation Specification

**Owner:** Mohit, Growstack / Turgo
**Status:** Draft for build
**Date:** 25 August 2026

---

## 1. What we are building and why

Today Jarvis runs on one Windows laptop. That works, but it means the assistant is off when the laptop is off, only one person can use it, nothing survives a reinstall, and no product can call it.

This document specifies moving the Hermes runtime onto a company server and exposing it as an authenticated HTTP API, so it becomes a service rather than a personal tool — while keeping the one thing the laptop uniquely provides: the ability to act on Mohit's own machine and inside his logged-in browser.

**The result:** an OpenAI-compatible endpoint your applications call, backed by the full Hermes agent — 90+ skills, memory, tool use, cron, image generation — with an optional secure bridge back to a workstation for local actions.

### Success criteria

| # | Criterion | How it is measured |
|---|---|---|
| 1 | Any backend service can call Jarvis over HTTPS | `POST /v1/responses` returns a completed run |
| 2 | Full tool capability, not just chat | A request that needs `terminal` executes and reports real output |
| 3 | Survives reboot, crash, and deploy unattended | Container restarts automatically; health endpoint green within 120s |
| 4 | No secret ever reaches a browser or a repo | Secrets only in server-side `.env`, injected as environment variables |
| 5 | A failed dependency degrades, never hangs | Model fallback configured; upstream timeout capped below the edge timeout |
| 6 | Actions on Mohit's laptop still possible | SSH terminal backend and CDP browser bridge reachable only over a private mesh |

### Explicit non-goals

- Not a multi-tenant SaaS. One organisation, trusted internal callers.
- Not a public chatbot. The API key grants command execution; it is a backend-to-backend credential.
- Not replacing the laptop instance. The laptop keeps running for personal, interactive work.

---

## 2. Architecture

```
   your app / Turgo backend
            │  HTTPS + Bearer
            ▼
   ┌─────────────────────┐        company server (Linux, Docker)
   │  Caddy  :443        │        - automatic TLS
   │  TLS, timeouts, log │        - only public listener
   └──────────┬──────────┘
              │ internal docker network
              ▼
   ┌─────────────────────────────────────────────┐
   │  Hermes gateway container                   │
   │   • agent loop, memory, 90+ skills          │
   │   • OpenAI-compatible API server :8642      │
   │   • cron scheduler                          │
   │   • policy guard (permission levels 0–3)    │
   │   • tools run HERE by default               │
   └───────┬─────────────────────────┬───────────┘
           │ HTTPS                   │ Tailscale (private mesh, no open ports)
           ▼                         ▼
   Anthropic API              Mohit's laptop
   Growstack LLM router        • sshd        → terminal.backend: ssh
   Instantly / Apollo          • Chrome CDP  → browser.cdp_url  (via auth proxy)
```

### Why this shape

**One container, not microservices.** Hermes is a single stateful runtime — agent loop, memory database, cron, and API server share process state. Splitting them would mean inventing coordination that the product does not have. One container with a persistent volume is both simpler and more correct.

**Caddy as the only public listener.** Automatic Let's Encrypt certificates, no manual renewal, and one place where TLS, timeouts and logging are configured. The Hermes port is never published to the host.

**The laptop bridge is optional and separate.** If the mesh is down, the server keeps serving everything that does not need the laptop. Local actions degrade; the service does not.

---

## 3. Technology choices and rationale

| Layer | Choice | Why this and not the alternative |
|---|---|---|
| Agent runtime | **Hermes Agent (Python 3.11)** | Not a decision we get to make — Hermes is Python, pinned `>=3.11,<3.14`. Do not attempt to port it. |
| Deployment | **Docker + Docker Compose** | Hermes ships a maintained `Dockerfile` with an s6-overlay supervision tree. Use it. A hand-rolled systemd install means owning Python, Node, ffmpeg, ripgrep and Chromium versions forever. |
| TLS / edge | **Caddy 2** | Automatic certificate issuance and renewal in ~15 lines. nginx needs certbot, a renewal timer, and a reload hook — three more things to break. |
| Private mesh | **Tailscale** | WireGuard, NAT traversal, no inbound ports opened anywhere. The laptop is behind NAT with a changing IP; a VPN appliance or port-forward is strictly worse. |
| CDP auth proxy | **Python 3.11, stdlib + `websockets`** | Runs inside the Hermes venv that already exists on the laptop. No new runtime, no npm tree. ~200 lines. |
| Your client code | **TypeScript / Node** | Your stack is Next.js. The API is OpenAI-compatible, so the official `openai` SDK works unchanged — point `baseURL` at Jarvis. No custom client to maintain. |
| Skills / automation | **Markdown + Python** | Hermes' native format. Skills are `SKILL.md` plus scripts; helper scripts use the Hermes venv interpreter so dependencies are already present. |
| Secrets | **Server-side `.env`, injected as env vars** | Never in the image, never in git, never in a browser bundle. |

### On languages specifically

You do not need to pick a language for the assistant — that is fixed by Hermes (Python). The choices you actually own:

1. **Client code: TypeScript.** Matches your Next.js stack and uses the stock `openai` package.
2. **Server-side helper scripts: Python 3.11.** Use `%HERMES%/venv/Scripts/python.exe` (or `/opt/venv/bin/python` in the container) so `yaml`, `requests`, `gradio_client` are already installed.
3. **Infrastructure: declarative files, not scripts.** `docker-compose.yml` and `Caddyfile` over bash. Reproducible, diffable, reviewable.

**Rule to keep:** if a task is a Hermes skill, write it as `SKILL.md` + a Python script. If it is application logic, write it in TypeScript in your own repo and call Jarvis over HTTP. Do not blur the two — skills that embed business logic become impossible to test.

---

## 4. Security model

Security is layered so that no single failure is fatal. Each layer assumes the one outside it has already been breached.

### 4.1 The core risk, stated plainly

`API_SERVER_KEY` grants the caller Hermes' full toolset, **including `terminal`**. It is not an "API key" in the ordinary sense — it is remote code execution on the server. Treat it as a root password.

**Consequences:**
- It lives only in server-side `.env` and your backend's secret store.
- It never appears in browser JavaScript, a mobile app, a repo, a log line, or a chat message.
- Browsers never call Jarvis directly. Your backend calls Jarvis; your frontend calls your backend.
- Rotate on any suspicion, and on staff change.

### 4.2 Layers

| # | Layer | Controls | Stops |
|---|---|---|---|
| 1 | Network | Only 80/443 public. Hermes port unpublished. | Direct access to the agent |
| 2 | Transport | TLS 1.2+, HSTS, automatic certs | Interception, downgrade |
| 3 | Authentication | Bearer `API_SERVER_KEY` on every route except `/health` | Anonymous callers |
| 4 | Authorisation | Policy guard: levels 0–3, single-use approval tickets | The agent taking consequential action alone |
| 5 | Blast radius | Container user is non-root; CPU/memory capped; only the data volume is writable | A compromised agent damaging the host |
| 6 | Laptop bridge | Tailscale mesh **plus** CDP auth proxy | Anyone reaching the workstation |
| 7 | Audit | Structured logs, `jarvis/policy.log`, spend ledger, redacted at source | Silent misuse |

### 4.3 Permission levels (already implemented)

| Level | Rule | Enforced by |
|---|---|---|
| 0 | Read, analyse, draft, test, open a PR — proceed | `SOUL.md` |
| 1 | Low-risk reversible writes — do, then report | `SOUL.md` + memory lists |
| 2 | Merge, publish, send, deploy, spend money — ask, then require a ticket | `policy-guard.py` + `jarvis approve <key>` |
| 3 | Money transfer, deletion, force-push, credential access — refuse | `policy-guard.py`, `approvals.deny`, Hermes' own blocklist |

Tickets are files created by a human on the machine. This is deliberate: text arriving in an email or a web page can *say* "the owner approves", but it cannot create a file. Two keys are required — the owner's words in conversation and their hand on the keyboard.

### 4.4 The browser bridge — where the real danger is

Chrome's DevTools Protocol has **no authentication mechanism at all**. Anyone who can reach port 9222 controls the browser: every logged-in session, every saved password, the ability to act as you on any site.

You cannot add authentication to CDP. You can only put an authenticating proxy in front of it, and that proxy must handle three things that naïve proxies get wrong:

1. **WebSocket upgrade.** The real traffic is a WS upgrade on `/devtools/page/<id>`. An HTTP-only proxy passes `/json/list` and then silently breaks every command.
2. **Host header validation.** Chrome rejects requests whose `Host` is not `localhost` or a bare IP — its DNS-rebinding defence. The proxy must rewrite `Host` to `127.0.0.1:9222`.
3. **URL rewriting.** `/json/version` returns a `webSocketDebuggerUrl` pointing at `ws://127.0.0.1:9222/...`. The client would connect there directly, bypassing the proxy. Those URLs must be rewritten in the response body.

**Mandatory controls:**

- Tailscale **and** the auth proxy. Not one or the other. Tailscale means the port is never on the public internet; the proxy means a foothold inside the network still needs a token.
- A **dedicated Chrome profile** (`--user-data-dir`), so the agent gets only the sessions you deliberately log into, not your entire personal browser.
- Never a router port-forward. Never `0.0.0.0` without the proxy.
- The bridge is off by default and turned on for a task, not left running.

### 4.5 Secret inventory

| Secret | Where it lives | Rotation |
|---|---|---|
| `ANTHROPIC_API_KEY` | server `.env` | on staff change / suspicion |
| `API_SERVER_KEY` | server `.env` + your backend secret store | quarterly, and on any leak |
| `GROWSTACK_ROUTER_KEY` | server `.env` | per company policy |
| `INSTANTLY_API_KEY`, `APOLLO_API_KEY` | server `.env` | per vendor policy |
| CDP proxy token | laptop `.env` + server `.env` | monthly |
| Tailscale auth key | used once at join, then discarded | ephemeral, pre-authorised |

**Note:** the keys used during development (fal, HuggingFace, Apollo, Instantly, Growstack router) were shared over chat and must all be rotated before this goes live.

---

## 5. Reliability — how it does not break

"Never breaks" is not achievable; "fails visibly, degrades gracefully, recovers by itself" is. Each known failure has a defined behaviour.

| Failure | Without design | With this design |
|---|---|---|
| Container crashes | Service down until someone notices | `restart: unless-stopped`; healthcheck; back in ~90s |
| Server reboots | Manual restart | Compose restart policy; no human needed |
| Model provider 5xx | Requests hang, then fail | `fallback_model` configured; automatic switch |
| Upstream slower than the edge timeout | Client sees a dead socket | Upstream timeout capped **below** the edge timeout, so we fail first and fail cleanly |
| Long agent run | Proxy cuts it off mid-run | Caddy read/write/idle set to 30m; `flush_interval -1` so SSE is never buffered |
| Disk fills | Silent corruption | Log rotation (20 MB × 5); `/health/detailed` reports disk |
| Runaway tool loop | Tokens burn all night | `tool_loop_guardrails.hard_stop_enabled: true` |
| Cron self-approves something | Unattended consequential action | `approvals.cron_mode: deny` — cron is capped at level 1 |
| Laptop offline | Everything fails | Only laptop-bound tasks fail; the service continues |
| Bad deploy | Broken and unrecoverable | Volume separate from image; roll back by redeploying the previous tag |

### Health and monitoring

- `GET /health` — public, cheap liveness. Use for the load balancer.
- `GET /health/detailed` — authenticated readiness: config, state DB, model, disk, platforms, active runs. **Degraded still returns HTTP 200** — alert on the `status` field, not the status code.
- Alert on: healthcheck failing twice, disk above 80%, fallback engaging more than N times an hour, any level-3 block in `policy.log`.

### Backup

The entire state is one Docker volume (`hermes-data`): config, memory, sessions, skills, plugins, cron, ledgers. Nightly `docker run --rm -v hermes-data:/data -v /backup:/backup alpine tar czf /backup/hermes-$(date +%F).tgz /data`, keep 14 days, and **test a restore quarterly** — an untested backup is a hope, not a backup.

---

## 6. Implementation plan

Each phase is independently useful and independently verifiable. Do not start a phase before its predecessor's verification passes.

### Phase 1 — Server foundation (½ day)

1. Provision Linux (Ubuntu 24.04 LTS), 4 vCPU, 8 GB RAM, 100 GB SSD.
2. Install Docker Engine + Compose plugin.
3. DNS `A` record for `jarvis.growstack.ai` → server IP. **Do this before starting the stack**, or certificate issuance fails.
4. Firewall: allow 22 (from admin IPs only), 80, 443. Deny everything else inbound.
5. `git clone` the Hermes source next to this repo.

**Verify:** `docker run hello-world`; DNS resolves from outside.

### Phase 2 — Hermes service (½ day)

1. `cp deploy/.env.example deploy/.env`, fill in — `ANTHROPIC_API_KEY`, `API_SERVER_KEY` (`openssl rand -hex 32`), `JARVIS_DOMAIN`, `ACME_EMAIL`.
2. `docker compose up -d --build`.
3. Copy the Jarvis assets into the volume: `SOUL.md`, `agent-hooks/policy-guard.py`, `skills/jarvis/*`, `plugins/image_gen/growstack-router`, then merge `config.additions.yaml`.
4. Restart so the persona, guard and skills load.

**Verify:** `curl https://jarvis.growstack.ai/health` → `{"status":"ok"}`; `/v1/models` with the bearer token lists `jarvis`; a request needing `terminal` executes and returns real output; a level-3 request is refused.

### Phase 3 — Client integration (½ day)

Your backend calls Jarvis with the stock `openai` package:

```ts
import OpenAI from "openai";

const jarvis = new OpenAI({
  baseURL: "https://jarvis.growstack.ai/v1",
  apiKey: process.env.JARVIS_API_KEY!,   // server-side only. Never NEXT_PUBLIC_.
});

const run = await jarvis.responses.create({
  model: "jarvis",
  input: "Summarise today's inbound leads and draft replies.",
  conversation: `user-${userId}`,   // Hermes keeps per-conversation memory
  store: true,
});
```

Use `conversation` to give each end user an isolated thread. For long tasks use the Runs API (`POST /v1/runs`, then subscribe to `/v1/runs/{id}/events`) so you can show progress and cancel.

**Verify:** end-to-end from your app; two different `conversation` values do not see each other's history.

### Phase 4 — Laptop bridge, only if needed (½ day)

1. Install Tailscale on the server and the laptop; join both to the tailnet.
2. Laptop: enable OpenSSH Server; set `TERMINAL_SSH_HOST` to the laptop's Tailscale IP.
3. Laptop: launch Chrome with `--remote-debugging-port=9222 --user-data-dir=%LOCALAPPDATA%\jarvis-chrome`.
4. Laptop: run the CDP auth proxy bound to the Tailscale interface.
5. Server: set `browser.cdp_url` to the proxy address.

**Verify:** from the server, a Jarvis request runs a command on the laptop; a browser request drives the real Chrome window; the CDP port is unreachable from the public internet (confirm with an external scan).

### Phase 5 — Operations (½ day)

1. Nightly volume backup + retention; restore rehearsal.
2. Uptime monitor on `/health`; alerting on `/health/detailed`.
3. Log shipping if you have a stack; otherwise rely on rotated JSON logs.
4. Runbook: restart, roll back, rotate a key, revoke a ticket, read the policy log.

---

## 7. Operating notes

### Cost

Token spend dominates. On Claude Sonnet 5 (~$3/$15 per 1M):

| Workload | Approx cost |
|---|---|
| One chat exchange | ₹2 |
| Morning brief | ₹10 |
| One coding task → PR | ₹53 |
| Month of daily automation | ₹500–800 |

Server: a 4 vCPU / 8 GB VPS is ₹1,500–3,000/month. Image generation via the Growstack router is billed to the company Azure account, not per-call here.

**Controls:** `--mode template` in the lead machine drafts without an LLM; keep `tool_loop_guardrails` on; use a cheaper model for bulk classification while leaving the interactive brain on Sonnet.

### Upgrades

Hermes moves quickly. Pin an image tag, upgrade deliberately: back up the volume, pull, `docker compose up -d --build`, verify health, and keep the previous tag for one-command rollback. Do not run `latest` in production.

### What stays on the laptop

Personal WhatsApp, the local console, and anything touching files that only exist there. The laptop instance and the server instance can share this repo as the source of truth for skills.

---

## 8. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | Do we need the laptop bridge at all? | Start without it. Add only when a real task requires it — it is the largest security surface in the design. |
| 2 | Which model on the server? | `claude-sonnet-5` via a real API key. The Growstack router is not yet usable for text: its SSE drops `tool_calls`, and non-streaming exceeds its own gateway timeout past ~60s of generation. Revisit when patched. |
| 3 | One instance or one per environment? | Two: `jarvis-dev` and `jarvis` (prod), separate volumes and keys. Never test against production memory. |
| 4 | Who holds `API_SERVER_KEY`? | Your backend's secret manager only. Two named people can read it. |
| 5 | WhatsApp on the server? | No. Pairing is interactive and the session is tied to one number. Keep it on the laptop; use Telegram if the server needs a chat surface. |

---

## Appendix A — Repository layout

```
hermes-project/
├── deploy/
│   ├── docker-compose.yml        gateway + Caddy, healthchecks, resource limits
│   ├── Caddyfile                 TLS, 30m timeouts, SSE-safe, token never logged
│   └── .env.example              every variable, documented
├── hermes/
│   ├── SOUL.md                   persona + permission levels
│   ├── config.additions.yaml     approvals, guard hook, guardrails, memory
│   ├── agent-hooks/policy-guard.py
│   ├── plugins/image_gen/growstack-router/
│   └── skills/jarvis/            9 skills
├── interface/
│   ├── router_shim.py            restores tool_calls in streamed router replies
│   └── router_shim task installer
└── docs/                         runbook + this specification
```

## Appendix B — Verification checklist

- [ ] `/health` returns ok over HTTPS from outside the network
- [ ] `/v1/models` requires the bearer token; without it returns 401
- [ ] A `terminal` request executes and returns real output
- [ ] A level-3 request is refused with an alternative offered
- [ ] A level-2 request blocks until a ticket exists
- [ ] Container restarts by itself after `docker kill`
- [ ] Model fallback engages when the primary is unreachable
- [ ] A 20-minute agent run is not cut off by the edge
- [ ] Backup restores into a clean volume and the service starts
- [ ] CDP port is not reachable from the public internet
- [ ] No secret appears in any log line
