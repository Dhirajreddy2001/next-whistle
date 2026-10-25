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

## Abuse controls

Core protects the model budget and the service with these layers. Limits are enforced in `services/core_application_service`; the defaults follow LLD section 12 and every value is an environment variable.

| Control | Default | Variable | Response |
| --- | --- | --- | --- |
| New sessions per network (hashed IP) | 10 / hour | `LIMIT_IP_SESSIONS_HOUR` | 429 `rate_limited` + `Retry-After` |
| Generations per session | 8 / hour, 20 / day | `LIMIT_SESSION_HOUR`, `LIMIT_SESSION_DAY` | 429 `rate_limited` |
| Generations per network | 20 / hour | `LIMIT_IP_HOUR` | 429 `rate_limited` |
| Free-text questions | count 2 (never cached) | — | as above |
| API reads per network | 600 / minute | `LIMIT_READ_MINUTE` | 429 `rate_limited` (the UI polls tracking twice a second, so this is higher than the LLD's 120) |
| Concurrent generations | 2 running, 4 queued | `GEN_CONCURRENCY`, `GEN_QUEUE` | 503 `busy` + `Retry-After` |
| Shared spend allowance | 2,000,000 units | `ALLOWANCE_BUDGET_UNITS`, `ALLOWANCE_RESERVE_UNITS`, `OUTPUT_WEIGHT` | 429 `allowance_exhausted` |
| Kill switch | on | `GENERATION_ENABLED=false` | factual templates only, no model call, no limit used |
| Request body size | 32 KB | `MAX_BODY_BYTES` | 413 `payload_too_large` |

- **Allowance:** units are input tokens plus output tokens × `OUTPUT_WEIGHT`. A request reserves `ALLOWANCE_RESERVE_UNITS`, then settles to the agent's measured usage; failures release the reservation, and abandoned reservations expire after five minutes. Set the budget from your USD allowance and token prices.
- **Idempotency:** send `Idempotency-Key` on POSTs. The same key and body replays the stored answer (`Idempotent-Replay: true`); the same key with a different body is 422 `idempotency_conflict`.
- **Cache:** answers are keyed by match, event, intent, level, language, question, dataset, rules and workflow versions. A viewer is only ever served an answer computed at or before their own time (within 60 seconds), so a cached answer cannot reveal the future. Fallback and free-text answers are never cached. Cache hits use no limit or allowance.
- **Network identity:** the peer address is HMAC-hashed (`IP_HASH_KEY`) before it is stored. `X-Forwarded-For` is ignored unless `TRUST_PROXY=true`, and then only the last entry (appended by your proxy) is used.
- **Secrets:** `APP_ENV=production` refuses to start without `SESSION_SIGNING_KEY` of 32 or more characters and sends HSTS. Locally, a key is created once in `.local/session.key` (owner-only) so restarts keep sessions.
- **Headers:** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a restrictive `Content-Security-Policy` and `Permissions-Policy` on every API response. The static site's CSP is in `frontend/public/staticwebapp.config.json`; narrow its `connect-src` to your exact API origin.

These limits live in SQLite, which is right for one replica (the planned `maxReplicas` is 1). Volumetric network floods belong to the platform edge (Azure Front Door or Container Apps ingress), not application code.
