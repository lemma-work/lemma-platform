# Lemma Desktop (Tauri)

The Lemma macOS desktop app — a thin Tauri (Rust) shell around the Python
supervisor.

## Architecture

Thin Rust shell + Python supervisor. The shell owns native chrome (window,
splash, tray, menu, navigation policy) and the supervisor process lifecycle.
All orchestration intelligence lives in the supervisor
(`lemma-stack supervise`), which boots the local stack and reports structured
progress over a JSONL stdio protocol (documented in
`lemma-stack/lemma_stack/supervise.py`). The supervisor drives the same
`lemma-stack` install/start used everywhere else, so the desktop and the CLI
installer can never skew.

- `src/main.rs` — the entire shell (~715 lines)
- `ui/index.html` — splash screen with a small Tauri adapter for its preload
  API (`window.lemmaDesktop`)
- `capabilities/main.json` — IPC permissions for the splash window only;
  remote pages (the app itself) get no IPC access

The shell injects a read-only `window.__LEMMA_DESKTOP__` marker into the main
webview so the shared frontend can choose desktop-safe behavior without gaining
native IPC privileges. Same-origin new-window requests stay in Lemma; external
URLs open in the system browser.

Desktop login uses a short-lived PKCE-style handoff. The webview keeps a private
verifier, the system browser completes the normal Lemma login, and the webview
exchanges the verifier for a fresh cookie session. The `lemma://auth/complete`
deep link only focuses the app; no access or refresh token is placed in a URL.

Connection modes (persisted in `~/Library/Application Support/Lemma/desktop-config.json`):
- **undecided** (first launch): asks whether to connect to Lemma Cloud or run
  Lemma locally; local setup requires a second confirmation before installation
- **local**: spawns the supervisor, shows the splash with live
  startup phases, navigates to `http://localhost:3711` when ready
- **hosted**: loads the hosted app directly, no local services

## Development

```sh
cd desktop
cargo build
./target/debug/lemma-desktop                      # local mode against this checkout
LEMMA_SUPERVISE_DRY_RUN=1 ./target/debug/lemma-desktop   # splash demo, no services
LEMMA_DESKTOP_CONNECTION_MODE=hosted ./target/debug/lemma-desktop
```

Useful env overrides: `LEMMA_DESKTOP_RUNTIME_ROOT`,
`LEMMA_DESKTOP_HOSTED_URL`, `LEMMA_DESKTOP_LOCAL_URL`, `AGENTBOX_PROVIDER`.

The supervisor can be driven without the shell:

```sh
lemma-stack supervise --dry-run   # then type: {"cmd":"start"}
```

## Distribution pieces

- `scripts/build-sidecar.sh` — compiles `lemma-stack` into a single
  self-contained binary (`lemma-supervisor`) via PyInstaller from
  `lemma-stack/lemma_stack/sidecar_main.py`. The sidecar runs `lemma-stack
  supervise`, which pulls the released container images itself — no runtime
  checkout or tarball download is involved.
- `scripts/extract-concepts.mjs` — bakes the in-app education concept registry
  (`lemma-frontend/lib/education/concepts.ts`) into `ui/concepts.gen.json` for
  the splash tour.
- `scripts/stage-podman-runtime.mjs` — stages and ad-hoc-signs the macOS arm64
  Podman runtime (krunkit entitlements in `krunkit-entitlements.plist`).

A distribution build runs the sidecar build then bundles with
`tauri.dist.conf.json` (adds the sidecar as externalBin):

```sh
node desktop/scripts/extract-concepts.mjs
desktop/scripts/build-sidecar.sh
cd desktop && npx -y @tauri-apps/cli@latest build --config tauri.dist.conf.json
```

Supervisor resolution order in the shell: `LEMMA_DESKTOP_SUPERVISOR_BIN` →
bundled sidecar next to the app executable → `uv run --project lemma-stack
lemma-stack supervise` from a checkout (dev fallback).

## Status / still to do

- [x] Supervisor protocol (start/stop/restart/status, phases, provider auto-detection)
- [x] Shell: splash, event relay, tray, hide-to-tray, navigation allowlist, modes
- [x] First-run setup auto-detection (content hash over dep manifests/lockfiles,
      marker in `.local/lemma/setup-signature`; start auto-upgrades to setup)
- [x] Runtime payload download-on-demand (manifest URL → download, sha256
      verify, stage under Application Support, run from staged runtime)
- [x] Supervisor as compiled sidecar binary (PyInstaller bootstrap)
- [x] Single-instance enforcement, start-at-login tray toggle
- [x] Log In tray item (opens `<base>/auth/desktop`)
- [x] System-browser auth with one-time desktop session handoff and deep-link return
- [x] Desktop-aware same-origin/new-window navigation policy
- [x] Podman bundle as downloadable artifact (sidecar downloads it when no
      Docker is found)
- [x] Dependency bootstrap: supervisor installs uv/Node (nvm) user-locally
      during setup on machines without dev tools
- [x] Hardened-runtime entitlements wired in `tauri.dist.conf.json`; dist
      builds bake the artifacts URL via `LEMMA_DEFAULT_RUNTIME_MANIFEST_URL`
- [x] Signing + notarization: dist build signs, notarizes, and staples when
      `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`
      (app-specific password), and `APPLE_TEAM_ID` are set in the environment
- [ ] tauri-plugin-updater
- [ ] WKWebView compatibility pass over lemma-frontend (click-through started
      on a real local run; track issues as found)
- [ ] Dogfood, then cut a public release
