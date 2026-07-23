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

The local gateway exposes stable loopback-only origins:

- `http://app.lemma.localhost:3711` — workspace frontend;
- `http://api.lemma.localhost:8711` — API and auth;
- `http://<slug>.apps.lemma.localhost:8711` — built pod apps;
- `http://<sandbox>-<app>.workspaces.lemma.localhost:8711` — live sandbox apps.

Sandbox callbacks are explicit configuration. The backend passes
`WORKSPACE_CALLBACK_*` values through unchanged and never infers or rewrites
`localhost` to `host.docker.internal`. Managed launch configuration sets the
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

On first local launch, the online build downloads the exact host and guest ZIPs
named in its embedded manifest through the system proxy. Downloads are
resumable, HTTPS-only, bounded, and verified by exact size and SHA-256 before
safe staged extraction. The manifest release must equal the desktop release.
Activation occurs only after host markers, guest target markers, native
entrypoints, and archive layout pass validation. Incomplete downloads remain
available for Retry. The offline build starts from its bundled payload without
network access (cloud models and OAuth still require their own network access).

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
- `LEMMA_DESKTOP_LOCALD_BIN=/path/to/lemma-locald`
- `LEMMA_DESKTOP_HOSTED_URL=...` and `LEMMA_DESKTOP_LOCAL_URL=...`

The manifest and runtime-root overrides are explicit development/enterprise
controls; no backend hostname mutation is tied to them.

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
