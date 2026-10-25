#!/usr/bin/env bash
# Create or update the contest environment in ONE resource group.
#   infra/deploy.sh --what-if     preview only, changes nothing
#   infra/deploy.sh               preview, then apply after you confirm
# Needs: az login, a subscription you intend to use, and (first run) a region, e.g. LOCATION=eastus2.
set -euo pipefail

cd "$(dirname "$0")/.."
RG="${RG:-rg-nextwhistle-prod}"
STATE=".local/cloud.env"
MODE="apply"; [[ "${1:-}" == "--what-if" ]] && MODE="what-if"

fail() { echo "error: $*" >&2; exit 1; }
command -v az >/dev/null || fail "Azure CLI not found"
command -v openssl >/dev/null || fail "openssl not found"

account="$(az account show --query '{name:name, id:id}' -o tsv 2>/dev/null)" || fail "not signed in. Run: az login"
sub_name="$(cut -f1 <<<"$account")"; sub_id="$(cut -f2 <<<"$account")"
echo "Subscription: $sub_name ($sub_id)"
echo "Resource group: $RG"
read -r -p "Type the subscription ID to confirm this is the right one: " typed
[[ "$typed" == "$sub_id" ]] || fail "subscription ID did not match; nothing was changed"

LOCATION="${LOCATION:-}"
if [[ -z "$LOCATION" ]]; then read -r -p "Azure region (for example eastus2): " LOCATION; fi
[[ -n "$LOCATION" ]] || fail "a region is required"
BUDGET_EMAIL="${BUDGET_EMAIL:-}"
if [[ -z "$BUDGET_EMAIL" ]]; then read -r -p "Email for budget alerts: " BUDGET_EMAIL; fi
[[ "$BUDGET_EMAIL" == *@* ]] || fail "a valid email is required"

# Secrets are created once, kept in a private local file, and never printed or committed (.local/ is ignored).
mkdir -p .local; umask 077
if [[ ! -f "$STATE" ]]; then
  printf 'SESSION_SIGNING_KEY=%s\nIP_HASH_KEY=%s\n' "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" > "$STATE"
  echo "Generated session and IP-hash keys in $STATE"
fi
# shellcheck disable=SC1090
source "$STATE"

if free="$(az cosmosdb list --query "[?enableFreeTier].name | [0]" -o tsv 2>/dev/null)" && [[ -n "$free" ]]; then
  echo "Note: a free-tier Cosmos DB account already exists in this subscription ($free). Only one is allowed, so this deploy will use a paid account unless you set COSMOS_FREE_TIER=false and accept that."
  [[ "${COSMOS_FREE_TIER:-}" == "false" ]] || fail "set COSMOS_FREE_TIER=false to continue, or delete the other free account"
fi

echo "Registering resource providers (first run takes a few minutes)..."
for provider in Microsoft.App Microsoft.CognitiveServices Microsoft.DocumentDB Microsoft.OperationalInsights Microsoft.Insights Microsoft.Web Microsoft.Storage Microsoft.ManagedIdentity Microsoft.Consumption; do
  az provider register --namespace "$provider" --wait >/dev/null
done

az group create --name "$RG" --location "$LOCATION" --output none --tags \
  Project=NextWhistle Environment=Contest Owner=Dhiraj \
  JudgingEnds=2026-11-11T07:59:00Z ExpiresOn=2026-11-11T08:15:00Z

dev_id="$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)"
params="$(mktemp)"; trap 'rm -f "$params"' EXIT
python3 - "$params" "$dev_id" "$BUDGET_EMAIL" "$(date +%Y-%m-01)" "${COSMOS_FREE_TIER:-true}" "${DEPLOY_APP:-false}" <<'PY'
import json, os, sys
path, dev, email, start, free, deploy_app = sys.argv[1:7]
values = {
    "sessionSigningKey": os.environ["SESSION_SIGNING_KEY"], "ipHashKey": os.environ["IP_HASH_KEY"],
    "developerPrincipalId": dev, "budgetContactEmails": [email], "budgetStartDate": start, "cosmosFreeTier": free == "true", "deployApp": deploy_app == "true",
}
for name in ("CORE_IMAGE", "ALLOWED_ORIGIN", "AGENT_SERVICE_URL"):
    if os.environ.get(name):
        values[{"CORE_IMAGE": "coreImage", "ALLOWED_ORIGIN": "allowedOrigin", "AGENT_SERVICE_URL": "agentServiceUrl"}[name]] = os.environ[name]
json.dump({"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#", "contentVersion": "1.0.0.0",
           "parameters": {k: {"value": v} for k, v in values.items()}}, open(path, "w"))
PY
export SESSION_SIGNING_KEY IP_HASH_KEY

echo; echo "What-if (what would change in $RG):"
az deployment group what-if --resource-group "$RG" --template-file infra/main.bicep --parameters "@$params" --no-pretty-print --output table || az deployment group what-if --resource-group "$RG" --template-file infra/main.bicep --parameters "@$params"
[[ "$MODE" == "what-if" ]] && { echo "What-if only: nothing was changed."; exit 0; }

read -r -p "Apply this deployment? Type 'yes' to continue: " answer
[[ "$answer" == "yes" ]] || fail "cancelled; nothing was deployed"
az deployment group create --resource-group "$RG" --template-file infra/main.bicep --parameters "@$params" \
  --query properties.outputs --output json > .local/cloud-outputs.json
echo "Deployed. Non-secret outputs saved to .local/cloud-outputs.json:"
python3 -c "import json;[print(f'  {k}: {v[\"value\"]}') for k,v in json.load(open('.local/cloud-outputs.json')).items()]"
