ARG PYTHON_IMAGE=python:3.14-slim
ARG UV_VERSION=0.11.31

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv-runtime
FROM ${PYTHON_IMAGE}

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    UV_PROJECT_ENVIRONMENT=/opt/agentbox \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/agentbox/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

COPY --from=uv-runtime /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl docker-cli \
    && rm -rf /var/lib/apt/lists/*

# Static podman remote client so the manager can drive a podman socket
# (CONTAINER_HOST=unix://...) the same way docker-cli drives docker.sock.
ARG PODMAN_REMOTE_VERSION=5.5.2
ARG TARGETARCH
RUN arch="${TARGETARCH:-$(dpkg --print-architecture)}" \
    && curl -fsSL "https://github.com/containers/podman/releases/download/v${PODMAN_REMOTE_VERSION}/podman-remote-static-linux_${arch}.tar.gz" \
        | tar -xz -C /tmp \
    && install -m 0755 "/tmp/bin/podman-remote-static-linux_${arch}" /usr/local/bin/podman \
    && rm -rf /tmp/bin

RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 -d /home/appuser -m appuser

COPY agentbox ./agentbox
COPY pyproject.toml ./
COPY uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
        --locked \
        --no-dev \
        --no-editable \
        --extra e2b \
        --extra postgres

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "agentbox.server:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
