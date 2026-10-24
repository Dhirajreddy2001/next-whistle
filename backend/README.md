# Backend Services

The backend deploys as two independent processes. Core owns public HTTP routes, anonymous sessions, replay data, deterministic evidence preparation, and fallbacks. Agent Service hosts a Microsoft Agent Framework workflow using Foundry's Responses protocol. Both consume the versioned contracts in `shared`; neither imports the other.

## Local Development

Run `make setup` once, then start these in separate terminals:

- `make core`: FastAPI at `http://127.0.0.1:8000`.
- `make agent`: Agent Framework Responses host at `http://127.0.0.1:8088`.
- `make frontend`: Vite development server.
- `make test`: Core API tests and Agent workflow tests.

The Agent Service defaults to `AGENT_MODE=fake`, so it makes no model calls. Core falls back to a deterministic, evidence-based answer if the Agent Service is unavailable. Set `AGENT_MODE=foundry`, `FOUNDRY_PROJECT_ENDPOINT`, and `FOUNDRY_MODEL` only when intentionally testing a configured Foundry deployment; this can incur model charges.

Core reads the repository's versioned synthetic data and rules by default. Override `DATASET_PATH`, `CATALOG_PATH`, `SESSION_DB_PATH`, `SESSION_SIGNING_KEY`, `AGENT_SERVICE_URL`, and `AGENT_TIMEOUT_SECONDS` for local or deployed environments. Never commit production signing keys or endpoint credentials.

The old `backend/app` import path forwards to Core for compatibility. New Core work belongs in `services/core_application_service`; shared wire models belong in `shared`; Agent workflow code belongs in `services/agent_service`.
