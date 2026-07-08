# Frontend build
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Backend deps + app (uv)
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS backend
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY backend/ ./
RUN uv sync --frozen --no-dev

# Runtime
FROM python:3.14-slim-trixie
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY --from=backend /app /app
COPY --from=frontend /build/dist /app/static
RUN useradd -u 1000 -m trailcam
USER trailcam
EXPOSE 8000
CMD ["uvicorn", "trailcam.main:app", "--host", "0.0.0.0", "--port", "8000"]
