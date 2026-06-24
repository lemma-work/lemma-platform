# syntax=docker/dockerfile:1

# Stage 1: Build stage with uv
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

# Set working directory
WORKDIR /app

# Enable bytecode compilation for faster startup
ENV UV_COMPILE_BYTECODE=1
# Use copy mode to avoid hardlink warnings across filesystems
ENV UV_LINK_MODE=copy

# Install git for git dependencies
RUN apt-get update && apt-get install -y git

# Build from the monorepo root:
#   docker build -f lemma-backend/Dockerfile .

# Copy dependency files first for better layer caching
COPY lemma-backend/pyproject.toml lemma-backend/uv.lock ./

# Copy local path dependencies and repo-native skills.
COPY agentbox-client /agentbox-client
COPY lemma-backend/lemma-connectors ./lemma-connectors
COPY lemma-skills ./lemma-skills

# Install dependencies into /app/.venv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy backend application code
COPY lemma-backend/ ./

# Sync the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Stage 2: Runtime stage
FROM python:3.14-slim-bookworm

# Set working directory
WORKDIR /app

# Create non-root user for security
# -d /home/appuser: explicitly set home directory
# -m: create the home directory
RUN groupadd -r appuser && useradd -r -g appuser -d /home/appuser -m appuser

# Create cache directory for composio with proper permissions
RUN mkdir -p /app/.composio && chown -R appuser:appuser /app/.composio

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /agentbox-client /agentbox-client
# Copy skills from the builder stage explicitly instead of relying on the blanket repo copy.
COPY --from=builder --chown=appuser:appuser /app/lemma-skills /app/lemma-skills

# Copy backend application code
COPY --chown=appuser:appuser lemma-backend/ ./

# Browser SDK bundle served at /public/sdk/lemma-client.js for no-build desks.
# settings.resolve_browser_sdk_path() looks here first in the container.
COPY --chown=appuser:appuser lemma-typescript/public/lemma-client.js /app/browser-sdk/lemma-client.js
# Opt-in web components bundle served at /public/sdk/lemma-ui.js (<lemma-agent-task>
# / <lemma-agent-thread>). settings.resolve_browser_ui_path() looks here first.
COPY --chown=appuser:appuser lemma-typescript/public/lemma-ui.js /app/browser-sdk/lemma-ui.js

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    COMPOSIO_CACHE_DIR=/app/.composio \
    HOME=/home/appuser

# Switch to non-root user
USER appuser

# Expose application port
EXPOSE 8000
# Run the application
CMD ["uvicorn", "app.app:app", "--host", "0.0.0.0", "--port", "8000"]
