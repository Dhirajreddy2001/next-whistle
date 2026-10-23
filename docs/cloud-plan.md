# Next Whistle Cloud Plan

## Purpose and operating window

This document is the deployment and operations plan for the contest release. It records the current cloud decisions where they differ from earlier planning documents. The application must remain publicly available through judging, which ends **November 10, 2026 at 11:59 PM Pacific (November 11 at 07:59 UTC)**. Cleanup begins at **November 11 at 12:15 AM Pacific (08:15 UTC)**.

Use one disposable production resource group so Azure resources can be removed together:

```text
Resource group: rg-nextwhistle-prod
Required tags:
  Project=NextWhistle
  Environment=Contest
  Owner=Dhiraj
  JudgingEnds=2026-11-11T07:59:00Z
  ExpiresOn=2026-11-11T08:15:00Z
```

Do not deploy until an active Azure subscription is visible in `az account list`. Confirm the selected subscription with `az account show` before every provision or cleanup operation.

## Target architecture and resources

| Resource | Purpose | Configuration and cost control |
| --- | --- | --- |
| Azure Static Web Apps | Hosts the React/Vite frontend | Free plan, Microsoft-provided domain, anonymous public access, GitHub deployment |
| Azure Container Apps environment | Runtime boundary for the Core Application Service | Consumption workload profile and Log Analytics integration |
| Azure Container App | Hosts the FastAPI Core Application Service | 0.5 vCPU/1 GiB initially, HTTP ingress, `minReplicas=0` normally, `maxReplicas=1`; consider `minReplicas=1` only during judging after measuring cost |
| Microsoft Foundry resource and project | Owns model deployments, hosted agent versions, traces, and evaluations | Pay-as-you-go models, minimal quota, no optional Code Interpreter or File Search |
| Foundry Hosted Agent | Runs the Agent Service built with Microsoft Agent Framework | Deploy from a source ZIP; start with the smallest tested CPU/memory tier and a two-minute session idle timeout |
| Azure Storage account | Stores private datasets, approved rules, generated cache, and the persistent usage ledger | Standard general-purpose v2, locally redundant storage, hot tier, public blob access disabled |
| Blob containers | Separate data by retention and permission | `datasets`, `rules`, `cache`, `usage`, and `temporary`; lifecycle deletion applies only to `temporary` objects older than seven days |
| Log Analytics workspace | Receives Container Apps logs | Shortest suitable retention, restrained log levels, and a small daily ingestion cap |
| Application Insights | API, workflow, dependency, and failure telemetry | Workspace-based; sample routine requests and never log prompts containing secrets or full anonymous tokens |
| Azure Cost Management budget | Warns before the contest ceiling is reached | **USD 20 hard project ceiling**, scoped to the contest resource group, with notifications at USD 5, 10, 15, 18, and 19.50 |
| User-assigned or system-assigned managed identities | Removes service credentials from application configuration | Grant only the Blob and Foundry data-plane roles each service needs |

No database, virtual network, API Management instance, Key Vault, Azure Container Registry, custom domain, or paid Static Web Apps plan is required for the contest MVP. Container Apps secrets may hold the anonymous-session signing key and IP-hash key. Replace this with Key Vault only if the project later needs longer-lived secret governance.

## Deployment layout

There are three independently versioned deployments: the frontend on Static Web Apps, the FastAPI Core Application Service on Container Apps, and the Microsoft Agent Framework Agent Service on Foundry Hosted Agents. The Agent Service uses Foundry's protocol host and does not run FastAPI.

The browser loads static assets from Static Web Apps and calls only the Core Application Service over HTTPS. The Core Service owns match data access, deterministic calculations, anonymous-session validation, caching, limits, and calls to the Agent Service. It invokes the Foundry Hosted Agent through managed identity. Both backend services use the same versioned contracts and deterministic evidence package; Blob Storage remains private.

Restrict Core Service CORS to the production Static Web Apps origin and local development origins. Expose only the Container App ingress and the Static Web Apps site publicly. Disable shared-key storage access when managed-identity integration has been verified.

Create all Azure resources through versioned Bicep under `infra/`; avoid portal-only production changes. Before applying infrastructure, run a resource-group-scoped what-if deployment and inspect additions, deletions, role assignments, SKUs, and replica settings.

## Avoiding Azure Container Registry

Next Whistle does not create or depend on a customer-managed Azure Container Registry.

### Core Application Service

Build its container in GitHub Actions and publish it to **GitHub Container Registry (GHCR)** as `ghcr.io/<owner>/next-whistle-core`. Because the contest repository is public, publish the runtime image publicly and configure Container Apps without registry credentials. Pin the deployed image by immutable digest rather than `latest`, for example:

```text
ghcr.io/<owner>/next-whistle-core@sha256:<digest>
```

The build workflow must run backend tests, build the image, scan it, push only from the protected main branch, and record the digest used by the deployment. A public image can be downloaded without storing a GitHub token in Azure.

### Agent Service

Deploy `backend/services/agent_service` to Foundry Hosted Agents using the supported **Source Code (ZIP upload)** path. The ZIP contains only the agent runtime, its dependency manifest, and required configuration files. Foundry performs the managed build; the project does not provision or administer ACR. Keep the ZIP or tagged source commit as the reproducible deployment artifact.

After each deployment, verify:

```bash
az acr list --resource-group rg-nextwhistle-prod --output table
```

The result must be empty. Source deployment may use Microsoft-managed build infrastructure internally; the guarantee here is that Next Whistle owns no ACR resource or ACR monthly charge.

## Identity, access, and secrets

Use GitHub Actions OpenID Connect federation for Azure deployments. Store tenant ID, subscription ID, and client ID as GitHub environment variables; do not create a long-lived Azure client secret. Scope the deployment identity to `rg-nextwhistle-prod`. Any elevated role used to establish RBAC must be temporary and removed after bootstrap.

Assign the Core Container App identity:

- Blob Data Reader for immutable datasets and rules.
- Blob Data Contributor only on containers that hold cache and usage records.
- The least-privileged Foundry role that permits invoking the deployed agent.

Give the Hosted Agent identity Blob Data Reader access only to immutable datasets and rules. It executes evidence tools locally against the Core-supplied bound context and cannot write caches or usage records. Store application secrets as Container Apps secret references, rotate them before the final deployment, and never place them in Bicep parameter files, ZIP artifacts, images, logs, or frontend variables.

## Cost and availability controls

The project's entire out-of-pocket cloud budget is **USD 20 total**, including development, evaluation, deployment, the judging window, model calls, storage, networking, and monitoring. Do not assume credits. This limit spans October and November; a new calendar or billing month does not reset it.

Reserve the budget provisionally as USD 6 for Azure hosting, storage, and monitoring; USD 10 for Foundry inference, hosted-agent compute, evaluations, and pre-generation; and USD 4 as contingency. Recalculate these allocations from measured costs before deployment, but never raise their combined total above USD 20.

Azure budget notifications can be delayed and do not stop resources or model calls. Application controls enforce the practical ceiling:

- Use `minReplicas=0` during development and measure whether one warm replica is affordable for judging.
- Limit Container Apps to one replica and Foundry concurrency to the amount needed for the demo.
- Configure Foundry session idle timeout to two minutes. Inactive compute is deprovisioned by the platform.
- Pre-generate likely judge responses and serve them from Blob cache.
- Maintain a durable global generation allowance and per-session/per-IP limits.
- At 80% of the generation allowance, disable free-text referee questions.
- At 95%, disable uncached generation while retaining replay, cached answers, and factual fallbacks.
- At USD 15 total project spend, disable free-text generation regardless of the allowance ledger.
- At USD 18 total project spend, disable all uncached model calls and force every compute service to its lowest safe scale setting.
- At USD 19.50, remove paid model deployments and other nonessential billable resources immediately; keep only the cheapest factual contest experience that fits under the remaining USD 0.50.
- Apply a seven-day lifecycle deletion rule only to `temporary`; retain `datasets`, `rules`, `cache`, and `usage` until contest cleanup.
- Keep telemetry sampling and retention low and alert on unusual 5xx rates, model use, and ingestion.

Check the live site, cache hit rate, remaining generation allowance, failures, and Azure cost once daily during judging. Production changes during this window are limited to restoring the submitted behavior.

## Deployment sequence

1. Confirm the subscription, region availability, model quota, contest dates, and selected Azure context.
2. Register required resource providers and create `rg-nextwhistle-prod` with the required tags.
3. Deploy Storage, monitoring, Foundry, the Container Apps environment, and identities through Bicep.
4. Apply least-privilege RBAC and verify managed-identity access.
5. Build, test, scan, and publish the Core Service image to public GHCR; deploy its digest to Container Apps.
6. Package and deploy the Agent Service as a Foundry source ZIP; configure models, environment variables, limits, and the two-minute idle timeout.
7. Deploy the frontend to Static Web Apps and lock CORS to its production hostname.
8. Seed immutable data, rules, and pre-generated cache artifacts.
9. Run health, replay, cached-generation, live-generation, cold-start, anonymous-browser, and mobile smoke tests.
10. Confirm `az acr list -g rg-nextwhistle-prod` is empty, confirm forecast project spend remains below USD 20, and record resource IDs, revisions, image digest, agent version, and live URL.

## Expiration and automated cleanup

Cleanup has two layers. The manual checklist is primary because scheduled GitHub workflows can run late. A scheduled workflow is the safety net.

Create `.github/workflows/azure-expiry-cleanup.yml` when Azure provisioning is added. It should:

- Support `workflow_dispatch` for a reviewed manual run.
- Run daily after 08:15 UTC.
- Authenticate through GitHub OIDC.
- Read the resource group's `ExpiresOn` tag.
- Exit without changes when the tag is missing, malformed, or still in the future.
- Require the exact project and environment tags before deletion.
- Inventory tagged Azure resources outside the resource group and fail loudly if any exist.
- Delete `rg-nextwhistle-prod` only when UTC time is at or beyond expiry.
- Poll until the group is absent, then list remaining resources tagged `Project=NextWhistle`.

Do not make the cleanup dependent on an application container or Foundry agent that will be deleted. Keep the workflow and OIDC identity outside the resource group. Protect changes to the workflow through branch protection. The scheduled run must proceed automatically after the guarded expiry check; a delayed approval must not leave paid resources running.

## End-of-judging runbook

At **November 11, 2026 at 08:00 UTC**, stop initiating generation and allow requests already in progress to finish. Then:

1. Save the final Cost Management view, deployment inventory, evaluation results, selected traces, logs needed for the submission record, image digest, agent version, and screenshots. Do not export secrets or user-identifying data.
2. Set `GENERATION_ENABLED=false`, set the Core Service minimum replica count to zero, and verify cached/factual behavior one final time.
3. Download any artifacts that must survive Azure deletion to the repository or an approved local archive.
4. At or after **08:15 UTC**, manually dispatch the `azure-expiry-cleanup` workflow. Its date and tag guards remain active for manual runs.
5. Confirm the resource group reaches `Deleted` and that no Azure resource with `Project=NextWhistle` remains in the subscription.
6. Check Foundry for agent versions, model deployments, and projects that were accidentally created outside the resource group; delete any found.
7. Confirm no ACR exists and review Cost Management again after usage data settles. A deleted resource group is not sufficient evidence if separately created resources remain.
8. Remove the GitHub-to-Azure federated credential and deployment service principal after Azure cleanup is verified. Tenant-level identities are not deleted with the resource group.
9. Delete the GHCR package if it is no longer useful, or retain it as a public contest artifact. GHCR cleanup is separate from Azure cleanup and requires GitHub package permissions.
10. Disable the scheduled cleanup workflow after successful verification so it cannot run the following year.

Resource deletion is irreversible and removes the live contest URL. If the contest publishes an official extension before cleanup starts, update both `JudgingEnds` and `ExpiresOn`, commit the change, and verify the tags in Azure. Once cleanup has begun, restoring the site requires a fresh deployment from Bicep, the pinned GHCR digest, the Foundry source artifact, and retained seed data.

## Operational evidence to retain

Keep these records in the repository without credentials:

- Bicep and environment parameter templates.
- Deployment and cleanup workflow definitions.
- Core image digest and source commit.
- Agent source artifact checksum and Foundry version.
- Smoke-test results, evaluation summaries, and final cost summary.
- A short cleanup report stating deletion time and the result of the final tagged-resource inventory.

## Official references

- [Deploy a Foundry hosted agent from source code](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent-code)
- [Manage Foundry hosted sessions](https://learn.microsoft.com/azure/foundry/agents/how-to/manage-hosted-sessions)
- [Azure Container Apps billing](https://learn.microsoft.com/azure/container-apps/billing)
- [Azure Blob Storage lifecycle policies](https://learn.microsoft.com/azure/storage/blobs/lifecycle-management-policy-configure)
- [Use GitHub Actions with Azure login and OpenID Connect](https://learn.microsoft.com/azure/developer/github/connect-from-azure-openid-connect)
- [Azure Cost Management budgets](https://learn.microsoft.com/azure/cost-management-billing/costs/tutorial-acm-create-budgets)
