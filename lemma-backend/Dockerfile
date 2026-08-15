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
COPY lemma-pod-bundle /lemma-pod-bundle
COPY lemma-backend/lemma-connectors ./lemma-connectors
COPY lemma-skills ./lemma-skills

# Install dependencies into /app/.venv. The local extra ships MarkItDown in the
# backend image for the managed all-in-one composition.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --extra local

# Copy backend application code
COPY lemma-backend/ ./

# Sync the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra local

# Stage 2: Runtime stage
FROM python:3.14-slim-bookworm

# Set working directory
WORKDIR /app

# Function dependency resolution happens before a revision becomes READY. The
# runtime image keeps the same pinned uv binary as the build stage; invocation
# sandboxes never contain a package manager.
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv

# Remove the base image's pip. Function dependency resolution goes through
# `uv pip`, which is uv's own implementation and needs nothing here; the system
# pip exists only because python:slim ships it. Its *vendored* msgpack and
# setuptools are the only HIGH findings left in this image, and no dependency
# of ours can move them -- they are pip's, not the application's.
RUN rm -rf /usr/local/lib/python3.14/site-packages/pip \
           /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14

# Create non-root user for security
# -d /home/appuser: explicitly set home directory
# -m: create the home directory
RUN groupadd -r appuser && useradd -r -g appuser -d /home/appuser -m appuser

# Create cache directory for composio with proper permissions
RUN mkdir -p /app/.composio && chown -R appuser:appuser /app/.composio

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
# uv installs monorepo path dependencies as editable links, so the linked
# source has to survive into the runtime image or importing lemma_pod_bundle
# fails once the builder stage is discarded. `sandbox_runtime` needs no such
# line: it lives inside lemma-backend/, which is copied wholesale below.
COPY --from=builder --chown=appuser:appuser /lemma-pod-bundle /lemma-pod-bundle
# Copy skills from the builder stage explicitly instead of relying on the blanket repo copy.
COPY --from=builder --chown=appuser:appuser /app/lemma-skills /app/lemma-skills

# Copy backend application code
COPY --chown=appuser:appuser lemma-backend/ ./

# Precompile the application's own bytecode.
#
# `UV_COMPILE_BYTECODE=1` in the builder stage does this for dependencies, which
# is why site-packages ships .pyc — openai alone has 1,497 of them. The
# application's own 1,157 modules were copied in as source and never compiled,
# so every process start parsed and compiled all of them, and
# `PYTHONDONTWRITEBYTECODE=1` below then threw the result away. Measured at 3.2s
# per start inside a warm container, and the api's cold start spends 38.2s of
# its 52s before it logs a line at all.
#
# That cost was paid by every replica, on every deploy, every restart and every
# scale-up. `PYTHONDONTWRITEBYTECODE` only stops Python *writing* a cache; it
# still reads one that is already there, so the flag can stay.
#
# `unchecked-hash` because the image is immutable: Python trusts the .pyc
# without stat-ing its source, which saves a syscall per module and, more
# importantly, cannot be invalidated by whatever mtime the COPY layer left
# behind.
RUN python -m compileall -q --invalidation-mode unchecked-hash \
        /app/app /app/sandbox_runtime \
    && chown -R appuser:appuser /app/app /app/sandbox_runtime

# Browser SDK bundle served at /public/sdk/lemma-client.js for no-build apps.
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
CMD ["uvicorn", "app.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
