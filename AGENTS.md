# Repository Guidelines

## Project Structure & Module Organization

This repository contains Football Match Companion planning documents and an initial local application scaffold. Use `docs/architecture.md` for service boundaries and `docs/build-spec.md` for implementation order and acceptance gates. Use the low-level design for detailed domain/API contracts and the high-level design for product intent. See `README.md` for implemented features; planned paths and commands are not current behavior until the scaffold provides them.

The monorepo structure (some modules are still planned) is:
- `backend/services/core_application_service/`: Python 3.12/FastAPI public API, sessions, limits, caches, storage adapters, and Agent Service client. The current `backend/app/` scaffold will move here.
- `backend/services/agent_service/`: Python 3.13 Microsoft Agent Framework workflow hosted by Foundry through `ResponsesHostServer`; do not add FastAPI to this service.
- `backend/shared/`: versioned Core-to-Agent contracts and deterministic domain/evidence code used by both deployments.
- Backend tests live beside each service, with serialized compatibility fixtures under `backend/tests/contract/`.
- `frontend/src/`: React/TypeScript application built with Node 22 and Vite.
- `data/`: synthetic match generators, validators, curated rules, and versioned output.
- `scripts/`, `infra/`, and `eval/`: operational scripts, Bicep infrastructure, and evaluation fixtures.

Keep domain calculations independent of FastAPI, Azure SDKs, and agent orchestration. Both backend services may import `shared`; they must never import each other. Treat the frontend, Core Service, and Agent Service as separate deployment artifacts.

## Build, Test, and Development Commands

Current Makefile targets:

- `make setup`: install backend and frontend dependencies (requires registry access).
- `make backend` / `make frontend`: run the API and Vite in separate terminals.
- `make test`: run the backend API and startup tests.
- `make build`: check TypeScript and build frontend assets.
- `make data`: deterministically generate the versioned history, demo matches, clips, and chunked tracking data.
- `make validate-data`: validate hashes, cutoffs, chronology, geometry, movement limits, and full-match coverage.

Docker Compose, `make up`, `make down`, `make seed`, `make it`, and `make e2e` remain planned. `data/catalog/` holds spoiler-free fixture metadata; validated replay datasets live under `data/datasets/`.

The current scaffold makes no model calls. Use the planned `local-fake` mode for daily development and CI once agent interfaces exist; reserve `local-foundry` for model evaluations.

## Coding Style & Naming Conventions

Use four-space Python indentation and snake_case identifiers. Keep API JSON camelCase and stored JSON snake_case, with conversion owned by Pydantic models. Follow the design's prefixed IDs, such as `m_`, `p_`, and `e_`. Planned checks include Ruff, mypy, and frontend linting; formatter configuration is not yet present.

## Testing Guidelines

Use pytest/pytest-asyncio and Hypothesis for backend tests, Vitest with Testing Library for frontend tests, and Playwright for smoke coverage. Name Python tests `test_*.py`. Prioritize cutoff visibility, replay consistency, evidence validation, and factual fallbacks. No numeric coverage threshold is specified.

## Commit & Pull Request Guidelines

No Git history is available to establish existing conventions. Use concise, imperative commit subjects. PRs should describe behavior changes, link relevant design sections or issues, report validation performed, and include screenshots for UI changes.

## Security & Configuration

Keep secrets in ignored local environment files or Azure secrets. Commit only secret-free configuration. Preserve anonymous-session checks, usage limits, and request-bound evidence cutoffs when implementing generation routes.

## Official Azure References

- [Azure MCP Server overview](https://learn.microsoft.com/en-us/azure/developer/azure-mcp-server/overview): consult for developer tooling that lets agents inspect and manage Azure resources through MCP. Use connected tools when available and appropriate to the task. This reference concerns development tooling; the application's evidence tools remain in-process Foundry functions as specified in the design.
- [Azure CLI documentation](https://learn.microsoft.com/en-us/cli/azure/?view=azure-cli-latest): consult for `az` command syntax, authentication, resource management, deployment, and troubleshooting. Verify commands against the installed CLI version and required extensions before execution.

Confirm the target subscription and resource group before Azure operations. Follow the task's authorization and existing operational boundaries when using either tool; linking documentation does not install or connect tooling.

## Agent Working Rules

Adapted from [multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills/blob/main/CLAUDE.md). Apply proportionate judgment to trivial tasks.

### Think Before Coding

Explain assumptions, competing interpretations, and meaningful tradeoffs before implementation. Suggest simpler alternatives when appropriate. If ambiguity blocks a correct solution, identify it and ask for clarification.

### Simplicity First

Implement the smallest solution that meets the request. Avoid speculative features, unnecessary configuration, one-use abstractions, and handling scenarios that cannot occur. Revisit implementations that are longer or more complicated than necessary.

### Surgical Changes

Limit edits to the requested work and follow surrounding conventions. Leave unrelated formatting, comments, refactors, and existing dead code alone; flag unrelated findings separately. Remove imports, variables, and functions made unused by your own edits. Each changed line should have a clear connection to the task.

### Goal-Driven Execution

Define observable completion criteria and verify them before finishing. For substantial tasks, outline steps with a corresponding check for each. Reproduce bugs with regression tests, cover rejected inputs when adding validation, and check behavior before and after refactoring. Iterate until the relevant checks pass; report any checks that could not run.

### Inspect Before Assuming

Read the relevant design sections and existing implementation before editing. Check manifests and configuration before choosing commands or dependencies. Treat directory layouts and commands described above as plans until confirmed on disk. Never invent files, APIs, command output, or completed checks.

### Resolve Decisions at the Right Level

Follow explicit user instructions first. Use `docs/architecture.md` for service boundaries, `docs/build-spec.md` for build order, the low-level design for detailed contracts, and the high-level design for product intent. Consult the schedule only for contest timing. Flag material contradictions instead of silently changing architecture. Make routine, reversible implementation choices independently and state consequential assumptions.

### Protect Match and Evidence Invariants

- Apply `available_ms <= cutoff_ms` consistently to replay, retrieval, statistics, overlays, and generated explanations. Test equality at the boundary and delayed decisions such as VAR reversals.
- Compute numerical facts and decision geometry in deterministic code. Models may explain evidence but must not invent measurements, events, or rule citations.
- Bind match identity and evidence cutoff in backend context. Model-supplied tool arguments must never widen that context.
- Personalization may change presentation, never facts or rule interpretation. Keep factual replay usable when generation fails.
- Include the versions and context required by the design in cache keys; never reuse an explanation across incompatible evidence or viewer settings.

### Keep External Integrations Contained

Keep Azure and Foundry calls behind the storage and agent interfaces. Prefer fake agents and local storage for development checks. Verify unfamiliar SDK methods against installed versions and official documentation. Add a dependency only when existing code and the standard library cannot reasonably meet the requirement; explain why it is needed.

### Protect Design Originality and Licensing

Create an original Next Whistle interface from the product requirements and football-replay concepts. Design systems, galleries, and competing products may inform general principles, but do not reproduce a recognizable layout, distinctive visual treatment, proprietary component set, copy, logo, illustration, or branded asset. Use project-created assets or assets whose license permits the intended use. Record each external asset's source, author, license, and attribution requirement in `frontend/ASSETS.md`; if rights are unclear, do not use it. Prefer original SVG/CSS graphics, open-source icons under a compatible license, and synthetic match data. Review final screens for distinctive similarity before release.

Apply the same rule to data and football-law material. Keep matches, clubs, players, commentary, and positional data fictional and project-created. Record every external factual source, dataset, rule reference, license, retrieval date, and required attribution in `data/SOURCES.md`. Paraphrase rules and explanations in original language, cite the authoritative source and section, and never copy substantial source text. Do not import scraped match data, broadcast footage, team crests, league marks, player likenesses, or commentary unless documented rights explicitly permit the intended use.

### Preserve User Work and Operational Boundaries

Inspect existing changes before editing and preserve work outside the task. Avoid destructive Git commands and file deletion unless authorized. Local edits do not imply permission to deploy, provision cloud resources, or incur paid model usage. Never print credentials, session signing keys, or private environment values in logs or responses.

### Verify Behavior and Report Evidence

Start with checks that exercise the changed behavior, then run relevant broader checks. Use deterministic fixtures; avoid live model calls in ordinary tests. Do not weaken assertions or suppress failures to obtain a passing result. Report what changed, which checks ran and their outcomes, and any remaining limitation. Distinguish code inspection from executed validation, and keep this guide current when scaffolding replaces planned commands with working ones.
