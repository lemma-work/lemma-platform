# Install and run Lemma locally

Lemma Desktop is the supported local installation. It installs and operates
Lemma without asking the user to install Docker, Podman, Homebrew, Python,
Node.js, PostgreSQL, Redis, or a general-purpose VM manager.

The signed application is a small online installer. On first local launch it
downloads the exact host and private-guest runtime for that Desktop version,
verifies their SHA-256 digests, extracts them into app-owned storage, and then
starts Lemma. Infrastructure and sandbox OCI images are also downloaded when
first needed, so this release is not an air-gapped installer.

## Supported systems

| Platform | Minimum | Architecture | Private runtime |
| --- | --- | --- | --- |
| macOS | macOS 14 | Apple silicon | Apple Virtualization.framework |
| Windows | Windows 11 23H2 | x86-64 | Private WSL2 distribution |

Intel Macs, Windows on Arm, and Desktop Linux are not release targets yet,
and Windows on x86-64 is experimental rather than published — see below.

Allow space for both downloaded archives and the expanded runtime shown during
setup, plus 4 GiB of working headroom. The immutable host and guest runtimes are
gated at 8 GiB combined; user databases, files, images, and workspace sandboxes grow
separately.

## macOS installation

1. Open the [latest Lemma release](https://github.com/lemma-work/lemma-platform/releases/latest).
2. Download `Lemma_<version>_aarch64-online.dmg`.
3. Open the DMG and drag **Lemma** into **Applications**.
4. Eject the DMG and open `/Applications/Lemma.app`.
5. Choose **Use Local Lemma** and select **Install local services**.

macOS may ask for Local Network access to let Lemma reach its private virtual
machine on this Mac. If setup cannot connect to local services, check Lemma in
**System Settings → Privacy & Security → Local Network**, then return to Lemma
and select **Try again**. A connection failure does not require deleting local
data. If access is already allowed, restart Lemma and check VPN or firewall rules.

Run Lemma from Applications, not from the mounted DMG. The public application
contains only the Desktop shell, `lemma-locald`, native runtime helpers, and
runtime metadata; CI rejects an installed public app larger than 25 MiB.

## Windows installation (experimental)

Windows is not a published platform yet. The installer is built and signed on
every release, but it is kept as a workflow artifact rather than attached to
the release, because attaching it would be an offer of support we cannot make
until the Windows paths have been tested end to end.

To try it:

1. Open the most recent **Release Lemma Desktop** run in
   [Actions](https://github.com/lemma-work/lemma-platform/actions/workflows/release-desktop.yml).
2. Download the `lemma-desktop-windows-<version>` artifact and unzip it.
3. Run the signed installer and open Lemma.
4. Choose **Use Local Lemma** and select **Install local services**.

Lemma imports a private `LemmaRuntime` WSL2 distribution. It does not install
Ubuntu, Docker Desktop, or Podman, and it does not change the default WSL
distribution. If WSL2 features are unavailable, **Set up Windows runtime**
requests elevation explicitly. Restart Windows if requested, then reopen
Lemma; setup resumes.

## First start and account creation

Setup reports the real stage being performed:

1. Resolve and validate runtime metadata.
2. Download the host runtime.
3. Download the private guest runtime.
4. Verify and extract both archives.
5. Start the private runtime.
6. Prepare infrastructure images.
7. Start PostgreSQL and its `lemma` and `lemma_datastore` databases.
8. Start Redis.
9. Start SuperTokens.
10. Run migrations and start the all-in-one backend.
11. Start the frontend.
12. Stabilize both processes and open the workspace.

Downloads show measured bytes, throughput, and ETA when available. Opaque work
shows its stage without invented byte counts. Interrupted downloads resume,
verified archives are reused, and failed staging directories are never
activated.

Select **Create account** after Lemma reports Ready. Local signup stays inside
the Desktop window. Local-only configuration disables email verification and
internet-facing auth throttles; SMTP is not required. Hosted Lemma sign-in
continues to use the system browser.

The local application consists of:

- one all-in-one Python backend for API, workers, schedules, the sandbox runtime
  management, surfaces, and document conversion;
- one Next.js frontend process;
- one private Linux runtime containing PostgreSQL, Redis, SuperTokens, and
  isolated sandbox containers.

Embedding-model initialization runs in the background and is non-fatal.
Signup, files, tables, settings, and normal workspace access do not wait for
Hugging Face. Semantic operations report a temporary capability error if the
model is still preparing.

## Configure an AI provider

If no validated provider exists, authenticated local pages show **Configure an
AI provider**. Open it, or use **Local settings → AI provider**.

Supported setup paths include:

- OpenAI-compatible APIs;
- Anthropic-compatible APIs;
- local Ollama;
- local LM Studio.

To run models on your own machine — and this is also the answer if you have no
API key at all, since nothing here requires a hosted account — start Ollama or
LM Studio and press **Ollama** or **LM Studio**. Each fills in that tool's
loopback base URL, which **Validate & apply** then probes for its model list.
Lemma talks to them as ordinary OpenAI-compatible providers, so the models,
their memory, and their lifecycle stay owned by the tool you already run. Local
inference then works without internet; connectors, web access, and other
external services still require their own networks.

Enter a base URL, default model, and API key when required, then choose
**Validate & apply**. Lemma discovers models and verifies that the default
model is usable. Configuration and secrets are applied transactionally; a
failed backend restart restores the prior configuration. Secrets live in
macOS Keychain or Windows Credential Manager.

Agents remain unavailable with a clear reason until a provider validates.
Non-AI features remain available. Configure a provider before the first `lemma
chat` or `lemma agent run`, or those are the commands that report it.

## Configure integrations and surfaces

Use **Local settings → Integrations** for Composio and custom Google or
Microsoft OAuth applications. Copy the callback URL displayed by the running
installation; ports are deliberately dynamic.

Use **Agent Surfaces** for Slack, Telegram, Teams, WhatsApp, and Resend.
Socket/long-polling modes do not require ingress. Webhook surfaces require a
public callback configured by the operator; Lemma does not create a tunnel
silently. Resend is optional and is unrelated to local account creation.

## Share a local installation

Open **Local settings → Sharing** from the workspace footer or the tray.

- **This computer** keeps the existing `app.lemma.localhost` origin.
- **Local network** binds one selected private IPv4/Wi-Fi interface and shows a
  URL and QR code. Use it only on a network you trust; it is HTTP.
- **Public link** uses your existing ngrok configuration. For Cloudflare, run
  `cloudflared tunnel login` once; Lemma can then create and reuse a dedicated
  named tunnel and DNS route automatically. Its generated tunnel credential is
  kept in private app storage. Existing named tunnels remain available as an
  advanced option, and Lemma never installs either CLI.

Every public activation repeats this warning: **Anyone with this link can
create an account and use this Lemma installation.** Public sharing intentionally
keeps signup open in this release. Cloudflare Quick Tunnels are not available.

The shared URL covers the workspace, auth, API, files, streamed chat/tool
calls, and webhook callbacks. Published pod apps stay local-only because their
current routes require wildcard subdomains. PostgreSQL, Redis, SuperTokens, the
private runtime and model endpoints are never exposed.

Closing to the tray keeps sharing active. Quitting, a Desktop disconnect,
network-interface loss, or tunnel exit stops sharing. LAN/Public mode never
resumes automatically.

## Lifecycle and tray behavior

There are two ways to leave, and they mean different things.

**Close the window** hides Lemma to the tray. Everything keeps running:
schedules fire, the Agent Host answers, and any shared link stays up. The tray
icon remains as the way back.

**Quit Lemma** (⌘Q) stops the local server and then exits. Because that ends
schedules, stops the agents on this computer, and closes any shared link, Lemma
says so first and names what is running; a quit with nothing running asks
nothing. Pods, files, and data stay on this Mac.

Everything else is repair, and lives in the tray under **Troubleshoot**:

| Action | Result |
| --- | --- |
| **Open Lemma** | Shows the workspace. |
| **Start Lemma** | Starts or reconciles the current desired state. |
| **Restart Lemma** | Restarts the backend and frontend without deleting data. |
| **Stop Lemma** | Stops the backend and frontend but leaves the private runtime warm. |
| **Stop the local server** | Stops application processes and the private runtime. |

Only a full stop releases all guest memory, which is why quitting performs one.
A transient component restart after Ready does not bounce the workspace back to
the installer.

## URLs and ports

On first start Lemma asks the OS for two high loopback ports and persists them
in `locald/network.json`. If an unrelated process later occupies either port,
Lemma does not terminate it; it allocates and persists a new pair.

The current URLs appear in Local settings and `lemma-stack status --json`:

| Surface | Shape |
| --- | --- |
| Workspace | `http://app.lemma.localhost:<frontend-port>` |
| API/auth | `http://app.lemma.localhost:<backend-port>` |
| Built app | `http://<slug>.apps.lemma.localhost:<backend-port>` |
| Sandbox app | `http://<sandbox>-<app>.workspaces.lemma.localhost:<backend-port>` |

Using the same `app.lemma.localhost` host for frontend and API preserves
WKWebView-compatible session cookies while ports distinguish the processes.
Production React apps remain available at
`<app-name>.apps.lemma.work`; local builds use the corresponding
`*.apps.lemma.localhost` route.

The sandbox receives the resolved API bridge as
`http://host.lemma.internal:<backend-port>`. The backend never guesses a
container runtime and never rewrites localhost automatically.

## CLI control

The `lemma-stack` CLI discovers the installed Desktop daemon and its dynamic
endpoints. Complete one Desktop local installation first. Desktop does not
install `lemma-stack`, and it is not on PyPI, so get it from the bootstrap
script:

```bash
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh |
  bash -s -- --cli-only
```

`--cli-only` installs `lemma-stack` and registers the `local` server in one
step. Without that flag the same script starts the Docker/Podman compatibility
install described under [External-runtime
compatibility](#external-runtime-compatibility), which is not the Desktop path.

Then, against the running installation:

```bash
lemma-stack status
lemma-stack status --json
lemma-stack start
lemma-stack restart
lemma-stack stop
lemma-stack stop --infra
lemma-stack doctor
lemma-stack logs locald
lemma-stack logs backend --follow
lemma-stack logs frontend
```

Managed configuration uses the same schema, validation, rollback, and OS vault
as Local settings:

```bash
lemma-stack config list
lemma-stack config get ai.protocol
lemma-stack config set \
  ai.protocol=openai_compat \
  ai.base_url=http://127.0.0.1:11434/v1 \
  ai.default_model=qwen3
lemma-stack config unset ai.protocol
```

The separate `lemma` CLI operates pods. It ships knowing one server,
`lemma-cloud`; `local` is written from the endpoints Desktop actually allocated
rather than from hardcoded ports. The bootstrap above already ran the
registration, so this is the step to repeat after a reinstall or when `lemma
servers select local` reports `Server not found: local`:

```bash
uv tool install lemma-terminal      # the `lemma` CLI; --cli-only does not install it
lemma-stack self register-cli --use
lemma servers select local
lemma auth login
```

Install it with `uv tool install`, which provisions the Python 3.14 the CLI
requires. `pip install lemma-terminal` on an older interpreter resolves back to
an obsolete release instead of failing; `lemma --version` shows what you have.

## Diagnostics and repair

Start from the symptom:

| Symptom | Start here |
| --- | --- |
| `lemma servers select local` says `Server not found: local` | `lemma-stack self register-cli --use`, above. |
| A `lemma` command behaves differently from the app, or reports an unexpected schema | `lemma doctor` — it diagnoses client/server version skew and duplicate CLI installs. |
| The stack will not start, or a component is unhealthy | `lemma-stack doctor`, then the logs below. |

The setup error view and **Local settings → Diagnostics** expose bounded,
redacted logs for:

- installer;
- lifecycle events;
- local daemon;
- migrations;
- backend;
- frontend;
- VM helper;
- guest and infrastructure services.

Logs use opaque cursors that survive rotation, return at most 128 KiB per
request, and redact passwords, tokens, API keys, cookies, and connection
credentials. A failed component is selected automatically and its recent
excerpt appears with the error.

Use **Open logs folder** for the source files. Webview debugging is available
from **Open developer tools** or `Cmd+Option+I` on macOS /
`Ctrl+Alt+I` on Windows. Set `LEMMA_DESKTOP_DEVTOOLS=1` for an automatic
inspector in a source/debug launch.

**Verify & repair runtime** replaces only immutable signed runtime files. It
does not delete the private data disk. If a child exits during startup, Lemma
fails immediately with its status and recent log excerpt instead of waiting
for the health timeout.

### Anonymous install health

**Local settings → Diagnostics** carries one switch: *Send anonymous install
health*. It is on in official builds and off in every build without an
ingestion key compiled in, which includes anything you build yourself.

What it sends is whether the app started and whether its runtime installed:
`desktop.launched`, `desktop.runtime_install`, `desktop.runtime_ready`,
`desktop.mode_selected`, `desktop.quit`. Each carries the operating system, the
architecture, the app version, and — for the two that measure something — a
*bucket* rather than a number, so `0-5s` rather than `3,214 ms`. An install
failure carries which step failed and one word for why, from a fixed list.

It cannot express anything else. There is no field for a pod, an organization,
a user, a file name, a hostname, a path, or an error message, and the event
type is a closed Rust enum rather than a map: an event this app cannot name is
an event it does not send.

It goes to `https://eu.i.posthog.com`, identified by a random id minted once
for this installation and stored beside it. The id is random — never derived
from your hostname, MAC address or machine id — and **Start over** takes it
with it, so a reset installation is a new one.

Three switches turn it off, and any one is enough:

```bash
LEMMA_TELEMETRY=0            # this launch
```

the toggle in Local settings, which is remembered and is never overridden by an
upgrade; and building without an ingestion key, which is the default for a
local build.

## Updates, data, and uninstall

Local Lemma stores application data and runs Lemma services on your computer.
Configured LLM providers, connectors, and online features can send requested
prompts, tool results, and connector payloads to external services. Local mode
does not mean offline operation.

### Checking for updates

Opening **Local settings**, and pressing **Check for updates** or **Install
update** there, fetches one file:

```
https://github.com/lemma-work/lemma-platform/releases/latest/download/latest.json
```

A nightly build reads `.../releases/download/desktop-nightly/latest.json`
instead — a tag whose contents are rewritten in place, so the address stays
constant.

Nothing else asks. There is no background poll and no check on launch, so an
installation whose settings are never opened never contacts GitHub about
updates at all.

The address carries nothing about your machine. Tauri's updater can interpolate
the installed version, target and architecture into an endpoint URL; Lemma's
endpoints contain no such placeholders, so the request is a plain GET for a
fixed path. GitHub sees what it sees for any download: your IP address, the
time, and the `User-Agent` the updater sends. The install id from
[Anonymous install health](#anonymous-install-health) is not part of it, and
turning telemetry off does not affect this request — they are unrelated paths.

If the update is installed, the payload and its signature are downloaded from
the same release, and the signature is checked against a public key built into
the app before anything is replaced.

### Rotating the update signing key

An installed app trusts exactly one public key: the one it was built with, in
`desktop/tauri.conf.json`. It has no way to learn a new one except by being
replaced, and it will only install a replacement signed by the key it already
trusts. So the order is fixed, and the wrong order strands every installed copy
permanently.

1. Generate the new pair with `tauri signer generate`. Keep the old private key
   until step 3 has shipped and been installed — it is the only thing that can
   still reach the installed base.
2. Commit the **new public key** to `desktop/tauri.conf.json`, but leave
   `TAURI_UPDATER_PRIVATE_KEY` set to the **old private key**. Cut a release.
   That release is signed with the old key, so installed apps accept it, and it
   carries the new public key forward.
3. Wait for that release to be installed. Until an app has taken it, it still
   verifies with the old key.
4. Replace `TAURI_UPDATER_PRIVATE_KEY` with the new private key. From the next
   release on, the pair agrees again.

`desktop/scripts/check_updater_key.py` runs in both release workflows and
compares the key that signed the payload with the key committed in
`tauri.conf.json`. It is deliberately a comparison of minisign key ids, not of
key material: the private key is never decrypted and never leaves the runner.

Between steps 2 and 4 the two disagree on purpose, and the check would
otherwise refuse the release. Set `LEMMA_UPDATER_KEY_ROTATION=1` on the step 2
release, and only on it: with that set the check *requires* the mismatch, so a
flag left switched on afterwards fails the next release rather than silently
disabling the check for good. Say which key signed the build in the release
notes.

A lost private key is not recoverable through this path. Installed apps can only
be moved to a new key by a build signed with the old one, so the remaining route
is a fresh download by hand.

Settings refreshes preserve unsaved drafts. Save applies the selected section;
Discard reloads that section from the saved configuration. If another save
changed the revision, review the conflict and discard/re-enter the affected
draft. Changing an API provider's destination requires entering a credential
for that destination or explicitly removing the saved key.

Managed state lives under:

- macOS: `~/Library/Application Support/Lemma`
- Windows: `%LOCALAPPDATA%\Lemma`

Important subpaths include:

- `runtime/releases/<version>-<artifact-identity>` — immutable installed
  host/guest candidates and retained releases; older installations also use
  version-only directory names;
- `locald/network.json` — resolved loopback ports;
- `locald/processes.json` — exact owned-process ledger;
- `locald/runtime/macos/data.raw` — sparse macOS persistent data disk;
- `locald/logs` and `runtime/install.log` — diagnostics.

The active macOS guest root is attached directly from its release directory as
read-only. Volatile OS state uses tmpfs; PostgreSQL, Redis, SuperTokens,
containerd, and workspace sandboxes use the separate data disk.

App updates never reset local data. An unsupported database migration or unknown
compatibility with an installed runtime blocks installation and keeps the
current version. Windows runtime upgrades are blocked until a data-preserving
migration is available. Matching PostgreSQL versions alone does not certify
an upgrade: consistent backup, migration recovery, and packaged-app upgrade
qualification remain required before a release can promise that guarantee.
Factory reset remains a separate, explicitly destructive recovery action.

Removing the app does not silently remove user data. Quit Lemma, back up
anything important, remove the application, and only then remove the platform
state directory if a destructive reset is intended.

## Test an unreleased pull request

The `Release Local Images` workflow can be manually dispatched on any branch
with `publish=false` and `share=true`. It publishes the branch's
digest-verified runtime archives to a prerelease tagged
`desktop-nightly-<short-sha>`, then attaches a signed, notarized online DMG
built against them — installable by anyone, with no version tag cut.

Nightly prereleases are currently kept indefinitely. Earlier builds pruned all
but the three most recent, and that pruning was removed; nothing replaced it, so
the releases page and the update feed's assets both grow without bound. Whether
to restore it is an open retention decision, and it is a real trade either way:
pruning a prerelease leaves any nightly still installed against it with nothing
to fetch on first launch, and not pruning grows storage for ever.

This is not a public offline installer. It exercises the same first-launch
installer using trusted application resources, while infrastructure and
sandbox OCI images still require network access. CI rejects:

- combined host/guest compressed archives above 6 GiB;
- the PR application resources above 7 GiB;
- expanded immutable runtimes above 8 GiB;
- a macOS guest root above 2 GiB.

Download the artifact for the exact commit, copy Lemma to Applications, and
perform the clean-install checklist in
[the Desktop maintainer guide](../desktop/README.md).

## External-runtime compatibility

`lemma-stack install` still supports explicit Docker/Podman compatibility for
Linux, development, CI, and migrations. Desktop never auto-selects that path,
never adopts the user’s default runtime, and does not document fixed ports for
managed installations.
