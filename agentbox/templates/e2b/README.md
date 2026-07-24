# E2B templates

AgentBox uses two immutable E2B template builds:

- `lemma-agentbox-workspace` extends E2B Code Interpreter with a locked authoring
  environment (including the Lemma SDK), Node 24, pnpm, uv, LiteParse, and
  headful Chrome. E2B currently owns the Code Interpreter kernel ABI (Python
  3.13); the template installs its locked environment for that interpreter and
  exposes it through a system `.pth` file. Do not attempt to replace E2B's
  managed kernel through the Jupyter kernel spec: `run_code` does not use that
  file.
- `lemma-agentbox-function` contains only the function runner and Lemma SDK.

Builds are created from the monorepo source. Both profiles default to 1 vCPU and
2 GB RAM; deployments may override the build resources with
`AGENTBOX_E2B_{WORKSPACE,FUNCTION}_{CPU_COUNT,MEMORY_MB}`. E2B's template build
API does not expose a disk-size setting, and function filesystems are treated as
ephemeral because the provider allocation is destroyed after the configured idle
period.

The returned template and build IDs must both be configured outside this source
repository. AgentBox combines them as
`<template_id>:<build_id>` so a mutable tag can never change a running profile.

```bash
cd agentbox
set -a
source ../lemma-backend/.env
set +a
.venv/bin/python templates/e2b/build_templates.py --target all
```

The script prints identifiers and effective resource values only. It does not
write credentials or modify an environment file. After both real provider
conformance suites and the backend API/JOB benchmark pass, promote the immutable
IDs in deployment configuration. A new build is not promoted merely because
template publication succeeded.

Python environments are resolved with `uv --locked`; Node dependencies are resolved
with `pnpm --frozen-lockfile`. Template publication must fail rather than update a
lock file or select a mutable package version.

The workspace installs Chrome's required shared libraries explicitly with Debian's
`--no-install-recommends` policy. In the July 2026 build this layer added about 95 MB,
roughly 9 MB less than `agent-browser install --with-deps`, while the full live
headful-browser conformance test still passed.

Do not configure a template name alone. Runtime profiles require both the template
ID and its exact build ID through:

```text
AGENTBOX_E2B_WORKSPACE_TEMPLATE
AGENTBOX_E2B_WORKSPACE_BUILD_ID
AGENTBOX_E2B_FUNCTION_TEMPLATE
AGENTBOX_E2B_FUNCTION_BUILD_ID
```
