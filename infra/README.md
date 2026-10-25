# Azure infrastructure

Everything for the contest lives in **one resource group** (`rg-nextwhistle-prod`) and is granted to **one workload identity**, so a single command removes it all.

## What is created (`main.bicep`)

| Resource | Role |
| --- | --- |
| User-assigned managed identity `id-nextwhistle` | The "service account". The Core app runs as it, and it holds every role below. No keys or passwords. |
| Foundry account + project `nextwhistle`, one deployment `deepseek` (DeepSeek-V4-Flash) | Models for the agents. The identity gets *Foundry User* (formerly Azure AI User) and *Cognitive Services OpenAI User* (the role that lets an identity call model deployments). Key access is disabled. |
| Cosmos DB for NoSQL (free tier), database `nextwhistle`, container `state` | Sessions, limits, spend allowance, cache. The identity gets the data-plane *Data Contributor* role. Key access is disabled. |
| Storage account (private, no shared key) | Datasets and rules. The identity gets *Blob Data Contributor*. |
| Container Apps environment + Core app (scale 0 to 1) | Runs as the identity; secrets are Container Apps secrets. |
| Static Web App (free) | The frontend. |
| Log Analytics (0.1 GB/day cap) + Application Insights | Monitoring. |
| Consumption budget (USD 20, alerts at 5, 10, 15, 18 and 19.50) | Email warnings only: Azure budgets do not stop spending. The app's own allowance does that. |

Optionally it also grants **your own Entra user** the two Foundry roles, so you can run the live evaluation from your laptop with `az login` and no key.

## Use

```
az login
make cloud-what-if     # preview, changes nothing
make cloud-up          # confirms the subscription, previews, then deploys after you type "yes"
make cloud-env         # prints the variables for the live eval
make cloud-down        # deletes everything and checks nothing is left
```

`cloud-up` first asks you to type the subscription ID, so it cannot run against the wrong subscription. Secrets (session signing key, IP-hash key) are generated once into `.local/cloud.env` (private, ignored by git, never printed).

## One-step deletion

`make cloud-down` lists everything, asks you to type the resource group name, refuses to touch a group not tagged `Project=NextWhistle` and `Environment=Contest`, deletes the group, then **purges the soft-deleted Foundry account** (Azure otherwise keeps holding its name and quota) and verifies nothing tagged `Project=NextWhistle` remains. The local-testing service account (`sp-nextwhistle-local`) is an Entra app registration outside the group; `cloud-down` deletes it using the ID in `.local/cloud-sp.env`. Only the GHCR image and any GitHub OIDC deployment identity need separate cleanup.

## Before the first real deployment

These are known gaps; none were exercised against Azure from here.

- The Core's agent client sends no Azure token, so it cannot yet call a Foundry Hosted Agent endpoint, which needs a bearer token.
- The agent reads datasets and rules from disk; the hosted source ZIP must either include them or read them from Blob.
- Model names, versions and quota in `main.bicep` are defaults to verify with `az cognitiveservices model list`.
- The template compiles and lints with Bicep 0.48.1, but `what-if` and the first deployment can still surface region, quota or API-version problems.

## Local service account

`sp-nextwhistle-local` is a service principal for running things from your laptop (the live evaluation, local Foundry calls). It holds only *Foundry User* and *Cognitive Services OpenAI User* on `rg-nextwhistle-prod`, its secret expires on 2026-11-12, and the credentials are in `.local/cloud-sp.env` (mode 600, git-ignored, never printed). Load them with `set -a; source .local/cloud-sp.env; set +a`; `DefaultAzureCredential` then uses them automatically. The deployed Core app uses the managed identity instead, which needs no secret.
