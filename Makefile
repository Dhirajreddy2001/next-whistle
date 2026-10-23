.PHONY: setup backend frontend test build data validate-data

setup:
	cd backend && uv sync --python 3.12
	cd frontend && npm install

backend:
	cd backend && uv run --python 3.12 uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

frontend:
	cd frontend && npm run dev

test:
	cd backend && uv run --python 3.12 pytest

build:
	cd frontend && npm run build

data:
	python3 data/generator/generate.py all

validate-data:
	python3 data/validator/validate.py
