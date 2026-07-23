# AgentBox

AgentBox is Lemma's provider-neutral sandbox fabric for Docker, Kubernetes, and E2B.

**Status:** Implemented and verified for Docker and E2B; Kubernetes deferred

The canonical AgentBox design is maintained in:

- [AgentBox overview](../docs/design/agentbox/README.md)
- [Sandbox protocol](../docs/design/agentbox/sandbox-protocol.md)
- [Provider adapters](../docs/design/agentbox/provider-adapters.md)
- [Function execution](../docs/design/agentbox/function-execution.md)
- [Testing strategy](../docs/design/agentbox/testing-strategy.md)
- [Verification and rollout](../docs/design/agentbox/verification-and-rollout.md)

Those documents are the source of truth for the architecture and remaining acceptance
gates. The canonical implementation is the typed `/api` fabric in `agentbox/api/fabric.py`,
the SQLAlchemy state package, and the adapters under `agentbox/adapters/`.

## Runtime standards

- Python projects are locked and installed with `uv`. Runtime images do not invoke
  `pip`, and function invocation never resolves or installs dependencies.
- Node projects are locked and installed with `pnpm`. `npm` and `npx` are not part of
  the supported workspace toolchain.
- The workspace profile pins Python 3.14, Node 24 LTS, `uv` 0.11.31, and `pnpm`
  11.15.1 on every provider. The E2B template points its managed Code Interpreter
  service at the profile-owned Python 3.14 kernel, retains the base image's system
  Node for provider services, and exposes the pinned Node 24 runtime to agent login
  shells.
- The function profile contains a locked Python environment and no Node runtime,
  browser, package manager, or invocation-time installer.

## Required manager secrets

`AGENTBOX_API_KEY` authenticates backend calls. A separate
`AGENTBOX_RUNTIME_CREDENTIAL_KEY` of at least 32 bytes derives private,
per-allocation workspace-runtime credentials. The runtime key must be independently
generated and stable across AgentBox replicas and restarts; AgentBox does not derive
it from the API key or silently generate an ephemeral replacement.

The repository development and load-test launchers provide explicit non-production
values. Deployed environments must provide their own secret values.

## Verification

Ordinary tests do not spend provider resources:

```bash
cd agentbox
.venv/bin/pytest -q
```

Real Docker conformance is opt-in:

```bash
AGENTBOX_RUN_DOCKER_TESTS=1 \
  .venv/bin/pytest -q tests/adapters/test_docker_real.py
```

Real E2B conformance requires the API key plus exact immutable template and build IDs.
The suite creates uniquely scoped sandboxes and cleans up only exact provider IDs:

```bash
AGENTBOX_RUN_E2B_TESTS=1 \
AGENTBOX_E2B_WORKSPACE_TEMPLATE=<template-id> \
AGENTBOX_E2B_WORKSPACE_BUILD_ID=<build-id> \
AGENTBOX_E2B_FUNCTION_TEMPLATE=<template-id> \
AGENTBOX_E2B_FUNCTION_BUILD_ID=<build-id> \
  .venv/bin/pytest -q tests/adapters/test_e2b_real.py
```

The backend-owned full-path API/JOB table benchmark and its scheduled quality gates
are documented in the
[function execution benchmark runbook](../docs/operators/agentbox-function-benchmark.md).
