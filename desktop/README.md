# Lemma Desktop

Lemma Desktop is the signed Tauri shell for Lemma Local. It offers either a
hosted connection or a zero-toolchain local install; users do not install
Docker, Podman, Homebrew, Python, Node.js, or a general-purpose VM manager.

## Runtime architecture

The desktop shell owns native windows, connection-mode selection, navigation,
and the privileged Control Center. It connects to the durable Rust
`lemma-locald` daemon through a mode-`0600` Unix socket on macOS or a
login-session-scoped named pipe on Windows. Remote application pages never
receive Tauri IPC capability.

Managed local mode has two long-lived Lemma application processes:

- one all-in-one Python backend containing API, worker, scheduler, AgentBox
  manager, surface receivers, and in-process document conversion;
- one Next.js frontend process, including local serving for built React apps.

Infrastructure is private to Lemma. macOS uses a lightweight
Virtualization.framework guest; Windows uses a private WSL2 distribution.
PostgreSQL and Redis are always-on guest services, SuperTokens is retained only
for the current compatibility mode, and sandbox containers are created on
demand. PostgreSQL also owns the separate `agentbox` database. There is no
Kreuzberg service and no Python/PyInstaller supervisor.

Windows startup is also app-owned. Normal startup performs a read-only
`wsl.exe --status` check. If WSL2 is unavailable, the local splash offers a
separate **Set up Windows runtime** action; only that explicit action opens UAC
and runs `wsl.exe --install --no-distribution --no-launch`. Lemma never installs
Ubuntu, changes the default distribution, or modifies global `.wslconfig`.
When Windows requires a reboot, a per-install resume marker makes the next app
launch continue automatically. The same operation is available as
`lemma-stack prepare` for terminal-led installs.

The local gateway exposes stable loopback-only origins:

- `http://app.lemma.localhost:3711` — workspace frontend;
- `http://api.lemma.localhost:8711` — API and auth;
- `http://<slug>.apps.lemma.localhost:8711` — built pod apps;
- `http://<sandbox>-<app>.workspaces.lemma.localhost:8711` — live sandbox apps.

Sandbox callbacks are explicit configuration. The backend passes
`WORKSPACE_CALLBACK_*` and `FUNCTION_RUNTIME_GATEWAY_URL` values through
unchanged and never infers or rewrites `localhost` to
`host.docker.internal`. Managed launch configuration sets the
sandbox-reachable `host.lemma.internal` bridge and every fresh sandbox must
reach the API health endpoint before it becomes ready.

## Control Center

The Control Center is a separate privileged local window with Overview, AI,
Integrations, Surfaces, Services, and Diagnostics sections. It can configure
OpenAI-compatible and Anthropic-compatible providers, discover models from a
provider's real `/models` endpoint, select a default model, and configure OAuth
applications and messaging surfaces.

Secrets are stored in the operating-system credential vault and are injected
only into the backend process. Snapshots and events expose presence flags, not
secret values. Configuration activation restarts only the backend, health
checks the new process, and rolls both configuration and vault state back if
activation fails; the frontend remains running.

## Online and offline installation

Every release publishes two signed and notarized packages per platform:

- **online** — the Tauri shell, `lemma-locald`, native runtime bridges, and a
  release manifest embedded in the signed app;
- **offline** — the same components plus the complete host and managed-guest
  runtime payloads.

The **online package is the recommended default**. The current macOS `.app` is
about 10 MiB installed and downloads the selected immutable runtime after the
user chooses Local mode. The offline package is intentionally large: the
current macOS bundle is about 3.0 GiB installed because it embeds a relocatable
Python backend, Node/Next frontend, and a 2 GiB sparse Linux appliance image.
That size is expected for the air-gapped artifact, not an accidental copy of
the source checkout. The current runtime downloads are about 307 MB for the
compressed host pack and 224 MB for the compressed guest pack; expanded
runtime plus writable data requires additional free space.

On first local launch, the online build downloads the exact host and guest ZIPs
named in its embedded manifest through the system proxy. Downloads are
resumable, HTTPS-only, bounded, and verified by exact size and SHA-256 before
safe staged extraction. The manifest release must equal the desktop release.
The installed cache also records both signed artifact identities, so a
same-version development build cannot silently reuse different host or guest
bits. Setup progress and errors are appended to a bounded private
`runtime/install.log` and **View log** reads that file even before the daemon
exists.
Activation occurs only after host markers, guest target markers, native
entrypoints, and archive layout pass validation. Incomplete downloads remain
available for Retry. The offline build starts from its bundled payload without
network access (cloud models and OAuth still require their own network access).

Every online launch requires the installed host/guest release to match the
signed desktop release exactly; an older complete pack is no longer accepted
merely because it exists. A newer desktop stages its matching immutable release
and retains the prior verified pack. Desktop configuration is activated through
a flushed atomic replacement on both macOS and Windows. Control Center can
quarantine and redownload a damaged current runtime without touching the
database disk, operator configuration, vault, or workspaces; if repair fails,
the original verified directory and configuration are restored. Schema-1 does
not declare database downgrade compatibility, so the UI deliberately withholds
manual rollback even though the prior pack is retained. Safe rollback becomes
available only with the manifest-v2 data boundary and pre-update snapshot.

Release artifacts are named `*-online.*` and `*-offline.*`. Windows host
runtime entrypoints are Authenticode-signed before the downloadable archive is
hashed. macOS apps and DMGs are Developer ID signed, notarized, and stapled.
The app ships only the virtualization entitlement required by the managed Mac
runtime; legacy JIT, unsigned-executable-memory, and disabled-library-validation
exceptions are absent.

## Development

```sh
node desktop/scripts/extract-concepts.mjs
desktop/scripts/build-sidecar.sh
cd desktop
cargo test --all-targets
cargo clippy --all-targets -- -D warnings
cargo run
```

Useful development overrides:

- `LEMMA_DESKTOP_CONNECTION_MODE=hosted|local`
- `LEMMA_DESKTOP_RUNTIME_ROOT=/path/to/lemma-platform`
- `LEMMA_DESKTOP_HOST_PACK_ROOT=/path/to/local-runtime`
- `LEMMA_DESKTOP_MANAGED_RUNTIME_ROOT=/path/to/managed-runtime`
- `LEMMA_DESKTOP_RELEASE_MANIFEST=/path/to/lemma-local.json`
- `LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS=1` — permits `file://` ZIPs only when
  `LEMMA_DESKTOP_RELEASE_MANIFEST` points to that exact developer manifest
- `LEMMA_DESKTOP_LOCALD_BIN=/path/to/lemma-locald`
- `LEMMA_DESKTOP_HOSTED_URL=...` and `LEMMA_DESKTOP_LOCAL_URL=...`

The manifest and runtime-root overrides are explicit development/enterprise
controls; no backend hostname mutation is tied to them.

### Test an unreleased branch end to end

The normal CI workflow uploads an ad-hoc-signed macOS online DMG named
`lemma-desktop-macos-<github.sha>`. On a pull request, `github.sha` is the
temporary PR merge commit rather than the branch head, so download the DMG by
artifact pattern as shown below. The Release Local Stack Images workflow can
also be manually dispatched on the same branch with `publish=false`. In that
mode it:

- pushes workspace/function and application images under
  `test-<12-character-commit>` rather than changing release or `latest` tags;
- builds both native host packs and managed guest runtimes;
- uploads `lemma-local-test-<commit>` for 14 days;
- does not create or modify a GitHub Release.

Branch-test Windows host packs are intentionally unsigned because pull requests
cannot access release signing credentials. Published Windows runtimes still
require Authenticode signing and timestamp verification.

For the current `0.6.2` desktop:

```sh
branch=codex/local-desktop-redesign
sha="$(git rev-parse HEAD)"

gh workflow run release-local-images.yml \
  --ref "${branch}" \
  -f version=0.6.2 \
  -f publish=false

# After the two workflows are green, substitute their run IDs.
gh run download <runtime-run-id> \
  -n "lemma-local-test-${sha}" \
  -D /tmp/lemma-pr/runtime
gh run download <ci-run-id> \
  --pattern "lemma-desktop-macos-*" \
  -D /tmp/lemma-pr/desktop

python scripts/prepare_desktop_test_runtime.py \
  --artifacts-dir /tmp/lemma-pr/runtime
```

Open the downloaded DMG, copy Lemma to Applications, quit any older Lemma
process, and launch its executable with the localized manifest:

```sh
LEMMA_DESKTOP_CONNECTION_MODE=local \
LEMMA_DESKTOP_RELEASE_MANIFEST=/tmp/lemma-pr/runtime/lemma-local.local.json \
LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS=1 \
/Applications/Lemma.app/Contents/MacOS/lemma-desktop
```

The file override does not weaken normal packages: it requires both explicit
environment variables, accepts only absolute hostless `file://` URLs, and
still enforces the workflow-recorded size, SHA-256, ZIP layout, extracted-size,
and release-version checks. For a source-built runtime, the direct
`LEMMA_DESKTOP_HOST_PACK_ROOT` and `LEMMA_DESKTOP_MANAGED_RUNTIME_ROOT`
overrides remain faster.

Build a small online package with `tauri.online.conf.json`, or a complete
offline package after staging `runtime/local-runtime` and
`runtime/managed-runtime` with `tauri.dist.conf.json`:

```sh
cd desktop
npx -y @tauri-apps/cli@2.11.4 build --config tauri.online.conf.json
npx -y @tauri-apps/cli@2.11.4 build --config tauri.dist.conf.json
```

The release workflow requires Apple Developer ID/notarization credentials or a
Windows code-signing PFX plus RFC 3161 timestamp URL. Missing credentials stop
the build before public artifacts are uploaded.
