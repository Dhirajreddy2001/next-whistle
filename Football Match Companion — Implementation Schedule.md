# Football Match Companion — Implementation Schedule

Oct 21, 2026 · @Dhiraj

Seven build days from 2026-10-21 to submission on 2026-10-27 at 23:59 PT, then a frozen deployment through judging to 2026-11-10. Everything is built and tested locally against floci-az first, but a thin walking skeleton goes to Azure on day 2 and is redeployed as features land, so cloud-only problems surface early. Companion documents: HLD v1.2 (Football\_Match\_Companion\_HLD.md) and the [Low-Level Design](https://claude.ai/artifact/AToPakJF7GLF7aMfVT3wiL).

## Approach

- **Local first, cloud early.** Every feature is developed and integration-tested on the local stack. The cloud gets a working skeleton on day 2 and a redeploy at the end of days 4, 5 and 6.
- **Each day ends with a gate.** A gate is a short list of checks that must pass before the next day's work starts. A failed gate triggers the slip rules at the end of this doc, not a late night that eats tomorrow.
- **Deterministic before generative.** Replay, stats, geometry and overlays are finished and tested before any agent depends on them, so model problems never block the factual product.
- **Spend tokens deliberately.** Day-to-day work runs with fake agents; real Foundry calls are batched into prompt sessions and evaluation runs, and every validated result is cached.

### Run modes

| Mode | Storage | Agents | Identity | Used for |
| --- | --- | --- | --- | --- |
| `local-fake` | floci-az (connection string) | `FakeAgentRunner`, scripted | none | Daily development, CI, integration tests |
| `local-foundry` | floci-az, or Azure storage for pre-warming | Real Foundry agents | Your `az login` via `DefaultAzureCredential` | Prompt work, evaluation, pre-generation |
| `cloud` | Azure Blob | Real Foundry agents | Managed identity | Deployed app, smoke and end-to-end tests |

Two settings select a mode: `AGENT_MODE=fake|foundry` and `STORAGE_MODE=connection_string|managed_identity`. No other code path differs between modes.

### Environments

| Environment | Where | Data | Purpose |
| --- | --- | --- | --- |
| Local | Docker Compose on your machine | Seeded by `make seed` | Development and integration tests |
| CI | GitHub Actions | Generated in the job | Unit, golden, property and integration tests (`local-fake`) |
| Prod | `rg-nextwhistle-prod` | Uploaded by deployment workflows | The one Azure environment; judges use it |

There is no separate staging environment: with one replica and synthetic data, a tagged redeploy plus the smoke test is the release gate.

## Local development stack

One `docker compose up` gives you storage, the backend, the frontend and a trace viewer; Foundry is the only remote dependency, and only in `local-foundry` mode. Azurite is a drop-in fallback if floci-az fails the ETag test on day 1.

### `docker-compose.yml`

```yaml
services:
  storage:
    image: floci/floci-az:<pinned-tag>      # pin after day-1 test; never :latest
    ports: ["4577:4577"]
    volumes: ["./.local/floci-data:/app/data"]
    profiles: ["default", "floci"]
  azurite:                                   # fallback storage
    image: mcr.microsoft.com/azure-storage/azurite:<pinned-tag>
    command: azurite-blob --blobHost 0.0.0.0 --loose
    ports: ["10000:10000"]
    profiles: ["azurite"]
  traces:
    image: jaegertracing/all-in-one:<pinned-tag>
    ports: ["16686:16686", "4317:4317"]
  backend:
    build: ./backend
    env_file: ["./env/local-fake.env"]       # or local-foundry.env
    ports: ["8000:8000"]
    depends_on: [storage, traces]
    volumes: ["~/.azure:/home/app/.azure:ro"]   # only used in local-foundry
  frontend:
    build: { context: ./frontend, target: dev }
    command: npm run dev -- --host 0.0.0.0
    environment: ["VITE_API_BASE=http://localhost:8000/api"]
    ports: ["5173:5173"]
```

For the security headers, also run `npx @azure/static-web-apps-cli start frontend/dist --api-location http://localhost:8000` once a day against a production build, because Vite does not apply `staticwebapp.config.json`.

### Environment profiles (`env/*.env`, committed without secrets)

| Variable | `local-fake` | `local-foundry` | `cloud` (set by Bicep) |
| --- | --- | --- | --- |
| `ENV` | `dev` | `dev` | `prod` |
| `STORAGE_MODE` | `connection_string` | `connection_string` | `managed_identity` |
| `STORAGE_CONNECTION_STRING` | from `floci az env --format sdk-vars` | same | — |
| `STORAGE_ACCOUNT_URL` | — | — | `https://<account>.blob.core.windows.net` |
| `AGENT_MODE` | `fake` | `foundry` | `foundry` |
| `FAKE_SCRIPT` | `tests/fakes/default.yaml` | — | — |
| `FOUNDRY_PROJECT_ENDPOINT`, `MODEL_TOOLS`, `MODEL_SMALL` | — | real values | real values |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://traces:4317` | `http://traces:4317` | — (Azure Monitor exporter) |
| `SESSION_SIGNING_KEY`, `IP_HASH_KEY` | dev values in `.env.local` (gitignored) | same | Container Apps secrets |
| `ALLOWED_ORIGINS` | `http://localhost:5173,http://localhost:4280` | same | SWA URL |
| `ALLOWANCE_BUDGET_UNITS` | `100000000` | small real value | from budget sheet |

### Makefile targets

| Target | What it does |
| --- | --- |
| `make up` / `make down` | Start or stop the stack (`PROFILE=azurite` switches storage) |
| `make data` | Run the generator and validator; writes `data/out/v<ver>/` |
| `make seed` | Create containers and upload datasets, rules, questions and `active.json` with `scripts/upload_dataset.py`, the same script CI uses against Azure |
| `make test` | Unit, golden, property and contract tests; no services |
| `make it` | Integration tests against the running stack in `local-fake` |
| `make etag-check` | Day-1 conditional-write test against the active storage |
| `make agent` | Run the local Agent Service protocol host for integration testing |
| `make deploy-agent` | Package one source ZIP and deploy a new Foundry Hosted Agent version containing the four logical roles |
| `make eval` | Evaluation set in `local-foundry`; writes `eval/results-<date>.csv` |
| `make pregen` / `make prewarm` | Narrative cards and cache pre-warming; `TARGET=local\|cloud` |
| `make e2e` | Playwright smoke; `BASE_URL=http://localhost:5173` or the SWA URL |
| `make deploy` | Tag and push, which triggers `deploy.yml` |

### Daily local loop

1. `make up && make seed` (seed is idempotent and skips unchanged versions).
2. Work in `local-fake`; `make test` on save, `make it` before every commit.
3. Switch to `local-foundry` only for a planned prompt session; check traces in Jaeger at `localhost:16686`.
4. `make e2e` before pushing a deploy tag.

## Day 1 — Wed Oct 21: foundations

Goal: every unknown that could change the design is answered today, and both matches exist as validated data. Nothing else on day 1 matters as much as the three checks in block A.

### Block A — unblockers (first 2–3 hours)

- [ ] Confirm hackathon registration on the Innovation Studio platform; if not registered, stop and decide whether to continue
- [ ] Confirm the Azure subscription; create `rg-nextwhistle-prod` with its expiry tag; set the USD 20 hard project budget alerts at USD 5/10/15/18/19.50
- [ ] Create the Foundry resource and project; deploy a candidate tool-capable model and a candidate small model; note names and versions
- [ ] Install floci CLI and Docker; `floci az start`; run `make etag-check` (stale `If-Match` must return 412; `If-None-Match: *` on an existing blob must return 412). Fail → switch compose profile to Azurite
- [ ] Agent spike (≤ 90 min): run the standalone Agent Service with Microsoft Agent Framework and the Foundry protocol host; Analyst calls one function tool and Storyteller rewrites its brief; confirm `store: false`

### Block B — repository and pipeline

- [ ] Create the GitHub repo (private until day 7), MIT license, folder layout from LLD Section 2
- [ ] Backend skeletons: FastAPI Core Service; separate Agent Service entry point without FastAPI; shared request/response contracts; contract compatibility test; local and Blob repository adapters
- [ ] Frontend skeleton: Vite + React + TS, Zustand, TanStack Query, i18next with `en` and the second language
- [ ] `docker-compose.yml`, `env/` profiles, Makefile from the local stack section; `make up` works
- [ ] CI: ruff, mypy, pytest, Vitest, frontend build, gitleaks, pip-audit, npm audit

### Block C — data

- [ ] Squads and fictional names for three clubs; real-name fixture list for the synthetic check
- [ ] Scenario YAML for match 1 (two goals, offside reversal with 0.6 m margin and 95 s delay, activity surge) and match 2 (borderline offside 0.08 m, tactical yellow, one goal)
- [ ] Generator steps 1–8 (LLD Section 4); deterministic with seed
- [ ] Dataset validator with all checks; golden expected score and final-state hash
- [ ] Law 11 and 12 paraphrases in `rules.yaml` with source links; `questions.yaml` with the 8 suggested questions
- [ ] `make data && make seed` loads both matches into floci-az

### Decisions to record today

Model deployments for `MODEL_TOOLS` and `MODEL_SMALL`; Agent Framework or hand-written workflow; second language and reviewer; floci-az or Azurite; registration status.

### Exit gate

| Check | Pass condition |
| --- | --- |
| Registration | Confirmed |
| Foundry | Both models answer from your laptop; the spike ran a function call end to end |
| Storage | ETag check passes on the chosen local storage |
| Data | `make data` passes the validator for both matches; data is in local storage |
| CI | Green on the skeleton |

If data slips: finish match 1 completely and generate match 2 on day 2 morning; never ship a half-validated dataset.

## Day 2 — Thu Oct 22: deterministic core and walking skeleton

Goal: the whole factual product works through the API locally, and a minimal version of it runs in Azure with managed identity by the end of the day.

### Block A — domain core

- [ ] Pydantic domain models; `DatasetStore` loading manifest and building indexes at startup
- [ ] `ReplayEngine`: visibility rules, goal reversal, `state_at`, checkpoints every 60 s, `evidence_cutoff`; Hypothesis test (1,000 random cutoffs per match, checkpoint equals full fold)
- [ ] `StatsEngine`: all 13 metrics for team and player, normalization, windows with `insufficient_data`
- [ ] `DetectorEngine`: goal, reversal, red card, activity surge with cooldown; precomputed triggers
- [ ] `GeometryEngine`: offside margin, uncertainty flag, involvement; foul context; golden tests for 0.60 m and 0.08 m
- [ ] Similarity scoring with versioned thresholds

### Block B — overlays and API

- [ ] `OverlayEngine` with the 9 cue types; publish `docs/overlay-cue-schema.json`; schema test on every generated cue
- [ ] Endpoints: `/matches`, `/matches/{id}/squads`, `/replay`, `/overlays`, `/players/{playerId}`, `/evidence/{id}`, `/decisions`
- [ ] Contract tests against OpenAPI; problem+json on every error path
- [ ] `make it` covers replay deltas, seek-before-goal, reversal visibility

### Block C — walking skeleton in Azure (evening, time-box 3 hours)

- [ ] Bicep: storage (shared key disabled), Log Analytics + App Insights with daily cap, Container Apps environment and app (min 0, max 1), system-assigned identity, role assignments, Static Web App
- [ ] GitHub OIDC federated credential; `deploy.yml` builds the image to GHCR, deploys Bicep, uploads datasets with `upload_dataset.py`, updates the app, deploys the frontend skeleton
- [ ] Frontend skeleton shows the match picker from the live API
- [ ] Smoke from your laptop: `/health/ready`, `/matches`, `/replay?cutoffMs=1425000`; measure cold start from zero

### Exit gate

| Check | Pass condition |
| --- | --- |
| Core tests | Unit, golden and property tests green; golden score and stats match for both matches |
| Geometry | Offside margins reproduce authored values within 0.01 m |
| Local API | `make it` green on all factual endpoints |
| Cloud skeleton | SWA page lists both matches from the Container App; managed identity reads blobs; CORS works; cold start measured |

If the cloud skeleton fails by the time box: keep the failing step's error, finish block B, and fix the cloud issue first thing on day 3 morning before agent work. Do not carry it past day 3.

## Day 3 — Fri Oct 23: agents, workflow and Ask the Ref backend

Goal: an explanation and a referee answer can be requested through the API and come back validated, from fake agents in tests and from real Foundry agents locally. This is the heaviest day; the Critic is explicitly not part of it.

### Block A — tools and briefs (fake mode)

- [ ] `BoundContext`, seven tool handlers, JSON schemas with `strict: true`, result envelope and truncation
- [ ] `ToolDispatcher`: allowlist by `(agent, intent)`, schema validation, call counter, evidence ledger, spans
- [ ] `EvidenceBrief` model and V15 brief validation
- [ ] `FakeAgentRunner` with YAML scripts: happy path, multi-call turn, number error, missing fact, out-of-scope question, timeout
- [ ] Dispatcher tests: Storyteller tool call rejected; Analyst cannot call geometry; model-supplied cutoff ignored; seventh call returns `tool_limit_reached`

### Block B — workflow, validator and caches

- [ ] `AgentWorkflow` graph (Agent Framework or hand-written): route, brief cache, Analyst/Referee, validate brief, Storyteller, validate answer, finalize; one routed retry; fallback
- [ ] Validator V1–V20 with unit tests per check
- [ ] Brief cache, explanation cache, referee cache; idempotency records; allowance ledger with reserve, settle and reconcile; ETag concurrency test with injected 412s
- [ ] Endpoints: `/explanations`, `/recaps`, `/referee/questions`; factual fallback with template cards
- [ ] Workflow tests in fake mode for every routing path

### Block C — real agents (planned prompt session, `local-foundry`)

- [ ] Write the four prompts (`prompt_version` 1.1.0) and level guidance; `make agents` creates them in Foundry
- [ ] Run 3 explanation moments and 3 referee questions; fix prompts until V-checks pass on first or routed attempt
- [ ] Confirm the borderline offside returns `not_settled`
- [ ] Record tokens per step; set `OUTPUT_WEIGHT`, reservation sizes and per-agent limits from measurements
- [ ] Decide whether `MODEL_SMALL` holds quality for the Storyteller (V5 and V12 failure rate under 20%)

### Exit gate

| Check | Pass condition |
| --- | --- |
| Fake-mode workflow | All routing paths tested; `make it` green including explanations and referee questions |
| Real agents | 3 of 3 explanations and 3 of 3 referee answers validate locally; one trace shows Analyst → brief → Storyteller |
| Safety | Dispatcher denials logged; free-text injection attempt refused |
| Cost | Measured tokens per uncached request recorded; allowance numbers set |

If real agents are not validating by evening: keep fake-mode work merged, simplify prompts, and move block C's remainder to day 4 morning; frontend work can proceed against fake mode in parallel.

## Day 4 — Sat Oct 24: frontend

Goal: a viewer can watch a match, change settings, read narrative cards, request explanations and ask the Ref, end to end on the local stack, and the build is redeployed to Azure by evening.

### Block A — match experience

- [ ] Routes `/` and `/match/:matchId` with query-mirrored settings; match picker; `SettingsSheet` (level, language, club, focus player)
- [ ] Store and playback loop: rAF clock, 1×/4×, delta prefetch at 2 s from `heldUntilMs`, `contextEpoch` on seek/restart/switch
- [ ] `Pitch` (SVG 105 × 68, thirds, snapshot players, focus ring), `ScoreBug`, `Timeline` with spoiler-safe markers, keyboard controls
- [ ] `OverlayLayer`: priority rules, two stacked cards max, reduced-motion support

### Block B — panels

- [ ] `MomentsPanel` with narrative cards (template cards until day 6 pre-generation) and Explain/Compare/Ask the Ref buttons
- [ ] `StatsPanel` with level-filtered metrics and window comparison; `PlayerPanel`
- [ ] `ExplainPanel` with evidence chips that highlight and seek backwards; historical cards labeled with their clock
- [ ] `RefPanel`, `RefAnswer` (three parts plus finding badge), `DecisionOverlay` (offside line, margin label, uncertainty band, foul context)
- [ ] `StatusBanner`: cold-start wake-up message, facts-only mode, generation unavailable

### Block C — quality and redeploy

- [ ] i18n strings complete in both languages; WCAG AA contrast check on club colors
- [ ] Vitest: epoch invalidation, stale response ignored, markers hidden before availability, settings invalidate slots
- [ ] Playwright smoke locally (`make e2e`): play 30 s at 4×, open a card, explain, switch language, set focus player, ask a suggested referee question
- [ ] SWA CLI run against the production build to verify headers and CSP
- [ ] Tag and deploy; run `make e2e BASE_URL=<swa-url>` against the cloud, with real agents answering one moment

### Exit gate

| Check | Pass condition |
| --- | --- |
| Local end to end | Playwright smoke passes on the local stack |
| Ask the Ref | Offside geometry draws; a suggested question returns a three-part answer |
| Cloud | Same smoke passes against the SWA URL; CSP blocks nothing needed |
| Mobile | Match view usable at 390 px width |

If behind: drop the Compare button and the `PlayerPanel` refresh polish first; keep the RefPanel, since it is a demo centerpiece.

## Day 5 — Sun Oct 25: hardening and full cloud deploy

Goal: the deployed app is safe to hand to strangers: limits hold, costs are bounded, failures degrade to facts, and you can see everything in telemetry.

### Block A — access and limits

- [ ] `POST /sessions` with HS256 JWT; bearer check on generation routes; silent refresh on 401 in the frontend
- [ ] Rate limits per session, per IP hash and global concurrency (semaphore 2, queue 4); `/usage` endpoint; `GENERATION_ENABLED` and `REF_FREE_TEXT_ENABLED` kill switches
- [ ] Free-text pre-model checks: length, control characters, injection patterns
- [ ] CORS locked to SWA URL; HSTS; input limits; `gitleaks` clean
- [ ] Container Apps secrets for signing and IP-hash keys; budget units set from day-3 measurements

### Block B — telemetry

- [ ] Azure Monitor OpenTelemetry exporter; workflow, agent, tool and validator spans; Foundry tracing enabled on the project
- [ ] Metrics from LLD Section 17; workbook with cache hit rate, tokens versus budget, validator failures, cold versus warm latency
- [ ] Alerts: 5xx rate, allowance below 15%, startup failure, slow warm latency

### Block C — full deploy and cloud verification

- [ ] Tag `v0.5.0`; full deploy
- [ ] Cloud tests: `make e2e` against SWA; forced Foundry failure (wrong agent version in a test revision) shows facts-only mode; allowance exhaustion test with a tiny budget in a test revision; idempotent double-click; out-of-scope referee question
- [ ] Measure: cold start, warm uncached latency per step, tokens per request, idle replica cost per day
- [ ] Decide judging-window `minReplicas` from the idle cost (1 if ≤ USD 1.5 per day)
- [ ] Draft the demo script (`docs/demo-script.md`): 6 beats in 115 seconds

### Exit gate

| Check | Pass condition |
| --- | --- |
| Limits | Unauthenticated generation 401; limits return 429 with `Retry-After`; kill switch serves cache only |
| Degradation | Foundry failure and exhausted allowance both leave replay, overlays and cached answers working |
| Telemetry | One request traced end to end across API, workflow, agents and tools in App Insights |
| Cost | Cumulative spend under USD 20; per-request cost known |

If telemetry slips, keep alerts on allowance and 5xx only and finish the workbook on day 6 while evaluation runs.

## Day 6 — Mon Oct 26: evaluation, pre-generation and demo video

Goal: prompts are frozen, the cloud cache holds every answer judges are likely to request, and the video is recorded. Code changes today are prompt and bug fixes only.

### Block A — evaluation (`local-foundry` against cloud storage)

- [ ] Run `make eval`: 5 moments × 3 levels × 2 languages, plus perspective and focus-player runs (34), plus 12 referee cases
- [ ] Foundry evaluations (groundedness) on the same runs; export scores
- [ ] Human review sheet: factual accuracy, level fit, Law explained correctly; second-language reviewer scores their half
- [ ] Fix prompts once; rerun only failing cases; bump `prompt_version` to the frozen value
- [ ] Optional: enable the Critic if it improves scores without breaking latency; otherwise leave `CRITIC_ENABLED=false`

### Block B — pre-generation and pre-warm (`TARGET=cloud`)

- [ ] `make pregen`: Analyst briefs for all triggers first, then neutral narrative cards for all levels and languages, then club-perspective cards for demo triggers; stop at `PREGEN_MAX_USD`
- [ ] `make prewarm`: demo explanations for the three demo profiles; suggested referee answers for each decision across levels and languages; full-time recaps
- [ ] Verify from a fresh browser on the cloud URL that demo moments return `source: cache` in under 1 s

### Block C — demo video and evidence

- [ ] Capture the workflow trace showing Analyst → brief → Storyteller and one routed retry (use a fake-injected number error in a test revision if a natural one is not available)
- [ ] Record the video following the demo script; keep it under 1:55; no third-party music or marks

| Beat | Seconds | Shows |
| --- | --- | --- |
| Problem and app | 0–15 | Match view with overlays, a casual fan and an analyst side by side |
| Personalization | 15–35 | Switch level, language, club and follow a player |
| Explain a moment | 35–55 | Narrative card, Explain, evidence chips highlighting the pitch |
| Time correctness | 55–70 | Seek before the goal; state and AI context change |
| Ask the Ref | 70–95 | Offside line with margin; reversed goal explained; borderline call reported as not settled |
| Under the hood | 95–115 | Workflow trace, agents and tools, Foundry evaluation scores, Azure architecture |

- [ ] Upload the video (public on YouTube or Vimeo)
- [ ] Choose the category: Best Multi-Agent System if the trace beat is convincing; otherwise Best Use of Microsoft Foundry

### Exit gate

| Check | Pass condition |
| --- | --- |
| Quality | Validator pass after routing ≥ 95%; referee V16/V18 100%; human factual mean ≥ 1.8 |
| Cache | All demo moments and suggested questions served from cache in the cloud |
| Video | Uploaded, under 2 minutes, every beat shows the deployed app |
| Budget | Cumulative spend recorded; remaining allowance covers the judging window |

## Day 7 — Tue Oct 27: submission and freeze

Goal: submit by early evening Pacific time, leaving hours of buffer before 23:59 PT. Convert the deadline to your local time zone today and set reminders.

### Block A — repository

- [ ] README: elevator pitch, problem, what it does, architecture diagram, Microsoft and Azure technologies used (Foundry agents, Agent Framework, Container Apps, Static Web Apps, Blob Storage, Application Insights, managed identity, GitHub Actions), multi-agent design, Ask the Ref, evaluation table, data statement (all synthetic, fictional clubs), testing instructions with the deployed URL and no-login note, local run instructions
- [ ] Copy HLD and LLD into `docs/`; publish the overlay cue schema
- [ ] Final secret scan; remove `.env.local` files; make the repository public

### Block B — submission

- [ ] Final deploy only if a fix is needed; tag `v1.0.0`; full smoke and Playwright against the cloud
- [ ] Fresh-browser check on desktop and phone: no login, demo moments instant, Ask the Ref works
- [ ] Fill the Projects page on the hackathon site: pitch, technologies, video URL, repo URL, live URL, testing instructions, category
- [ ] Screenshot the confirmation

### Block C — freeze

- [ ] Set `minReplicas` per the day-5 decision
- [ ] Disable automatic deploys on push; protect `main`
- [ ] Record the freeze revision name, image tag and `active.json` value in the README

### Exit gate

Submission confirmed before 18:00 PT; the live URL, repo and video all open from a logged-out browser.

## Judging window — Oct 28 to Nov 10

The app must keep working, free and without restriction, until the judging period ends on 2026-11-10 at 23:59 PT. Changes are limited to keeping it running exactly as shown in the video.

### Daily check (10 minutes)

- [ ] Open the live URL in a private window; play a match, open one cached explanation and one referee answer
- [ ] Workbook: 5xx count, cache hit rate, tokens versus budget, remaining allowance
- [ ] Cost Management: cumulative lifetime project spend against the USD 20 hard ceiling, including both October and November

### Rules for changes

- Allowed: raising or lowering limits, the kill switches, `minReplicas`, re-uploading the same dataset version, restarting a revision.
- Allowed with care: a bug fix that keeps behavior identical to the video, deployed as a new tagged revision with the full smoke test.
- Not allowed: new features, prompt changes that alter demo answers, dataset version changes.

### If spend approaches the limit

At 80% of the model allowance or USD 15 total project spend, whichever comes first, set `REF_FREE_TEXT_ENABLED=false`. At 95% of the model allowance or USD 18 total project spend, set `GENERATION_ENABLED=false` and minimize compute; cached answers, narrative cards and factual replay stay available. At USD 19.50, remove paid model deployments and nonessential billable resources so the project cannot cross the USD 20 hard ceiling.

### After judging ends

- [ ] At 2026-11-11 08:00 UTC, disable generation and export the traces, evaluation results and cost evidence to retain
- [ ] At 08:15 UTC, dispatch the guarded expiry workflow; delete `rg-nextwhistle-prod`, including the Core and Foundry deployments
- [ ] Confirm no `Project=NextWhistle` Azure resource remains and verify that billing stops, following `docs/cloud-plan.md`

## Risks, slip rules and cut order

### Top risks

| Risk | Early signal | Mitigation |
| --- | --- | --- |
| Not registered | Day 1 block A | Confirm first; everything else waits on it |
| Foundry model or quota unavailable in region | Day 1 spike | Pick another region for Foundry only; storage and app can stay put |
| Agent Framework integration friction | Spike exceeds 90 min | Hand-written workflow behind the same interface |
| floci-az ETag gaps | `make etag-check` fails | Azurite profile |
| Managed identity, RBAC or CORS issues | Day 2 skeleton | Found 4 days early by design; fix before day-3 agent work |
| Prompt quality on the small model | Day 3 V5/V12 failure rate | Use `MODEL_TOOLS` for the Storyteller |
| Latency of sequential agents | Day 5 measurement | Pre-warm cache; brief cache; Critic stays off |
| Cost overrun from pre-generation | Day 6 `PREGEN_MAX_USD` | Generate demo triggers first; templates for the rest |
| Second-language review unavailable | Day 1 | Ship English plus the second language only for demo moments, clearly labeled |

### Slip rules

1. A failed exit gate is fixed the next morning, time-boxed to 3 hours, before new work starts.
2. If still failing, apply the next cut from the list below and move on.
3. Never cut tests on the deterministic core, the validator or the limits; they protect the demo and the budget.
4. Day 6 evening and day 7 are not available for features under any slip.

### Cut order

1. Synthetic shot speed
2. Critic agent
3. Penalty decision type
4. Free-text referee questions (suggested questions stay)
5. Club-perspective narrative card variants
6. Compare intent and `find_similar_events`
7. Second match (one excellent match beats two rough ones)

What never gets cut: the Analyst–Storyteller split with a visible trace, the offside Ask the Ref flow with its overlay, time-correct replay, personalization by level and language, anonymous access, and the video.
