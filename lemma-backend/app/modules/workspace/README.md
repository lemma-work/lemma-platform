# Workspace Sandboxes

Workspace execution uses the AgentBox sandbox manager for both local and cloud
Lemma. The main backend talks to one HTTP API; the manager chooses the provider
with `AGENTBOX_PROVIDER=docker|kubernetes`.

## Model

- A sandbox is an ephemeral execution envelope keyed by `sandbox_id`.
- A session is the per-workload execution context inside a sandbox. Session env
  carries request-scoped values such as `LEMMA_TOKEN`.
- Sandboxes are not file stores. File reads/writes needed by agent tools happen
  through shell/Python execution inside a session.
- The manager owns sandbox cleanup. The backend only asks for create, status,
  session/process execution, and delete.

## Backend Pieces

- `WorkspaceSandboxService`: high-level sandbox lifecycle and session factory.
- `AgentBoxSandbox`: manager-backed sandbox adapter.
- `AgentBoxWorkspaceSession`: typed manager client session for Python, shell,
  process stdin, and process termination.
- `WorkspaceFileManager`: agent tool helper that performs ephemeral file work by
  running commands inside a session.

## Configuration

Backend and sandbox manager:

```bash
AGENTBOX_API_URL=http://127.0.0.1:8721
AGENTBOX_API_KEY=dev-agentbox-key
```

Sandbox manager only:

```bash
AGENTBOX_PROVIDER=docker
AGENTBOX_RUNTIME_IMAGE=ghcr.io/lemma-work/agentbox-runtime:latest
AGENTBOX_RUNTIME_PORT=8080
AGENTBOX_STORAGE_ROOT=/tmp/agentbox-workspaces
```
