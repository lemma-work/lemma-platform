# E2B templates

Sandbox provisioning uses two immutable E2B template builds:

- `lemma-agentbox-workspace` extends E2B Code Interpreter with a locked authoring
  environment (including the Lemma SDK), Node 24, pnpm, uv, LiteParse, and
  headful Chrome. The shell, `python`, `python3`, `pip`, `pip3`, and E2B code
  contexts all use the locked Python 3.14 environment. Packages installed with
  plain `pip install` go into `/workspace/.python`, so shell commands and
  persistent Python contexts see the same workspace-backed package set.
- `lemma-agentbox-function` contains only the function runner and Lemma SDK.

Builds are created from the monorepo source. Both profiles default to 1 vCPU and
2 GB RAM; deployments may override the build resources with
`AGENTBOX_E2B_{WORKSPACE,FUNCTION}_{CPU_COUNT,MEMORY_MB}`. E2B's template build
API does not expose a disk-size setting, and function filesystems are treated as
ephemeral because the provider allocation is destroyed after the configured idle
period.

The returned template and build IDs must both be configured outside this source
repository. The deployment combines them as `<template_id>:<build_id>` so a
mutable tag can never change a running profile -- the workspace settings take a
single template string (`E2B_WORKSPACE_TEMPLATE` / `E2B_FUNCTION_TEMPLATE`), so
whatever sets them is what does the pinning.

Run it from `lemma-backend`; the builder copies from the monorepo root, which it
resolves from its own location.

```bash
cd lemma-backend
set -a
source .env
set +a
.venv/bin/python sandbox-images/templates/e2b/build_templates.py --target all
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
