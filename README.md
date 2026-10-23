# Next Whistle

Football Match Companion: replay fictional matches, understand turning points, and explore referee decisions using recorded evidence.

## Current implementation

The local foundation includes a FastAPI match catalog and shared live-state clock, startup validation, liveness/readiness endpoints, structured errors, a React/TypeScript replay prototype, and deterministic fictional datasets. Agent workflows and Azure deployment are not implemented yet.

No Azure subscription, credentials, or paid AI calls are required for this foundation.

## Run locally

Install Python 3.12, uv, and Node 22.12+ (Node 22 is the design baseline), then run:

```bash
make setup
```

Run these in separate terminals:

```bash
make backend
make frontend
```

Open http://localhost:5173. Vite proxies `/api` to the backend on port 8000. API docs: http://127.0.0.1:8000/api/docs. Health: `/health/live` and `/health/ready`.

```bash
make test   # backend API and startup checks
make build  # TypeScript check and production frontend build
```

Optional backend settings: `CATALOG_PATH` overrides `data/catalog/matches.json`; `DATASET_PATH` overrides `data/datasets/v1.1.0`; `ALLOWED_ORIGINS` is a comma-separated list (defaults to `http://localhost:5173`). `LIVE_MATCH_ID`, `LIVE_INSTANCE_EPOCH_MS`, and `LIVE_MATCH_DURATION_MS` configure the shared, wall-clock-driven live instance.

For a separately deployed frontend, set `VITE_API_BASE_URL` to the public Core Application Service origin, for example `https://next-whistle-core.example.com`. Leave it unset for local development, where Vite proxies relative `/api` requests.

## Architecture and next work

`backend/app/` currently contains the initial API and local catalog loader; it will move into `backend/services/core_application_service/`. The target backend has two independent deployments: the FastAPI Core Application Service on Container Apps and a Microsoft Agent Framework Agent Service on Foundry Hosted Agents. Shared contracts and deterministic evidence code live under `backend/shared/`. `frontend/src/` contains the independently deployed React application, and `data/` contains fictional fixtures, generators, validators, and versioned datasets. Next: create the target service boundaries before real Foundry integration.

See [cloud costs](docs/costs.md) for the difference between free local development and usage-based Azure hosting.
See the [cloud deployment and cleanup plan](docs/cloud-plan.md) for the Azure resource inventory, ACR-free deployment approach, cost controls, expiry policy, and end-of-judging teardown runbook.

## Synthetic data

Run `make data` to deterministically generate two data products under `data/datasets/v1.1.0/`:

- A 36-match fictional history corpus with aggregate statistics and indexed key moments for comparisons and turning-point retrieval.
- Two complete scripted demo matches with event feeds, evidence snapshots, decisions, fictional player movement profiles, original SVG replay clips, and complete 90-minute-plus tracking. Tracking is simulated at 20 Hz, stored at 2 Hz in one-minute chunks, and rendered with browser interpolation; key-moment clips retain 20 Hz detail.

Run `make validate-data` to verify hashes, chronology, availability cutoffs, references, coordinates, movement limits, full-match coverage, required scenarios, and synthetic provenance. Clip records contain `from_ms`, `to_ms`, evidence IDs, and an animation track ID; the frontend renders those windows from project-created match data, so no broadcast footage is downloaded or licensed. Animation and tracking data are presentation-only unless a record is explicitly linked as evidence.
Use the [architecture specification](docs/architecture.md) for service boundaries and the [build specification](docs/build-spec.md) for repository structure, implementation order, test gates, and release artifacts.
