ARG PYTHON_IMAGE=python:3.14.4-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

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
RUN pip install --no-cache-dir .

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "agentbox.server:app", "--host", "0.0.0.0", "--port", "8000"]
