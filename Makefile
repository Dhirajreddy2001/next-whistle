.PHONY: install install-backend install-agent run eval eval-live setup backend core agent frontend test test-core test-agent build data validate-data

install: install-backend install-agent
	cd frontend && npm install

# Core API environment in backend/.venv (Python 3.12 via uv when available, else the system python3).
install-backend:
	cd backend && if command -v uv >/dev/null 2>&1; then \
		(test -d .venv || uv venv --python 3.12 .venv) && uv pip install --python .venv/bin/python -r requirements-dev.txt; \
	else \
		(test -d .venv || python3 -m venv .venv) && .venv/bin/pip install -r requirements-dev.txt; \
	fi

# Agent Service environment in backend/.venv-agent (Python 3.13, pre-release Agent Framework packages).
install-agent:
	cd backend && (test -d .venv-agent || uv venv --python 3.13 .venv-agent) && uv pip install --python .venv-agent/bin/python --pre -r services/agent_service/requirements.txt

# Run Core (8000) and Agent Service (8088) together; Ctrl+C stops both. Start the UI separately with `make frontend`.
run:
	@trap 'kill 0' INT TERM EXIT; \
	(cd backend && .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) & \
	(cd backend && PYTHONPATH=services/agent_service/src .venv-agent/bin/python -m next_whistle_agent.main) & \
	wait

# Golden Q&A set on the scripted roles: no model, no cost.
eval:
	cd backend && .venv-agent/bin/python ../eval/run_eval.py --mode fake

# Golden Q&A set on real Foundry models. Spends money: needs FOUNDRY_PROJECT_ENDPOINT, MODEL_TOOLS, MODEL_SMALL,
# PRICE_INPUT_PER_MTOK, PRICE_OUTPUT_PER_MTOK, and an explicit cap, e.g. make eval-live MAX_USD=2 LIMIT=12
eval-live:
	cd backend && AGENT_MODE=foundry .venv-agent/bin/python ../eval/run_eval.py --mode foundry --confirm-spend --max-usd $(MAX_USD) --limit $(LIMIT)

setup:
	uv sync --project backend/services/core_application_service --python 3.12
	uv sync --project backend/services/agent_service --python 3.13 --prerelease allow
	cd frontend && npm install

backend:
	$(MAKE) core

core:
	uv run --project backend/services/core_application_service --python 3.12 uvicorn next_whistle_core.main:app --reload --host 127.0.0.1 --port 8000

agent:
	uv run --project backend/services/agent_service --python 3.13 --prerelease allow python -m next_whistle_agent.main

frontend:
	cd frontend && npm run dev

test:
	$(MAKE) test-core
	$(MAKE) test-agent

test-core:
	uv run --project backend/services/core_application_service --python 3.12 pytest backend/tests

test-agent:
	uv run --project backend/services/agent_service --python 3.13 --prerelease allow pytest backend/services/agent_service/tests

build:
	cd frontend && npm run build

data:
	python3 data/generator/generate.py all

validate-data:
	python3 data/validator/validate.py
