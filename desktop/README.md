# Lemma Desktop maintainer guide

Lemma Desktop is a thin Tauri shell over the durable `lemma-locald` control
plane. It supports hosted Lemma and a zero-toolchain local installation.

For the user journey, see [Install and run Lemma locally](../docs/installation.md).
For the process topology, lifecycle protocol, and port model, see the
[Desktop architecture](../docs/architecture/desktop.md).

## Shipped topology

The host runs:

- `lemma-desktop`;
- `lemma-locald`;
- one all-in-one Python backend;
- one Next.js frontend;
- `lemma-runtime` plus `lemma-vz` on macOS or WSL tooling on Windows.

The private guest runs PostgreSQL, Redis, SuperTokens, containerd, and the sandbox runtime
sandboxes. There is no user-facing Docker/Podman dependency and no Kreuzberg
container. PDF/document conversion runs in the backend.

Closing every window hides Desktop to the tray. The daemon and desired services
survive shell exit so schedules continue. **Quit Lemma** (⌘Q) performs a full
stop before exit, after naming what is running — closing the window is the way
to leave without stopping anything, so quitting does not need to be the other
one too.

Every exit route funnels through `RunEvent::ExitRequested`, including Dock →
Quit and the app's own `exit` after a confirmed stop. `Shell::quit_confirmed` is
what keeps that from re-arming the prompt against the exit it just authorised.

Because the stack outlives the shell, a relaunch is usually a reconcile rather
than a start. Desktop records the serving workspace and its runtime generation,
and a launch whose recorded workspace answers with that same generation opens it
directly — no splash, no navigation — and reconciles with the daemon on a
worker. Anything else falls back to the splash.

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
make desktop-test          # every crate in the desktop workspace
make desktop-test-app      # just the app crate, for a fast loop
make desktop-lint          # clippy, warnings are errors
swift build --package-path desktop/local-runtime/macos-vz
uv run --project lemma-backend pytest \
  lemma-backend/app/tests/unit/test_health_endpoints.py
npx tsc --noEmit --project lemma-frontend/tsconfig.json
```

`lemma-guestd` is the Linux guest daemon: it reaches for `std::os::unix`
unconditionally, so it builds and tests on macOS and Linux but not on Windows.
Its vsock listener is behind a Linux `cfg` that only a Linux build compiles,
which is why CI runs `make desktop-guestd` there as well.

The `cfg(windows)` branches — most of the runtime manager, locald's job objects
and named pipes, the Agent Host's npm shims — compile on no developer machine
here. The msvc target cannot be cross-compiled from macOS because
`libsqlite3-sys` needs a C toolchain, but the gnu target compiles the same
branches, which is enough to lint them:

```bash
brew install mingw-w64
cd desktop
# tauri-build resolves externalBin by target triple, so the app crate needs
# files under the Windows names before it will compile at all. Placeholders
# are enough for a lint; only bundling reads them.
for n in lemma-locald lemma-agent-host lemma-runtime; do
  cp "binaries/$n-aarch64-apple-darwin" "binaries/$n-x86_64-pc-windows-gnu.exe"
done
CC_x86_64_pc_windows_gnu=x86_64-w64-mingw32-gcc \
  CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER=x86_64-w64-mingw32-gcc \
  cargo clippy --workspace --exclude lemma-guestd \
    --all-targets --target x86_64-pc-windows-gnu -- -D warnings
rm -f binaries/*windows-gnu.exe
```

The **Windows desktop build check** job in CI is the real gate; this is how to
avoid learning about it from a red PR.

Build Desktop sidecars:

```bash
make desktop-sidecars
```

On Windows there is no `make`, so the same verbs live in a PowerShell
dispatcher over the same underlying scripts:

```powershell
pwsh desktop\scripts\desktop.ps1 help
pwsh desktop\scripts\desktop.ps1 test
pwsh desktop\scripts\desktop.ps1 exe
```

There is deliberately no `dev` verb: running the app from source is macOS only
for now. `desktop/local-runtime/manager` names its WSL distribution with a
global constant, so a dev run in a throwaway state root would adopt and mutate
the distribution a real install owns. On Windows, build and install the
installer instead.

### Signing a local build you intend to actually use

The sidecar script signs with a Developer ID when the machine has one and falls
back to ad-hoc otherwise, saying which it chose. Set `APPLE_SIGNING_IDENTITY` to
override it — including to `-` to force ad-hoc, which is what CI does for builds
that are deliberately untrusted.

Export the same identity when bundling, because the bundler re-signs the
sidecars it copies in:

```bash
export APPLE_SIGNING_IDENTITY="Developer ID Application: NAME (TEAMID)"
```

Without it an otherwise Developer ID build ends up with an ad-hoc daemon, and
that has a user-visible cost rather than just a Gatekeeper one. locald keeps the
operator's secrets in the OS credential vault, which ties each stored item to
the code identity of whoever created it. An ad-hoc designated requirement is a
bare `cdhash`, so every rebuild is a new program as far as the vault is
concerned and the user is asked to re-authorise access on the next launch. A
Developer ID requirement names `work.lemma.locald` and the team instead — the
identifier being fixed by the `Info.plist` that `desktop/locald/build.rs` links into the
binary — and survives rebuilds.

Verify with:

```bash
codesign -d -r- desktop/binaries/lemma-locald-aarch64-apple-darwin
```

A `designated => cdhash H"..."` line means the vault will re-prompt. A line
naming `identifier "work.lemma.locald"` means it will not.

To run Desktop local mode against the code you are editing:

```bash
desktop/scripts/dev-local.sh --source
```

locald supervises the backend out of `lemma-backend/` through `uv run` and the
frontend through `next dev`, so the workspace is your working tree rather than
the last release. The managed runtime, ports, health checks and restart policy
are the packaged ones — a dev run that exercised a different supervisor would
prove nothing about the real one.

It opens the workspace, as the packaged app does. Add `--control` when Local
settings is the thing you are working on.

It borrows the VM guest artifacts from an installed release (the one thing a
checkout cannot build on demand) read-only; override the location with
`LEMMA_DESKTOP_MANAGED_RUNTIME_ROOT`. To run a released pack instead, pass its
path:

```bash
desktop/scripts/dev-local.sh /absolute/path/to/local-runtime
```

Either way it rebuilds the native sidecars, gracefully replaces the prior
isolated dev daemon, and keeps its state under `/tmp/lemma-desktop-dev` so an
installed Lemma daemon cannot capture the dev UI — and so a dev session never
adopts or corrupts a real install's Agent Host identity.

For source-level installer testing, prepare an exact manifest and archives and
set:

```bash
export LEMMA_DESKTOP_RELEASE_MANIFEST=/absolute/path/lemma-local.json
export LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS=1
```

Only that explicitly selected manifest may use `file://` artifact sources.
Packaged releases ignore development port overrides and do not enable arbitrary
local artifacts.

## Build a test installer

CI's **Desktop workspace** and **Windows desktop build check** jobs do *not*
produce installers. They prove the app compiles, lints, tests, and bundles;
they build against a placeholder manifest with unresolvable URLs, so the
resulting app refuses to install and says so. Their artifacts are named
`lemma-desktop-macos-buildcheck-<sha>`.

For a build someone else can install, cut a **Release Local Images** run with
`share`:

```bash
gh workflow run release-local-images.yml -f version=0.7.0 -f publish=false -f share=true
```

That publishes the runtime archives and the manifest to a prerelease tagged
`desktop-nightly-<short-sha>`, then builds the **online** DMG against it —
signed with Developer ID, notarized and stapled — and attaches it there. The
download link is printed to the job summary. Prereleases never become "Latest",
so the version-tag release channel is untouched.

It has to be the online DMG. Apple's notary service unpacks `host-runtime.zip`
and rejects everything inside: a bundled CPython and `node_modules` are not
Developer ID signed and never will be. So a self-contained DMG cannot be
notarized, and an un-notarized one is refused by Gatekeeper on any machine that
did not build it. CI does not package self-contained apps at all — half a
gigabyte a run, for something nobody can hand to a tester.

Without `share`, a `publish: false` run still builds and verifies both
platforms' runtimes and uploads them as workflow artifacts, which is what the
local self-contained builds below consume.

### Or build one locally

The same run's artifacts also drive a local build, through the same staging
script CI uses — so a green local build and a green CI build mean the same
thing. The host pack and the guest runtime cannot be produced from a checkout
(the pack embeds digests of specific container builds; the guest is assembled
under `docker buildx` with a kernel unpacked by `zstd`), which is why this
fetches them:

```bash
make desktop-runtime-fetch RUN=<run-id>
make desktop-dmg
```

```powershell
pwsh desktop\scripts\desktop.ps1 runtime-fetch -Run <run-id>
pwsh desktop\scripts\desktop.ps1 exe
```

One run feeds both machines. The test app installs its embedded compressed
runtimes into Application Support on first launch; registry access is still
required for infrastructure and sandbox images.

## Clean macOS acceptance test

Use a disposable test machine where possible. For an intentionally destructive
local reset, first **Quit Lemma**, remove the test app, then remove
`~/Library/Application Support/Lemma`. Never make a release repair delete that
directory.

Acceptance flow:

1. Copy the PR app from the DMG to Applications; confirm the copy is not multi-GB.
2. Launch from Applications and choose Local.
3. Confirm download/extraction stages show real progress and no Start button.
4. Create a local account inside WKWebView; verify it remains authenticated.
   Confirm the marketing landing page never appears — not before signup, not
   after signing out, and not in a LAN browser (step 10).
5. Confirm the workspace does not return to the installer after Ready.
6. Walk local onboarding: it must ask for a provider, then agents on this
   computer, then who can reach this installation, in that order. Confirm the
   provider step states that the model is the installation's single default,
   that **Set this up later** advances without claiming success, and that
   completing a provider in Local settings advances the step on its own.
7. Open **Local settings** from the workspace footer, close it with Escape,
   reopen it from the tray, and confirm the underlying workspace state was not
   remounted or lost. It must look like the rest of the product: warm paper,
   violet primary action, no gold.
8. On the AI provider page, press **Ollama** and **LM Studio** to prefill a
   loopback endpoint, then **Connect and list models** and pick a default from
   the list — typing a model name must not be required. Apply it and verify
   thinking and structured tool calls. Also verify an API provider can replace
   them, and that a model the provider does not serve is refused.
9. From the onboarding agents step, and again from **Models**, press
   **Connect this computer** and confirm pairing needs no code and no terminal.
   Add a detected agent with **Add to chat models**, pick it in a chat, run a
   prompt, and approve a permission. Confirm the tray reads
   `Agent Host: connected`, that turning it off from either the tray or the card
   stops the process and survives an app restart, and that a full quit stops it
   without turning it off. A machine with no coding agents installed must say so
   in one line and still let the step continue. Repeat in hosted mode: no locald
   appears until the Agent Host is enabled, and no host pack is downloaded.
10. Enable **Local network** on a trusted Wi-Fi interface. Scan the QR code in a
    second browser, create/sign into an account, and verify streamed chat, a
    tool call, and a file transfer. Confirm that browser is offered the account
    portal rather than the landing page. Disable it and confirm the LAN port
    closes.
11. Verify ngrok preflight without exposing credentials. Activate a public link
    only after the open-signup confirmation, repeat streamed chat/file/webhook
    checks, then disable it. After `cloudflared tunnel login`, verify automatic
    setup creates one installation-owned named tunnel and DNS route, reuses it
    after disable, and still offers an existing tunnel as an advanced option.
    Quick Tunnels must not appear.
12. Run a sandbox operation that uses `lemma` CLI against the dynamic API.
13. Open a built React app at `*.apps.lemma.localhost`; while sharing, verify
    the UI honestly says published pod apps remain local-only.
14. Check the menu bar: **Settings…** on ⌘, opens Local settings, and no menu
    item names a service. The tray's first line must report the stack's real
    state, and everything operational must sit under **Troubleshoot**.
15. Close the window; verify schedules, the Agent Host, and active sharing
    remain available from the tray. Then press ⌘Q with sharing on, the Agent
    Host paired, and the stack up: the prompt must name all three, offer closing
    the window as the alternative, and say data stays on this Mac. Cancel, and
    confirm nothing stopped. Quit again and confirm it stops everything, takes
    its window off screen immediately rather than leaving a black one, and that
    Dock → Quit is asked in the same way. With the stack stopped, no Agent Host
    and no shared link, ⌘Q must exit without asking anything.
16. Restart and confirm ports and data persist, but LAN/Public mode does not
    resume automatically. The restart must reopen the pod you were last on with
    no installer splash; check **Diagnostics → Launch timing** and confirm the
    trace says `resume: hit` and reaches the window in well under a second.
17. Inspect every Diagnostics source and exercise runtime repair.
18. Quit and confirm the VM also releases its memory — `ps` must show no
    `lemma-vz`, and Activity Monitor no multi-GB helper, once the app is gone.

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
runtime/launch.log
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
