ARG PYTHON_IMAGE=python:3.14.6-slim
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
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 -d /home/appuser -m appuser

COPY agentbox/agentbox ./agentbox
COPY agentbox/pyproject.toml ./
COPY agentbox/uv.lock ./
COPY agentbox-client /agentbox-client
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=lemma-python,target=/lemma-python,readonly \
    uv sync \
        --locked \
        --no-dev \
        --no-editable \
        --extra e2b \
        --extra postgres

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "agentbox.server:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
