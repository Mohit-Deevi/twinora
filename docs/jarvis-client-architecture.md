# turgo-jarvis — Client Integration Architecture

Companion to *Jarvis on Hermes — Server Implementation Specification*.
That document specifies the agent service. This one specifies the repository our products use to call it.

---

## 1. The problem this repository solves

Jarvis exposes an OpenAI-compatible API. It is tempting to call it directly from the Turgo backend with the `openai` SDK and be done in an afternoon. That would be a mistake, for four reasons that only surface in production:

**Agent runs are not request/response.** A chat completion returns in two seconds. "Find 100 leads, draft outreach, build the campaign" runs for minutes. Any synchronous HTTP handler that waits for it will hit a proxy timeout, hold a connection through a deploy, and lose the work on a pod restart.

**Agent actions have side effects.** A retried chat completion costs a few paise. A retried *outreach run* sends the same email twice. Retry semantics that are correct for an LLM call are actively dangerous for an agent.

**The credential is command execution.** Section 3.1 of the server specification classifies it as remote code execution. It cannot live anywhere a browser, a mobile client, or a low-trust service can reach. Exactly one component in our estate should hold it.

**The upstream will change.** Hermes releases frequently; endpoints and event shapes move. If forty call sites across Turgo import the Hermes client directly, every upstream change is a forty-file migration.

This repository is the answer to all four: **one owned boundary between Turgo and the agent.**

---

## 2. Architectural position

```
  Turgo web / mobile
        |  session auth (their user identity)
        v
  Turgo backend  ────────────────────────┐
        |  internal service call          │  never talks to Jarvis directly
        v                                 │
  ┌───────────────────────────────────┐   │
  │  turgo-jarvis  (this repository)  │◄──┘
  │                                   │
  │   api/      what Turgo may ask    │
  │   domain/   what a task means     │
  │   infra/    how Hermes is reached │  ← the ONLY holder of the credential
  └────────────────┬──────────────────┘
                   │  HTTPS + Bearer
                   v
          Jarvis (Hermes agent service)
```

**The rule this diagram encodes:** the credential exists in exactly one deployment, and `infra/jarvis` is the only directory in our estate that knows Hermes exists. Everything else speaks Turgo's language — campaigns, leads, briefs — not the agent's.

This is an anti-corruption layer in the Evans sense. It is not ceremony. It is what allows a Hermes upgrade to be a one-file change.

---

## 3. Repository structure

```
turgo-jarvis/
├── README.md                          what this is, how to run it, how to add a task
├── .gitlab-ci.yml                     lint → typecheck → test → build → scan → deploy
├── .env.example                       every variable, documented, no real values
├── package.json
├── tsconfig.json                      strict: true. Non-negotiable.
├── Dockerfile                         multi-stage; distroless runtime
│
├── src/
│   ├── index.ts                       composition root — the ONLY place that wires dependencies
│   │
│   ├── config/
│   │   ├── env.ts                     zod-validated environment. Fails at boot, never at 3am.
│   │   └── capabilities.ts            which callers may invoke which tasks
│   │
│   ├── api/                           ── transport. Knows HTTP. Knows nothing about Hermes.
│   │   ├── server.ts                  Fastify instance, plugin registration
│   │   ├── middleware/
│   │   │   ├── auth.ts                caller identity (service token or mTLS)
│   │   │   ├── correlation.ts         request id in, trace id through, both in every log
│   │   │   ├── rate-limit.ts          per-caller, per-task-type
│   │   │   └── error-handler.ts       domain error → HTTP status. One place.
│   │   ├── routes/
│   │   │   ├── tasks.ts               POST /tasks, GET /tasks/:id, DELETE /tasks/:id
│   │   │   ├── tasks.stream.ts        GET /tasks/:id/events  (SSE passthrough)
│   │   │   ├── conversations.ts       GET/DELETE conversation history
│   │   │   └── health.ts              /health (liveness), /health/ready (readiness)
│   │   └── schemas/                   zod request/response contracts, exported for clients
│   │       ├── task.schema.ts
│   │       └── conversation.schema.ts
│   │
│   ├── domain/                        ── business meaning. Pure. No I/O, no SDK imports.
│   │   ├── task/
│   │   │   ├── task.entity.ts         Task, TaskStatus, state transitions
│   │   │   ├── task.types.ts          the closed set of things Turgo may ask for
│   │   │   └── task.policy.ts         may this caller run this task, at this cost?
│   │   ├── conversation/
│   │   │   └── conversation.id.ts     Turgo user id → stable, namespaced agent thread id
│   │   └── errors.ts                  the error taxonomy (section 7)
│   │
│   ├── application/                   ── use cases. Orchestrates domain + infra. Still testable.
│   │   ├── submit-task.usecase.ts     validate → dedupe → persist → enqueue
│   │   ├── execute-task.usecase.ts    the worker body: call Jarvis, stream, persist, notify
│   │   ├── cancel-task.usecase.ts
│   │   └── get-task.usecase.ts
│   │
│   ├── infra/                         ── the outside world. Swappable. Mocked in tests.
│   │   ├── jarvis/
│   │   │   ├── jarvis.client.ts       the ONE place that imports the OpenAI SDK
│   │   │   ├── jarvis.mapper.ts       agent events → domain events
│   │   │   ├── jarvis.errors.ts       upstream failure → domain error
│   │   │   └── jarvis.types.ts        upstream shapes, isolated so they cannot leak
│   │   ├── queue/
│   │   │   ├── queue.port.ts          interface the application depends on
│   │   │   └── bullmq.adapter.ts      Redis-backed implementation
│   │   ├── store/
│   │   │   ├── task.repository.port.ts
│   │   │   ├── postgres.adapter.ts
│   │   │   └── migrations/
│   │   ├── notify/
│   │   │   └── webhook.adapter.ts     signed callbacks to Turgo on completion
│   │   └── telemetry/
│   │       ├── logger.ts              pino, structured, redaction list applied at source
│   │       └── tracing.ts             OpenTelemetry spans across api → queue → jarvis
│   │
│   └── worker/
│       └── main.ts                    second entrypoint: consumes the queue
│
├── test/
│   ├── unit/                          domain + application. No network, no database.
│   ├── contract/
│   │   ├── jarvis.contract.test.ts    runs against a real Jarvis in CI; catches upstream drift
│   │   └── fixtures/                  recorded agent responses for offline runs
│   └── integration/                   api + postgres + redis via testcontainers
│
├── ops/
│   ├── docker-compose.dev.yml         postgres + redis + a fake Jarvis, for local work
│   ├── fake-jarvis/                   deterministic stub. Tests must not need a live agent.
│   └── k8s/                           manifests, if we deploy to the cluster
│
└── docs/
    ├── adr/                           architecture decision records
    │   ├── 0001-async-task-model.md
    │   ├── 0002-anti-corruption-layer.md
    │   └── 0003-idempotency-strategy.md
    ├── adding-a-task-type.md          the runbook a new engineer follows
    └── error-catalogue.md
```

### Why this shape, and what it is not

The three-layer split — `api` / `domain` + `application` / `infra` — exists to make one property true: **the business rules can be tested without a network.** `domain` imports nothing from `infra`. `application` depends on *ports* (interfaces), never adapters. That inversion is what lets the contract test suite run against a stub in ninety seconds and against the real agent in CI.

**This is not microservices.** It is one deployable with two entrypoints (`src/index.ts` for HTTP, `src/worker/main.ts` for the queue) sharing a codebase. Splitting them into separate repositories would buy nothing and cost a shared-type problem.

**Rejected alternative — call Hermes directly from the Turgo backend.** Faster to build, and it fails on all four counts in section 1. Notably it puts a command-execution credential inside a service that also serves browser traffic.

**Rejected alternative — a thin proxy with no domain layer.** Appealing until the first time a caller needs to run a task the agent should not expose to them. Without `task.policy.ts` there is nowhere for that rule to live except scattered `if` statements in route handlers.

---

## 4. The task model

Everything Turgo asks for is a **Task**: a named, versioned, policy-checked unit of agent work.

```ts
// domain/task/task.types.ts — the closed set. Adding a member is a deliberate act.
export const TASK_TYPES = {
  LEAD_SOURCE:      "lead.source",       // ICP → leads → drafts
  CAMPAIGN_BUILD:   "campaign.build",    // drafts → Instantly campaign (paused)
  CONTENT_CREATE:   "content.create",    // angle → copy + image
  BRIEF_DAILY:      "brief.daily",       // mail + calendar + repos → digest
  CODE_TO_PR:       "code.to_pr",        // issue → branch → tests → PR
  RESEARCH:         "research",          // question → cited answer
} as const;
```

A closed union rather than free-text prompts is the single most important decision in this repository. It gives us:

- **A policy surface.** `task.policy.ts` can say *this caller may run `RESEARCH` but not `CAMPAIGN_BUILD`*.
- **A cost model.** Each type carries an expected token budget; the policy rejects a request that would exceed a caller's quota before spending anything.
- **A test surface.** Six types have six contract tests. "Any prompt" has none.
- **A migration surface.** When a task's prompt changes, the type is versioned; in-flight tasks keep the old behaviour.

Free-text passthrough exists as exactly one type (`RESEARCH`), deliberately the least privileged.

### Lifecycle

```
  submitted ──> queued ──> running ──> succeeded
                              │
                              ├──────> failed      (terminal, with error taxonomy)
                              ├──────> cancelled   (client asked, agent stopped)
                              └──────> needs_approval ──> queued   (level-2 gate cleared)
```

`needs_approval` is not an error. When the agent hits a level-2 action, the run parks and Turgo surfaces an approval to a human. This is the client-side half of the server's permission model, and omitting it would mean the agent either blocks forever or acts without consent.

---

## 5. Non-obvious decisions

### 5.1 Asynchronous by default (ADR-0001)

`POST /tasks` returns `202 Accepted` with a task id. It never waits for the agent.

Long agent runs through a synchronous handler fail three ways: proxy timeouts at the edge, connections held open across a deploy, and work lost on restart. Making the queue the source of truth fixes all three, and gives cancellation and retry for free.

Clients that want live progress subscribe to `GET /tasks/:id/events`, which proxies the agent's SSE. **The stream is a view; the task record is the truth.** A client that disconnects loses the stream, not the work.

### 5.2 Idempotency is mandatory, not optional (ADR-0003)

Every `POST /tasks` requires an `Idempotency-Key` header. The key plus the caller identity is unique in the database.

Justification: agent tasks send email, create campaigns, and open pull requests. A network blip that causes the caller's HTTP library to retry must not double-send. This is the difference between a retry being safe and a retry being an incident.

Replaying a key returns the original task, including its current status. It does not start a second run.

### 5.3 Conversation identity is derived, never supplied

```ts
// domain/conversation/conversation.id.ts
export const conversationId = (tenantId: string, userId: string) =>
  `turgo:${tenantId}:${userId}`;
```

Callers pass their own user identifier; this repository derives the agent thread id. If callers supplied it directly, one caller could read another tenant's agent memory by guessing a string. Derivation makes cross-tenant access structurally impossible rather than merely forbidden.

### 5.4 Retry policy is per failure class, not global

A blanket "retry three times" is wrong here: it is correct for a 503 and catastrophic for a task that already sent email.

| Failure | Retry? | Rationale |
|---|---|---|
| Upstream 5xx, connection reset, timeout before any tool ran | Yes, exponential backoff | No side effects yet |
| Timeout *after* a tool executed | **No** — mark `failed_indeterminate` | We cannot know what already happened. A human decides. |
| 4xx from the agent | No | Our request is wrong; retrying repeats the mistake |
| Model provider exhausted | No, surface immediately | Retrying burns quota and delays the alert |

`failed_indeterminate` is an uncomfortable state that most systems omit, and its absence is why they double-send.

### 5.5 The credential never leaves the process

`JARVIS_API_KEY` is read once at boot in `config/env.ts` and passed to exactly one constructor. It is never logged (the pino redaction list covers `authorization`, `apiKey`, `Idempotency-Key`), never returned in an error, never exposed on a debug route. `env.ts` fails fast at boot if it is absent or malformed — a missing credential is a startup failure, not a 3am 500.

---

## 6. Observability

One correlation id enters at the edge and appears in every log line, every span, and the agent request itself:

```
Turgo request-id  →  turgo-jarvis correlation-id  →  Jarvis conversation + run id
```

When someone asks "why did this customer get two emails", that chain is the answer. Without it, the investigation is archaeology.

- **Logs:** pino, JSON, redaction applied at source rather than at the sink.
- **Traces:** OpenTelemetry spans across `api → queue → worker → jarvis`. The queue hop is where naive tracing loses the thread; propagate context into the job payload.
- **Metrics:** task count by type and terminal status, queue depth, agent latency percentiles, token spend by task type and caller.

Token spend by caller is the metric that prevents an unpleasant month. Budget alerting belongs here, not in a spreadsheet.

---

## 7. Error taxonomy

A closed set, mapped to HTTP in exactly one place (`api/middleware/error-handler.ts`):

| Domain error | HTTP | Retryable by caller | Meaning |
|---|---|---|---|
| `ValidationError` | 400 | No | Malformed request |
| `CapabilityDenied` | 403 | No | Caller may not run this task type |
| `QuotaExceeded` | 429 | Later | Caller over budget |
| `TaskNotFound` | 404 | No | Unknown id, or another tenant's |
| `AgentUnavailable` | 503 | Yes | Jarvis unreachable; already retried internally |
| `AgentRejected` | 422 | No | The agent refused (level-3 policy) — surface the reason verbatim |
| `TaskIndeterminate` | 500 | **No** | Side effects unknown; requires human review |

`AgentRejected` deserves emphasis: when the policy guard refuses an action, that refusal is a *product event*, not a bug. Turgo should show the user what was refused and the alternative offered.

---

## 8. Delivery pipeline

```yaml
# .gitlab-ci.yml — stages
lint        eslint + prettier --check
typecheck   tsc --noEmit                    # strict mode; no implicit any
test:unit   vitest run --coverage           # domain + application, no network
test:contract  vitest run test/contract     # against ops/fake-jarvis
build       docker build --target runtime
scan        trivy image + npm audit --audit-level=high
test:e2e    against a staging Jarvis        # manual trigger on main
deploy:stg  automatic on main
deploy:prd  manual, tagged releases only
```

**Contract tests are the guard against upstream drift.** They run twice: against the deterministic stub on every commit, and against a real staging Jarvis nightly. When Hermes changes a response shape, the nightly run fails and we learn it before production does.

**Tagged releases only for production.** The server specification pins the agent image; the client should be no looser.

---

## 9. Adding a task type — the whole procedure

The measure of this architecture is whether a new engineer can extend it without a briefing.

1. Add the member to `TASK_TYPES` and its input schema in `api/schemas/task.schema.ts`.
2. Declare its cost band and required capability in `config/capabilities.ts`.
3. Add the prompt template and expected tool surface in `infra/jarvis/`.
4. Write a contract test with a recorded fixture in `test/contract/fixtures/`.
5. Document it in `docs/adding-a-task-type.md`.

Five files, all obvious from the structure, none of them a route handler. If a change requires editing `api/routes/`, the abstraction has leaked and that is a review comment.

---

## 10. What this repository deliberately does not do

- **No prompt engineering in application code.** Prompts belong to Hermes skills, which are versioned, tested and reusable by the workstation instance. Duplicating them here creates two sources of truth that will diverge.
- **No business logic in skills.** The reciprocal rule. Skills are capabilities; Turgo's rules about who may do what live here.
- **No direct database access to Hermes state.** The agent's memory and session store are its own. We read them through the API or not at all.
- **No browser-facing endpoints.** Every caller is a trusted backend service. The moment a browser needs this, the answer is a Turgo backend endpoint that calls us, not CORS.
