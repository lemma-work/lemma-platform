# AgentBox

AgentBox gives Lemma one authenticated sandbox API across Docker, Podman,
Kubernetes, E2B, and Daytona. Provider adapters own compute lifecycle and
endpoint discovery; the AgentBox runtime owns Python sessions, shell/TTY
processes, function execution, and browser/app proxying.

See [the architecture and operations guide](docs/architecture.md) for provider
capabilities, state backends, suspension semantics, networking, and production
configuration.

## Install

The core package includes Docker, Podman, and Kubernetes support. Install cloud
and durable-state adapters explicitly:

```bash
uv sync --all-extras
# or: uv pip install -e '.[e2b,daytona,postgres]'
```

## Local quick start

Build the runtime image, then start the manager with SQLite state and Docker:

```bash
cd agentbox
make build

AGENTBOX_PROVIDER=docker \
AGENTBOX_API_KEY=dev-agentbox-key \
AGENTBOX_API_URL=http://127.0.0.1:8711 \
AGENTBOX_RUNTIME_IMAGE=ghcr.io/lemma-work/lemma-agentbox-runtime:latest \
AGENTBOX_STATE_DB_PATH=/tmp/agentbox-state.db \
uv run uvicorn agentbox.server:app --host 127.0.0.1 --port 8711
```

Use the same `AGENTBOX_API_URL` and `AGENTBOX_API_KEY` in lemma-backend.

All manager endpoints except `/health` require
`X-API-Key: <AGENTBOX_API_KEY>`. The `Authorization` header is reserved for a
delegated Lemma bearer token on function-executor requests.

## API walkthrough

Ensure the logical user sandbox. Only durable, non-secret environment values
belong here:

```bash
curl -X PUT http://127.0.0.1:8711/sandboxes/user-123 \
  -H "X-API-Key: $AGENTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"env":{"LEMMA_BASE_URL":"https://api.lemma.work"}}'
```

Create a runtime session. Delegated tokens and other dynamic credentials belong
in session environment, not sandbox state:

```bash
curl -X PUT http://127.0.0.1:8711/sandboxes/user-123/sessions/task-a \
  -H "X-API-Key: $AGENTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "cwd":"/workspace/c/2026-07-15/example",
    "env":{"LEMMA_TOKEN":"...","LEMMA_POD_ID":"..."}
  }'
```

Run stateful Python in that session:

```bash
curl -X POST http://127.0.0.1:8711/sandboxes/user-123/sessions/task-a/python \
  -H "X-API-Key: $AGENTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"code":"x = 41\nx + 1","timeout_seconds":60}'
```

Start or poll a shell/TTY process:

```bash
curl -X POST http://127.0.0.1:8711/sandboxes/user-123/sessions/task-a/exec-command \
  -H "X-API-Key: $AGENTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"cmd":"python -m http.server 3000","yield_time_ms":1000,"timeout":300}'
```

Release idle compute while retaining the logical sandbox mapping:

```bash
curl -X POST http://127.0.0.1:8711/sandboxes/user-123/suspend \
  -H "X-API-Key: $AGENTBOX_API_KEY"
```

Permanent deletion is deliberately separate:

```bash
curl -X DELETE http://127.0.0.1:8711/sandboxes/user-123 \
  -H "X-API-Key: $AGENTBOX_API_KEY"
```

## Runtime image

`lemma-agentbox-runtime` includes Python, Node/npm/pnpm, Chromium, Agent Browser,
frontend tooling, the Lemma SDK/CLI, the runtime server, and the in-sandbox
function-executor service. Build and publish immutable tags from this directory:

```bash
make print-images
make build
make push TAG=<immutable-release-tag>
```

Set `AGENTBOX_RUNTIME_IMAGE` to that immutable image in Docker/Kubernetes. E2B
uses `E2B_SANDBOX_TEMPLATE`; Daytona uses exactly one of
`DAYTONA_SANDBOX_SNAPSHOT` or `DAYTONA_SANDBOX_IMAGE`.

`/workspace` is reserved for user projects and function working data. Browser
profiles, cookies, saved sessions, locks, and caches are compute-ephemeral under
`/tmp`; runtime startup also removes legacy browser state from `/workspace`.

## Verification

The default suite is hermetic and does not contact a sandbox provider:

```bash
uv run pytest -m "not e2e"
```

The real local-provider contract builds the current checkout for both Docker
and Podman, then verifies runtime sessions, concurrent API/JOB functions,
browser proxying, `/workspace/c/{date}/{slug}` continuity, suspend/resume, and
permanent deletion:

```bash
uv run pytest tests/e2e/test_local_providers_real_e2e.py -m e2e
```

The E2B contract requires credentials and a template containing the current
runtime code. Supply secrets through the environment, never command arguments:

```bash
E2B_API_KEY=... \
E2B_SANDBOX_TEMPLATE=... \
uv run pytest tests/e2e/test_e2b_real_e2e.py -m e2e
```

Real-provider tests create billable resources. Every resource receives a
test-specific logical ID and is permanently deleted in fixture cleanup, with a
provider-scoped E2B sweep as a failure backstop.
