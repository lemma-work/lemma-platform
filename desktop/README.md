# Lemma Desktop maintainer guide

Lemma Desktop is a thin Tauri shell over the durable `lemma-locald` control
plane. It supports hosted Lemma and a zero-toolchain local installation.

For the user journey, see [Install and run Lemma locally](../docs/installation.md).
For requirements and architecture, see the
[product specification](../docs/design/local-desktop-product-spec.md) and
[technical design](../docs/design/local-desktop-technical-design.md).

## Shipped topology

The host runs:

- `lemma-desktop`;
- `lemma-locald`;
- one all-in-one Python backend;
- one Next.js frontend;
- `lemma-runtime` plus `lemma-vz` on macOS or WSL tooling on Windows.

The private guest runs PostgreSQL, Redis, SuperTokens, containerd, and AgentBox
sandboxes. There is no user-facing Docker/Podman dependency and no Kreuzberg
container. PDF/document conversion runs in the backend.

Closing every window hides Desktop to the tray. The daemon and desired services
survive shell exit so schedules continue. **Quit and stop Lemma** performs a
full stop before exit.

## Runtime packaging

Public release apps bundle only `lemma-local.json` and native control helpers.
The application must remain at or below 25 MiB installed. First launch
downloads:

- `lemma-host-pack-<target>.zip`;
- `lemma-guest-runtime-<target>.zip`.

Every manifest entry contains source URL/resource, SHA-256, compressed size,
expanded size, archive format, platform target, and release identity. Archives
are resumable, verified while transferring, extracted into disposable staging,
validated, and atomically activated.

The PR test DMG embeds the two compressed archives and rewrites only their
manifest sources to trusted resource names. It must not contain expanded
`local-runtime` or `managed-runtime` directories.

Current hard gates:

- host plus guest compressed: 750 MiB;
- PR bundled application: 850 MiB;
- expanded immutable runtime: 2.25 GiB;
- macOS root disk: 1.25 GiB;
- public application: 25 MiB.

OCI infrastructure/sandbox images are not included. Public offline claims and
offline release artifacts are intentionally removed.

## Build and test locally

Prerequisites for maintainers are Rust, Node.js 22, Swift/Xcode on macOS,
Python/uv, and the repository’s normal build toolchain.

Run focused validation:

```bash
cargo test --manifest-path desktop/Cargo.toml --locked
cargo test --manifest-path locald/Cargo.toml --locked
cargo test --manifest-path local-runtime/manager/Cargo.toml --locked
cargo test --manifest-path local-runtime/guestd/Cargo.toml --locked
swift build --package-path local-runtime/macos-vz
uv run --project lemma-backend pytest \
  lemma-backend/app/tests/unit/test_health_endpoints.py
npx tsc --noEmit --project lemma-frontend/tsconfig.json
```

Build Desktop sidecars:

```bash
desktop/scripts/build-sidecar.sh
```

To iterate on Local settings without building a DMG every time, use the
isolated dev launcher:

```bash
desktop/scripts/dev-local.sh /absolute/path/to/local-runtime
```

It rebuilds the native sidecars, gracefully replaces the prior isolated dev
daemon, and keeps its state under `/tmp/lemma-desktop-dev` so an installed
Lemma daemon cannot capture the dev UI.

For source-level installer testing, prepare an exact manifest and archives and
set:

```bash
export LEMMA_DESKTOP_RELEASE_MANIFEST=/absolute/path/lemma-local.json
export LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS=1
```

Only that explicitly selected manifest may use `file://` artifact sources.
Packaged releases ignore development port overrides and do not enable arbitrary
local artifacts.

## Build the PR test DMG

Run the **Release Local Images** workflow on the PR branch with:

- `version`: the Desktop version, currently `0.6.2`;
- `publish`: `false`.

The workflow builds and verifies both runtimes, creates a resource-backed
manifest, builds an ad-hoc-signed DMG, enforces size gates, and uploads:

```text
lemma-desktop-macos-pr-test-<full-commit-sha>
```

Download with GitHub CLI:

```bash
sha="$(git rev-parse HEAD)"
gh run list --workflow release-local-images.yml --branch "$(git branch --show-current)"
gh run download RUN_ID \
  -n "lemma-desktop-macos-pr-test-${sha}" \
  -D /tmp/lemma-pr-dmg
```

The test app installs its embedded compressed runtimes into Application
Support on first launch. Registry access remains required for infrastructure
and AgentBox images.

## Clean macOS acceptance test

Use a disposable test machine where possible. For an intentionally destructive
local reset, first select **Quit and stop Lemma**, remove the test app, then
remove `~/Library/Application Support/Lemma`. Never make a release repair
delete that directory.

Acceptance flow:

1. Copy the PR app from the DMG to Applications; confirm the copy is not multi-GB.
2. Launch from Applications and choose Local.
3. Confirm download/extraction stages show real progress and no Start button.
4. Create a local account inside WKWebView; verify it remains authenticated.
5. Confirm the workspace does not return to the installer after Ready.
6. Open **Local settings** from the workspace footer, close it with Escape,
   reopen it from the tray, and confirm the underlying workspace state was not
   remounted or lost.
7. On the AI provider page, use **Use Ollama** and **Use LM Studio** to
   prefill a loopback endpoint, apply it, and verify model discovery, thinking,
   and structured tool calls. Also verify an API provider can replace them.
8. Enable **Local network** on a trusted Wi-Fi interface. Scan the QR code in a
   second browser, create/sign into an account, and verify streamed chat, a
   tool call, and a file transfer. Disable it and confirm the LAN port closes.
9. Verify ngrok preflight without exposing credentials. Activate a public link
   only after the open-signup confirmation, repeat streamed chat/file/webhook
   checks, then disable it. After `cloudflared tunnel login`, verify automatic
   setup creates one installation-owned named tunnel and DNS route, reuses it
   after disable, and still offers an existing tunnel as an advanced option.
   Quick Tunnels must not appear.
10. Run an AgentBox operation that uses `lemma` CLI against the dynamic API.
11. Open a built React app at `*.apps.lemma.localhost`; while sharing, verify
    the UI honestly says published pod apps remain local-only.
12. Close the window; verify schedules and active sharing remain available
    from the tray. Full Quit must stop sharing before exiting.
13. Restart and confirm ports and data persist, but LAN/Public mode does not
    resume automatically.
14. Inspect every Diagnostics source and exercise runtime repair.
15. Use **Quit and stop Lemma** and confirm the VM also releases its memory.

Also test with blocked Hugging Face access, a failed OCI registry/DNS request,
and unrelated listeners occupying persisted ports.

## Runtime state and debugging

macOS state root:

```text
~/Library/Application Support/Lemma
```

Key files:

```text
desktop-config.json
runtime/install.log
runtime/releases/<version>/
locald/network.json
locald/installation.id
locald/processes.json
locald/events.jsonl
locald/logs/
locald/runtime/macos/data.raw
locald/runtime/macos/console.log
```

Set `LEMMA_DESKTOP_DEVTOOLS=1` for the WKWebView inspector and
`LEMMA_DESKTOP_DEBUG=1` for protocol event output. The in-app Diagnostics view
is the preferred user-facing path; it returns bounded redacted data.

Development-only dynamic-port overrides require both variables:

```bash
export LEMMA_LOCALD_FRONTEND_PORT=49180
export LEMMA_LOCALD_BACKEND_PORT=49181
```

Packaged release builds ignore them.

## Release policy

`release-desktop.yml` publishes only signed/notarized online macOS and Windows
installers. `release-local-images.yml` publishes immutable host/guest runtimes
and the release manifest. The release gate requires the platform E2Es, size
breakdown, signatures, and runtime integrity checks.

Do not reintroduce:

- expanded runtimes in the public app;
- public offline/air-gapped claims;
- hardcoded managed ports;
- default localhost rewriting in the backend;
- Podman/Docker/Kreuzberg requirements in the Desktop journey;
- service health that accepts non-2xx or the wrong runtime generation.
