# Next Whistle Build Specification

**Status:** Development specification  
**Version:** 1.0  
**Date:** October 22, 2026

## 1. Goal

Build the smallest contest-ready Next Whistle system that demonstrates time-correct football replay, deterministic analysis, evidence-grounded multi-agent explanations, and Ask the Ref. Development is local and fake-agent-first. Live Foundry calls are reserved for protocol verification, evaluation, pre-generation, and the final demo.

The observable completion condition is a no-login deployed site where a judge can replay a synthetic match, inspect evidence, request a cached or generated explanation, and ask a supported referee question without exposing future events or exceeding the USD 20 project ceiling.

## 2. Source-of-truth order

Use these documents in this order:

1. `docs/architecture.md` for service boundaries, ownership, and request flow.
2. This document for repository layout, work order, commands, and acceptance gates.
3. The LLD for domain schemas, endpoint payloads, tool definitions, and validation rules.
4. The HLD for product intent and scope.
5. `docs/cloud-plan.md` for Azure provisioning, cost control, and cleanup.

Record a decision in the architecture spec before implementing a change that crosses service or deployment boundaries.

## 3. Target repository layout

The current `backend/app` is an initial scaffold. The first work package migrates it without changing behavior.

```text
backend/
  services/
    core_application_service/
      pyproject.toml
      Dockerfile
      src/next_whistle_core/
        main.py
        config.py
        api/
        services/
        store/
        telemetry.py
      tests/
    agent_service/
      pyproject.toml
      main.py
      requirements.txt
      src/next_whistle_agent/
        workflow.py
        config.py
        agents/
        tools/
        prompts/
        validation/
      tests/
  shared/
    pyproject.toml
    src/next_whistle_shared/
      contracts/
      domain/
      evidence/
    tests/
  tests/contract/
frontend/
  package.json
  staticwebapp.config.json
  openapi/core-v1.json       # committed public Core contract snapshot
  src/
    app/                 # router, providers, shell, error boundary
    api/                 # client, generated types, problem details
    routes/              # match picker and match experience
    features/
      catalog/
      replay/
      narratives/
      explanations/
      referee/
      settings/
    components/          # shared UI primitives
    i18n/
    styles/
    test/
data/
eval/
infra/
scripts/
```

Core supports Python 3.12+. Agent development and deployment use Python 3.13. Frontend uses Node 22. The agent packaging script copies `next_whistle_shared` into a staging directory so `main.py`, `requirements.txt`, and importable packages are at the ZIP root.

## 4. Dependency rules

- Core and Agent may depend on Shared.
- Core and Agent must not import each other.
- Shared contains no network, filesystem-cloud, web-framework, or agent-framework code.
- API models serialize camelCase; Python and stored JSON use snake_case.
- Azure access stays behind Core store/agent-client adapters and the Agent evidence repository adapter.
- Tests use local repositories and a fake Agent Service client by default.
- No ordinary unit or CI test makes a paid model call.
- Add a dependency only for a demonstrated requirement and pin it in the relevant service lockfile.
- Frontend `app` may compose routes and features; features may import `api` and shared `components`; shared components and API code must not import feature modules.
- Frontend domain behavior belongs in pure selectors/hooks, not rendering components. Generated API types are never hand-edited.

A static import-boundary test must enforce these rules.

## 5. Required development commands

The first work package must update the Makefile to provide:

| Command | Required behavior |
| --- | --- |
| `make setup` | Install Core, Agent, Shared, and frontend dependencies |
| `make core` | Start FastAPI Core locally on port 8000 |
| `make agent` | Start the local Foundry protocol host on port 8088 |
| `make frontend` | Start Vite on port 5173 |
| `make test` | Run Shared, Core, Agent, and contract tests without Azure/model calls |
| `make test-core` | Run Core tests |
| `make test-agent` | Run Agent tests with fake model/tool runners |
| `make test-contract` | Round-trip versioned Core/Agent fixtures |
| `make test-frontend` | Run frontend unit and component tests without a live backend |
| `make lint` | Run Python and frontend lint/import-boundary checks |
| `make build` | Type-check and build the frontend; validate Python packaging and generated API types |
| `make package-agent` | Produce a flat source ZIP and SHA-256 manifest |
| `make smoke` | Exercise health, catalog, fake explanation, and contract compatibility |

Until migration is complete, existing `make backend`, `make test`, and `make build` behavior must remain usable or be replaced atomically in the same change.

## 6. Frontend development specification

### Runtime and libraries

Use React 18, strict TypeScript, Vite, React Router, TanStack Query, Zustand, and i18next. Use Vitest with Testing Library for unit/component tests, Mock Service Worker for network scenarios, and Playwright for the judge journey. Keep pitch rendering in semantic React/SVG and use project CSS tokens; a canvas, animation framework, or component library requires a demonstrated need.

Required npm scripts:

| Script | Behavior |
| --- | --- |
| `npm run dev` | Vite development server with `/api` proxy to local Core |
| `npm run lint` | ESLint over TypeScript/React source |
| `npm run typecheck` | `tsc --noEmit` in strict mode |
| `npm run test` | Vitest unit/component suite |
| `npm run api:generate` | Generate transport types from the committed Core OpenAPI snapshot |
| `npm run api:check` | Fail when generated types differ from the OpenAPI snapshot |
| `npm run build` | Typecheck, API check, and production Vite build |
| `npm run preview` | Serve the production bundle locally |
| `npm run e2e` | Run the Playwright smoke journey |

### Screens and layout

The `/` route loads a score-free catalog and viewer defaults. Selecting a fixture navigates to `/match/:matchId` with valid query parameters. The match route contains the score/clock header, SVG pitch, timed overlays, timeline controls, and panels for Moments, Stats, Player, Explain, and Referee. Desktop presents pitch and panel side by side; mobile stacks them and keeps playback controls reachable without covering content.

Do not split into separate pages for panels. A tab change must preserve clock, loaded replay data, selected evidence, and completed explanations.

### State and data fetching

- TanStack Query owns catalog, replay snapshots/deltas, narratives, player data, decisions, and usage status.
- Zustand owns the local clock, playing, speed, selected panel/event, viewer settings, and `contextEpoch`.
- Query keys include match and all server-relevant context. Do not key high-frequency replay queries by every animation-frame clock value.
- Prefetch a bounded delta when the clock approaches `heldUntilMs`; merge by stable ID and reject duplicates or out-of-order ranges.
- A seek increments `contextEpoch`, aborts obsolete requests, clears forward-only held data, and fetches authoritative state at the new cutoff.
- Explanation and referee mutations capture epoch and clock at submission. Stale results become labeled history and cannot change current overlays or facts.
- Persist only validated viewer settings in `localStorage`. Keep the anonymous token in `sessionStorage` and create/refresh it lazily for generation routes.

### API client

Use one fetch wrapper that supplies the public API base URL, `Authorization`, `Idempotency-Key`, JSON headers, abort signals, and request correlation metadata. It parses RFC 9457 errors into typed application errors. On `session_expired`, request one new anonymous token and retry the mutation once with the same idempotency key. Do not automatically retry other mutations.

Development uses the Vite `/api` proxy. Production uses `VITE_API_BASE_URL` pointing directly to Container Apps. Only `VITE_` values intended for browsers may enter the bundle; CI scans the built output for known secret names.

### Replay correctness

The browser clock advances through `requestAnimationFrame` using elapsed wall time multiplied by 1× or 4×. Clamp it to match bounds and pause when the tab is hidden if elapsed time would create a visible jump. Render only records with `availableMs <= clockMs`, including timeline markers and corrected decisions. Seeking before an incident removes that incident, its derived score/statistics, cues, cards, decisions, and active generated context.

The pitch uses the latest visible supplied snapshot and never interpolates missing factual positions. Visual annotations must have text equivalents. Evidence chips highlight supplied entities and may seek backward to the evidence time.

### Resilience and accessibility

- Show a waking-server state after 1.5 seconds; retry GET requests at most three times with bounded backoff.
- Show factual replay when Agent Service, model generation, usage state, or anonymous-session creation fails.
- Disable repeated mutation clicks while pending and announce completion/failure in an appropriate live region.
- Support keyboard playback and seeking, logical tab order, visible focus, accessible names, reduced motion, WCAG AA contrast, and 200% text zoom.
- Localize every UI-owned string. Never construct sentences from translated fragments or mark generated HTML as safe.
- Verify desktop and 390 px layouts; controls need at least a 44 px touch target where practical.

### Design originality and asset licensing

- Build an original Next Whistle visual language from the product's replay, evidence, and referee concepts; do not clone a reference product or design-system showcase.
- Use references for general principles only. Do not copy distinctive layouts, component compositions, branded artwork, proprietary tokens, or marketing copy.
- Use original project assets or assets licensed for the intended use. Track source, author, license, and required attribution in `frontend/ASSETS.md`.
- Prefer original CSS/SVG graphics, compatible open-source icons, and synthetic match content. Exclude any asset whose usage rights cannot be verified.
- Include an originality and license review in the frontend release gate.

### Frontend acceptance tests

- Match picker contains no scores or future-event spoilers.
- Playback at 1×/4×, pause, restart, forward/backward seek, and delta loading remain consistent.
- A seek invalidates stale requests and removes future markers immediately.
- Changing language, level, club, or player invalidates only incompatible generated slots.
- Evidence selection highlights the correct pitch item and never seeks into the future.
- Session refresh happens once; rate-limit and generation-disabled errors show specific recovery text.
- Facts-only mode remains navigable with the Agent Service unavailable.
- The production bundle loads under Static Web Apps CSP and its SPA fallback supports direct match URLs.
- Every shipped external visual asset appears in `frontend/ASSETS.md`, and the final interface passes a documented similarity review against any named references.

## 7. Work packages and gates

### WP1 — Service boundaries

- Move the existing FastAPI scaffold into Core without changing routes.
- Create Shared packages for API base models, identifiers, viewer settings, `BoundContext`, `AgentRequest`, and `AgentResponse`.
- Create Agent `main.py`, workflow/role directories, and a fake workflow; keep Agent Framework imports behind the runtime factory.
- Add contract fixtures and import-boundary tests.
- Update Makefile and README commands.

**Gate:** current catalog and health tests pass from the new Core path; Agent request/response fixtures round-trip; importing either service does not require Azure credentials.

### WP2 — Deterministic data and domain

- Generate and validate versioned synthetic matches, squads, events, snapshots, rules, and suggested questions.
- Keep clubs, players, commentary, events, and positions fictional. Document external rule references and other factual sources in `data/SOURCES.md`, including license, retrieval date, section, and attribution requirements.
- Implement replay, statistics, geometry, overlays, detectors, and evidence retrieval in Shared.
- Enforce availability cutoff on every query.
- Implement local repository adapters before Blob adapters.

**Gate:** golden score/stat tests, cutoff equality and VAR-delay tests, geometry tests, dataset validation, and source/license review pass deterministically.

### WP3 — Core APIs and controls

- Split routes into health, matches/replay, sessions, explanations/recaps, and referee questions.
- Add session signing, rate/concurrency limits, allowance reservation/settlement, idempotency, cache, and factual templates.
- Add `AgentServiceClient` protocol plus fake and Foundry adapters.
- Return RFC 9457 errors and propagate `X-Request-Id`.

**Gate:** all factual endpoints work with no Agent Service; fake generation covers success, timeout, malformed response, exhausted allowance, and duplicate request.

### WP4 — Agent workflow

- Implement role specifications for Analyst, Referee, Storyteller, and optional Critic.
- Implement strict tool schemas, per-role allowlists, immutable bound context, call limits, and evidence ledger.
- Implement evidence brief, workflow routing, structured outputs, validators, and one routed retry.
- Host through `ResponsesHostServer`; do not add FastAPI.
- Keep Critic disabled until the required flow is stable.

**Gate:** fake model scripts cover every route and denial; Storyteller/Critic cannot call tools; model-supplied match or cutoff values cannot widen context.

### WP5 — Frontend integration

- Establish the app/router/providers structure, generated API types, typed client, error boundary, design tokens, and `en` plus the reviewed second locale.
- Move the existing match picker into the catalog feature without changing its working catalog request.
- Implement Zustand playback/settings state and TanStack Query resources before building the match screen.
- Implement the SVG pitch, local replay clock, delta prefetch/merge, score bug, spoiler-safe timeline, cue overlays, Moments, Stats, and Player panels.
- Implement anonymous-session acquisition, explanation/compare/recap slots, evidence navigation, decisions, Ask the Ref, and deterministic geometry overlays.
- Ignore or archive stale mutation results using `contextEpoch`; abort stale GET requests.
- Provide loading, waking-server, offline, rate-limit, cached, generation-disabled, and facts-only states.
- Add keyboard operation, live-region announcements, reduced-motion behavior, contrast verification, text equivalents, and responsive layouts.
- Add `staticwebapp.config.json` with SPA fallback, CSP, and security headers; verify the production build through a local static server.

**Gate:** lint, TypeScript, API drift check, Vitest/Testing Library, and production build pass. Playwright completes the judge journey at 390 px and desktop widths, including seek invalidation, one explanation, one referee answer, language/settings changes, and a forced facts-only response.

### WP6 — Foundry and cloud integration

- Pin tested Agent Framework and hosting versions.
- Verify the local `/readiness` and `/responses` protocol.
- Package a flat Python 3.13 source ZIP and deploy one Hosted Agent version.
- Build Core to public GHCR by digest and deploy Container Apps.
- Provision Blob, monitoring, budget alerts, identities, and Static Web Apps from reviewed IaC.
- Run contract and smoke tests before routing production Core to the new Agent version.

**Gate:** fresh-browser cloud smoke passes; one trace crosses Core, Agent workflow, tools, and model; `az acr list -g rg-nextwhistle-prod` is empty.

### WP7 — Evaluation, freeze, and cleanup readiness

- Evaluate golden explanations and referee cases; fix prompts once and pin versions.
- Pre-generate only the highest-value demo responses within `PREGEN_MAX_USD=2`.
- Capture artifact identifiers and demo evidence.
- Install and dry-run the guarded expiry workflow without deleting resources.

**Gate:** factual validator pass rate meets the LLD target, demo paths use cache where planned, cumulative forecast stays below USD 20, and cleanup inventory is complete.

## 8. Contract and compatibility tests

Store representative JSON fixtures under `backend/tests/contract/fixtures/v1/`. Tests must prove:

- Core emits an `AgentRequest` the Agent parses with unknown fields rejected.
- Agent emits an `AgentResponse` the Core parses with unknown fields rejected.
- CamelCase serialization and snake_case Python round-trip.
- Unsupported `schemaVersion` fails closed.
- Request ID, match, cutoff, dataset, rules, event, and operation cannot change in the response.
- Evidence IDs and statistic references are validated against the request-bound dataset.
- A newer optional field remains backward-compatible only when explicitly declared optional.

Bump the contract version for breaking changes. Deploy the Agent first only when the existing Core can parse it; otherwise deploy a backward-compatible Core before the Agent.

## 9. Test strategy

| Layer | Tooling | What it proves |
| --- | --- | --- |
| Shared unit/golden/property | pytest, Hypothesis | Replay, stats, geometry, cutoff, schemas |
| Core unit/API | pytest, FastAPI TestClient/httpx | Routes, sessions, limits, cache, fallback |
| Agent unit/workflow | pytest with fake runners | Routing, tools, validation, retries |
| Contract | pytest over JSON fixtures | Independent deployment compatibility |
| Frontend | Vitest and Testing Library | State, visibility, stale-response handling |
| End to end | Playwright | Judge journey through frontend and Core |
| Live protocol | Small explicit suite | Foundry host, model, identity, and tracing |

Start with the narrowest relevant tests. Live suites require an explicit environment flag and a dollar cap. Never weaken cutoff, evidence, or usage assertions to make tests pass.

## 10. Configuration

Core configuration includes storage mode/account, Agent name/version, allowed origins, session keys, generation switches, allowance units, concurrency, and rate limits. Agent configuration includes Foundry project/model deployments, role limits, prompt/tool/validator versions, workflow timeout, retry count, uncertainty threshold, and Critic switch. Frontend build configuration includes only the public Core API base URL, application version, and optional non-secret telemetry switch.

Validate configuration at startup. Production must fail readiness for missing required data or malformed configuration, but readiness must not depend on a successful model call.

Commit `.env.example` files with placeholders only. Secrets belong in ignored local files or Azure secret references.

## 11. Build and release artifacts

CI produces and records:

- Core test report and public GHCR image digest.
- Agent test report, flat source ZIP, SHA-256, and dependency manifest.
- Shared contract fixture/version report.
- Frontend production bundle, generated-API drift result, accessibility/component test report, and Playwright result.
- Dataset and rules checksums.
- Security scan results.

Deployment workflows remain separate for frontend, Core, and Agent. Production promotion pins every artifact; no deployment uses `latest`. Rollback selects a previous artifact rather than rebuilding source.

## 12. Definition of done

A work package is complete when its behavior is implemented, focused tests pass, relevant broader checks pass once, documentation matches actual commands and paths, and no secret or paid call is required for ordinary local tests.

The contest release is complete when the deployed no-login experience passes the judge journey, evidence never crosses the viewer cutoff, generated claims validate or fall back, cloud/model usage remains below USD 20, artifact versions are recorded, and the end-of-judging cleanup workflow is ready.
