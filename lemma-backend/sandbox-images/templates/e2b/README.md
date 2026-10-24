# E2B templates

Sandbox provisioning uses three immutable E2B template builds:

- `lemma-workspace` extends E2B Code Interpreter with a locked authoring
  environment (including the Lemma SDK), Node 24, pnpm, uv, LiteParse, and
  headful Chrome. The shell, `python`, `python3`, `pip`, `pip3`, and E2B code
  contexts all use the locked Python 3.14 environment. Packages installed with
  plain `pip install` go into `~/.python`, so shell commands and persistent
  Python contexts see the same home-backed package set.
- `lemma-workspace-docker` is `lemma-workspace` unchanged, plus Docker Engine,
  Compose and Buildx, for machines that run a stack (a pod's QA box, a compose
  file under review). See [workspace-docker](#workspace-docker) below.
- `lemma-function` contains only the function runner and Lemma SDK.

The workspace templates build from E2B's `code-interpreter-v1`, which is
**Debian 13 (trixie)**. Anything added from a third-party apt repository has to
use that repository's Debian suite.

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
Google Chrome from Google's apt repository rather than Debian's Chromium (chosen
when the base was Ubuntu, where `chromium` is a snap that does not run in a
container; changing the browser now is a change of its own), and it is built for
amd64 only.

## workspace-docker

Docker's `linux/debian` apt repository (suite read from `/etc/os-release`):
`docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`,
`docker-compose-plugin` and `iptables`. `user` is in the `docker` group, so
plain `docker` and `docker compose` work without `sudo`. The group is
root-equivalent inside the VM, but `user` already has passwordless `sudo`
there, so the trust boundary is unchanged: it is the VM.

dockerd runs rootful on native overlayfs with cgroup v2; neither rootless Docker
nor fuse-overlayfs is needed. There is no start command. The guest boots
systemd and the package enables `docker.service`, so the daemon starts at boot
and again on every resume. The template is ready when `docker info` answers.
A filesystem-only pause cold-boots the VM on resume, so the provider's
post-resume step is:

```bash
docker info >/dev/null 2>&1 || sudo systemctl start docker
```

That waits out a slow boot rather than mistaking it for a failure. A pause keeps
`/var/lib/docker` -- images, volumes, build cache and stopped containers -- but it
is a power-off for the containers: those with `restart: unless-stopped` or
`always` come back on their own, the rest need `docker compose up` again.

**Disk is the binding limit.** The root filesystem is fixed at 25 GB by the E2B
plan; neither the SDK nor the build API can change it. About 19 GB is free on a
new sandbox. A machine running a compose stack and building images fills it, so
the backend's disk janitor prunes at `MACHINE_DISK_PRUNE_FRACTION` (75% by
default) -- finished runs first, then unused Docker images and build cache
(`docker image prune -af`, `docker builder prune -af`), then package caches --
and refuses new runs past `MACHINE_DISK_REFUSE_FRACTION` (90%) after pruning.

## Sizes

E2B fixes CPU and memory when a template is built, so each size a plan sells is
its own template build. `--size {cpu}x{memory_mb}` builds one target at that
size, and a size other than the target's default is named
`-{cpu}x{memory_gib}`:

| Target | Default size | Name at the default | Other sizes |
|---|---|---|---|
| `workspace` | 1 vCPU / 2048 MB | `lemma-workspace` | `--size 2x4096` → `lemma-workspace-2x4` |
| `workspace-docker` | 4 vCPU / 8192 MB | `lemma-workspace-docker` | `--size 8x16384` → `lemma-workspace-docker-8x16` |
| `function` | 1 vCPU / 2048 MB | `lemma-function` | |

The default sizes can be overridden with
`E2B_{WORKSPACE,WORKSPACE_DOCKER,FUNCTION}_{CPU_COUNT,MEMORY_MB}`; that keeps
the bare name. E2B's template build API does not expose a disk-size setting, and
function filesystems are treated as ephemeral because the provider allocation is
destroyed after the configured idle period.

The backend picks a template per machine size from JSON maps keyed
`{cpu}x{memory_mb}` -- the same spelling `--size` takes and the build prints as
`size_key`. A size with no entry is served from the single template setting:

```text
E2B_WORKSPACE_TEMPLATE         default lemma-workspace
E2B_WORKSPACE_SIZE_TEMPLATES   e.g. {"2x4096": "lemma-workspace-2x4"}
E2B_DOCKER_TEMPLATE            default lemma-workspace-docker
E2B_DOCKER_SIZE_TEMPLATES      e.g. {"8x16384": "lemma-workspace-docker-8x16"}
```

## Building

The returned template and build IDs must both be configured outside this source
repository. The deployment combines them as `<template_id>:<build_id>` so a
mutable tag can never change a running profile -- the workspace settings take a
single template string (`E2B_WORKSPACE_TEMPLATE`, `E2B_DOCKER_TEMPLATE`,
`E2B_FUNCTION_TEMPLATE`, and each value in the size maps), so whatever sets
them is what does the pinning.

Run it from `lemma-backend`; the builder copies from the monorepo root, which it
resolves from its own location.

```bash
cd lemma-backend
set -a
source .env
set +a
.venv/bin/python sandbox-images/templates/e2b/build_templates.py --target all
.venv/bin/python sandbox-images/templates/e2b/build_templates.py --target workspace --size 2x4096
.venv/bin/python sandbox-images/templates/e2b/build_templates.py --target workspace-docker --size 8x16384
```

`--target all` builds all three at their default sizes. A candidate build takes
`--name-suffix=-candidate` (with `=`, since the value starts with a dash), which
goes after the size: `lemma-workspace-docker-8x16-candidate`.

`test_e2b_docker_template_real.py` exercises a built `workspace-docker`
template end to end -- Docker as `user`, this repository's compose stack,
pause and resume, pruning -- given `E2B_API_KEY` and `E2B_DOCKER_TEMPLATE`.

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
