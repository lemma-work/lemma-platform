# AgentBox

AgentBox is Lemma's sandbox manager. It exposes one typed HTTP API for creating
ephemeral code sandboxes, creating sessions inside them, and running Python,
shell, or long-lived process commands. The same manager API is used locally and
in cloud.

## Architecture

```text
lemma-backend
  |
  | HTTP + API key
  v
AgentBox Manager
  |
  +-- AGENTBOX_PROVIDER=docker       -> local Docker containers
  |
  +-- AGENTBOX_PROVIDER=podman       -> managed local Podman containers
  |
  +-- AGENTBOX_PROVIDER=kubernetes   -> cloud Kubernetes pods
        |
        v
agentbox runtime container
  |
  v
runtime HTTP server on port 8080
```

The main backend never talks to Docker or Kubernetes directly. Provider-specific
logic lives under `agentbox/providers`.

## Runtime Image

There is one runtime image: `agentbox-runtime`. It is the development-capable
image and includes Python, Node/npm/pnpm, Chromium, Agent Browser, frontend
tooling, the Lemma Python SDK/CLI, and the AgentBox runtime server.

The API accepts an optional exact `image` on sandbox create/update. If omitted,
the provider uses `AGENTBOX_RUNTIME_IMAGE`.

## Sandbox Model

- A sandbox is an ephemeral container/pod boundary.
- A session is a per-workload context inside the sandbox. Use session env for
  request-scoped values such as `LEMMA_TOKEN`.
- Sandbox files are not a product API. If a caller needs to read or write files,
  it should do so through command execution in a session.
- The manager stores only lightweight lifecycle state needed for cleanup and
  recreating an active sandbox.

## Configuration

Manager:

```bash
AGENTBOX_PROVIDER=docker              # docker, podman, or kubernetes
AGENTBOX_API_KEY=dev-agentbox-key
AGENTBOX_API_URL=http://127.0.0.1:8721
AGENTBOX_RUNTIME_IMAGE=ghcr.io/lemma-work/agentbox-runtime:latest
AGENTBOX_RUNTIME_PORT=8080
AGENTBOX_STATE_DB_PATH=/data/agentbox-manager/state.db
AGENTBOX_SESSION_IDLE_TIMEOUT_SECONDS=300
AGENTBOX_SANDBOX_IDLE_TIMEOUT_SECONDS=300
```

Local container provider:

```bash
AGENTBOX_STORAGE_ROOT=/tmp/agentbox-workspaces
AGENTBOX_STORAGE_HOST_ROOT=
AGENTBOX_ENDPOINT_HOST=127.0.0.1
AGENTBOX_PLATFORM=linux/arm64/v8
AGENTBOX_MEMORY_LIMIT=2g
AGENTBOX_CPU_LIMIT=1
```

Kubernetes provider:

```bash
AGENTBOX_NAMESPACE=agentbox
AGENTBOX_RUNTIME_CLASS_NAME=gvisor
AGENTBOX_NODE_SELECTOR_POOL=sandbox
AGENTBOX_SANDBOX_CPU_REQUEST=250m
AGENTBOX_SANDBOX_CPU_LIMIT=1000m
AGENTBOX_SANDBOX_MEMORY_REQUEST=500Mi
AGENTBOX_SANDBOX_MEMORY_LIMIT=2Gi
```

## HTTP API

All endpoints except `/health` require either:

- `Authorization: Bearer <AGENTBOX_API_KEY>`
- `X-API-Key: <AGENTBOX_API_KEY>`

Create a sandbox:

```bash
curl -X POST http://127.0.0.1:8711/sandboxes/user-123 \
  -H "Authorization: Bearer $AGENTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "disk_size_gb": 1,
    "wait_ready": true
  }'
```

Create a session:

```bash
curl -X POST http://127.0.0.1:8711/sandboxes/user-123/sessions/task-a \
  -H "Authorization: Bearer $AGENTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "cwd": "/workspace",
    "env": {
      "LEMMA_TOKEN": "...",
      "LEMMA_BASE_URL": "https://api.lemma.work"
    }
  }'
```

Run Python in a session:

```bash
curl -X POST http://127.0.0.1:8711/sandboxes/user-123/exec \
  -H "Authorization: Bearer $AGENTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "task-a",
    "code": "x = 41\nx + 1",
    "timeout_seconds": 60
  }'
```

Run a shell command in a session:

```bash
curl -X POST http://127.0.0.1:8711/sandboxes/user-123/exec \
  -H "Authorization: Bearer $AGENTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "task-a",
    "command": ["sh", "-lc", "lemma --help && pwd"],
    "cwd": "/workspace",
    "timeout_seconds": 60
  }'
```

Run a long-lived process:

```bash
curl -X POST http://127.0.0.1:8711/sandboxes/user-123/sessions/task-a/exec-command \
  -H "Authorization: Bearer $AGENTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "cmd": "python -m http.server 3000",
    "yield_time_ms": 1000,
    "timeout": 300
  }'
```

Write stdin or poll a process:

```bash
curl -X POST http://127.0.0.1:8711/sandboxes/user-123/sessions/task-a/write-stdin \
  -H "Authorization: Bearer $AGENTBOX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "process_id": "proc-...",
    "chars": "hello\n",
    "yield_time_ms": 1000
  }'
```

Delete the sandbox:

```bash
curl -X DELETE http://127.0.0.1:8711/sandboxes/user-123 \
  -H "Authorization: Bearer $AGENTBOX_API_KEY"
```

## Local Docker Run

Start the manager with the Docker provider:

```bash
cd agentbox
AGENTBOX_PROVIDER=docker \
AGENTBOX_API_KEY=dev-agentbox-key \
AGENTBOX_API_URL=http://127.0.0.1:8711 \
AGENTBOX_RUNTIME_IMAGE=ghcr.io/lemma-work/agentbox-runtime:latest \
AGENTBOX_STATE_DB_PATH=/tmp/agentbox-state.db \
uv run uvicorn agentbox.server:app --host 127.0.0.1 --port 8711
```

Use the same manager settings for the main backend:

```bash
AGENTBOX_API_URL=http://127.0.0.1:8711
AGENTBOX_API_KEY=dev-agentbox-key
```

For managed local Podman execution only, set:

```bash
AGENTBOX_PROVIDER=podman
LOCAL_PODMAN_MACHINE_NAME=lemma-runtime
```

## Build And Push Images

Use the Makefile from this directory:

```bash
cd agentbox
make print-images
make build
make push TAG=2026-05-30-single-runtime
```

Published images:

```text
agentbox-manager:<tag>
agentbox-runtime:<tag>
```

Set the runtime image in the manager environment:

```bash
AGENTBOX_RUNTIME_IMAGE=ghcr.io/lemma-work/agentbox-runtime:<tag>
```
