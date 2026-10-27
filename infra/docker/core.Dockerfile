# Core Application Service. Build from the repository root:
#   az acr build --registry <acr> --image next-whistle-core:<tag> --file infra/docker/core.Dockerfile .
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_NO_CACHE=1
RUN pip install --no-cache-dir uv
WORKDIR /app
# The Core reads the dataset and rules from <repo root>/data, so the image keeps the same layout.
COPY backend/shared backend/shared
COPY backend/services/core_application_service backend/services/core_application_service
COPY data/datasets/v1.3.0 data/datasets/v1.3.0
COPY data/rules data/rules
RUN uv sync --project backend/services/core_application_service --frozen --no-dev
# These projects have no build backend, so their source folder goes on the path instead of being installed.
ENV PYTHONPATH=/app/backend/services/core_application_service/src
RUN useradd --system --create-home app && mkdir -p /app/.local && chown app /app/.local
USER app
EXPOSE 8000
CMD ["/opt/venv/bin/uvicorn", "next_whistle_core.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
