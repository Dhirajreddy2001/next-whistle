#!/usr/bin/env bash
# Delete EVERYTHING this project created in Azure, in one step, then confirm nothing is left.
#   infra/destroy.sh        asks you to confirm before deleting
# It refuses to touch a resource group that is not tagged Project=NextWhistle and Environment=Contest.
set -euo pipefail

cd "$(dirname "$0")/.."
RG="${RG:-rg-nextwhistle-prod}"
fail() { echo "error: $*" >&2; exit 1; }
command -v az >/dev/null || fail "Azure CLI not found"

account="$(az account show --query '{name:name, id:id}' -o tsv 2>/dev/null)" || fail "not signed in. Run: az login"
sub_name="$(cut -f1 <<<"$account")"; sub_id="$(cut -f2 <<<"$account")"
echo "Subscription: $sub_name ($sub_id)"

az group show --name "$RG" >/dev/null 2>&1 || fail "resource group $RG does not exist in this subscription; nothing to delete"
project="$(az group show --name "$RG" --query 'tags.Project' -o tsv)"
environment="$(az group show --name "$RG" --query 'tags.Environment' -o tsv)"
[[ "$project" == "NextWhistle" && "$environment" == "Contest" ]] || fail "$RG is not tagged Project=NextWhistle and Environment=Contest; refusing to delete it"

echo; echo "These resources will be PERMANENTLY deleted:"
az resource list --resource-group "$RG" --query "[].{name:name, type:type}" --output table
echo
read -r -p "Type the resource group name ($RG) to delete everything: " typed
[[ "$typed" == "$RG" ]] || fail "name did not match; nothing was deleted"

# Foundry accounts are soft-deleted by Azure and keep holding their name and quota until purged.
foundry_accounts="$(az cognitiveservices account list --resource-group "$RG" --query "[].[name,location]" -o tsv)"

echo "Deleting $RG (this removes the identity, Cosmos DB, storage, Foundry, Container Apps, Static Web App and budget)..."
az group delete --name "$RG" --yes
while IFS=$'\t' read -r name location; do
  [[ -z "${name:-}" ]] && continue
  echo "Purging soft-deleted Foundry account $name..."
  az cognitiveservices account purge --name "$name" --resource-group "$RG" --location "$location" || echo "  could not purge $name; check: az cognitiveservices account list-deleted"
done <<<"$foundry_accounts"

echo; echo "Checking for leftovers tagged Project=NextWhistle anywhere in the subscription:"
left="$(az resource list --tag Project=NextWhistle --query 'length(@)' -o tsv)"
deleted="$(az cognitiveservices account list-deleted --query 'length(@)' -o tsv 2>/dev/null || echo 0)"
echo "  resources still tagged: $left"
echo "  soft-deleted Foundry accounts still held: $deleted"
[[ "$left" == "0" ]] || echo "WARNING: some tagged resources remain. Inspect them with: az resource list --tag Project=NextWhistle -o table"
if [[ -n "${APP_ID:-}" ]]; then az ad app delete --id "$APP_ID" && echo "Deleted deployment app registration $APP_ID"; fi
echo "Done. Local secrets in .local/cloud.env were NOT removed; delete that file yourself if you no longer need it."
echo "Also check Cost Management in a day or two, and remove any GitHub federated credential you created."
