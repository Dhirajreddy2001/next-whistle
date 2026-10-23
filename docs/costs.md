# Development and hosting costs

Reviewed October 22, 2026. Free local development does not require Azure credits. This starter uses your machine, local JSON, and no model API. Existing computer and internet costs still apply.

| Component | Local development | Planned Azure deployment |
| --- | --- | --- |
| React frontend | No hosting charge | Static Web Apps Free plan, within its limits |
| FastAPI backend | No hosting charge | Container Apps Consumption free allowance, then metered CPU, memory, and requests |
| Match data and cache | Local files | Blob Storage capacity and operations; potential transfer charges |
| AI explanations | Fake agents planned; no calls in this starter | Model input/output tokens for every agent turn, retry, evaluation, and pre-generation |
| Telemetry | Local logs | Application Insights / Log Analytics ingestion and retention |

Container Apps currently includes 180,000 vCPU-seconds, 360,000 GiB-seconds, and 2 million requests per subscription each calendar month. At 0.5 vCPU and 1 GiB, the compute allowance corresponds to 100 hours of active runtime, assuming no other app consumes it. Idle replicas have separate billing rules; keeping one replica running all month is not guaranteed free. Scaling to zero reduces compute costs but does not remove storage and telemetry costs.

Foundry prompt agents incur model inference charges; optional tools such as Code Interpreter and File Search have additional charges. Our design uses in-process evidence tools and excludes those optional tools. Repeated multi-agent calls and evaluations must be counted, not just visible viewer requests.

The hard out-of-pocket ceiling is **USD 20 for the entire project**, including development, evaluation, model calls, hosting, storage, monitoring, and the judging window. No Azure credits are assumed, and the limit does not reset between October and November. Reserve USD 6 for hosting/storage/monitoring, USD 10 for all Foundry use, and USD 4 for contingency until measured costs justify a lower allocation. Budget alerts are delayed notifications rather than a spending lock, so application generation limits and early shutdown thresholds must keep the measured and forecast total below USD 20. No cloud resources are provisioned by this starter.

Sources:
- [Static Web Apps plans](https://learn.microsoft.com/en-us/azure/static-web-apps/plans)
- [Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing)
- [Foundry Agent Service billing](https://learn.microsoft.com/en-us/azure/foundry/agents/faq#how-am-i-charged-for-foundry-agent-service)
- [Blob storage cost factors](https://learn.microsoft.com/en-us/azure/storage/blobs/access-tiers-overview)
- [Monitor Logs cost factors](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/cost-logs)
