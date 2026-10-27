# Agent Service (Microsoft Agent Framework, hosted through the Responses protocol). Build from the repository root:
#   az acr build --registry <acr> --image next-whistle-agent:<tag> --file infra/docker/agent.Dockerfile .
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_NO_CACHE=1
RUN pip install --no-cache-dir uv
WORKDIR /app
# The agent runs its evidence tools against the dataset and rules on disk, in the same <repo root>/data layout.
COPY backend/shared backend/shared
COPY backend/services/agent_service backend/services/agent_service
COPY data/datasets/v1.3.0 data/datasets/v1.3.0
COPY data/rules data/rules
RUN uv sync --project backend/services/agent_service --frozen --no-dev --prerelease allow
# These projects have no build backend, so their source folder goes on the path instead of being installed.
ENV PYTHONPATH=/app/backend/services/agent_service/src
RUN useradd --system --create-home app
USER app
ENV AGENT_MODE=foundry
EXPOSE 8088
CMD ["/opt/venv/bin/python", "-m", "next_whistle_agent.main"]
