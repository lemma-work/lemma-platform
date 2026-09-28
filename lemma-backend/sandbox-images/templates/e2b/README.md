# E2B templates

Sandbox provisioning uses two immutable E2B template builds:

- `lemma-workspace` extends E2B Code Interpreter with a locked authoring
  environment (including the Lemma SDK), Node 24, pnpm, uv, LiteParse, and
  headful Chrome. The shell, `python`, `python3`, `pip`, `pip3`, and E2B code
  contexts all use the locked Python 3.14 environment. Packages installed with
  plain `pip install` go into `~/.python`, so shell commands and persistent
  Python contexts see the same home-backed package set.
- `lemma-function` contains only the function runner and Lemma SDK.

The workspace template has the same layout as `Dockerfile.workspace`: the
packages, the browser and the locked third-party Python closure, with Lemma's
own code on top as a floor -- the SDK and CLI, the same `sandbox_runtime` list
the runtime overlay carries, and the scripts under the names the overlay's
`bin/` gives them. The overlay the backend installs at session start supersedes
the floor on `sys.path` and, through `lemma-python.sh` and `set_envs`, on `PATH`,
so a Lemma code change never needs a new template. See
[the sandbox layout](../../../../docs/architecture/sandbox/README.md#one-layout-a-stable-image-a-floor-and-the-overlay).
`test_the_images_bake_the_overlay_floor` fails if the template and the
Dockerfile stop baking the same floor.

Rebuild and promote the template when what it installs changes -- a package, a
lockfile, the profile scripts -- not for a Lemma code change. The backend keeps
working against an older template: the scripts it runs are looked up on a
`PATH` that puts the overlay first, and fall back to the template's own copies.
Promoting a new template means new sandboxes for users, so treat it as the
exception it is.

Known differences from the Docker image, both deliberate: this template runs
Google Chrome from Google's apt repository rather than Debian's Chromium
(`chromium` on this Ubuntu base is a snap that does not run in a container),
and it is built for amd64 only.

Builds are created from the monorepo source. Both profiles default to 1 vCPU and
2 GB RAM; deployments may override the build resources with
`E2B_{WORKSPACE,FUNCTION}_{CPU_COUNT,MEMORY_MB}`. E2B's template build
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
E2B_WORKSPACE_TEMPLATE
E2B_WORKSPACE_BUILD_ID
E2B_FUNCTION_TEMPLATE
E2B_FUNCTION_BUILD_ID
```
