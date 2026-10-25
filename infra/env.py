#!/usr/bin/env python3
"""Print the (non-secret) variables for `make eval-live`, from the outputs saved by infra/deploy.sh."""
import json
import sys
from pathlib import Path

path = Path(__file__).resolve().parents[1] / ".local" / "cloud-outputs.json"
if not path.is_file():
    sys.exit("No .local/cloud-outputs.json yet. Run make cloud-up first.")
out = {key: value["value"] for key, value in json.loads(path.read_text()).items()}
print(f"export FOUNDRY_PROJECT_ENDPOINT={out['foundryProjectEndpoint']}")
print(f"export MODEL_TOOLS={out['modelToolsDeployment']}")
print(f"export MODEL_SMALL={out['modelSmallDeployment']}")
print("# Set the price per million tokens of your two models (check the Azure pricing page), then:")
print("export PRICE_INPUT_PER_MTOK=<usd>   PRICE_OUTPUT_PER_MTOK=<usd>")
print("make eval-live MAX_USD=2 LIMIT=12")
