# Install and run Lemma locally

Lemma Desktop is the supported local installation. It installs and operates
Lemma without asking the user to install Docker, Podman, Homebrew, Python,
Node.js, PostgreSQL, Redis, or a general-purpose VM manager.

The signed application is a small online installer. On first local launch it
downloads the exact host and private-guest runtime for that Desktop version,
verifies their SHA-256 digests, extracts them into app-owned storage, and then
starts Lemma. Infrastructure and AgentBox OCI images are also downloaded when
first needed, so this release is not an air-gapped installer.

## Supported systems

| Platform | Minimum | Architecture | Private runtime |
| --- | --- | --- | --- |
| macOS | macOS 14 | Apple silicon | Apple Virtualization.framework |
| Windows | Windows 11 23H2 | x86-64 | Private WSL2 distribution |

Intel Macs, Windows on Arm, and Desktop Linux are not release targets yet.

Allow at least the expanded runtime size shown during setup plus 4 GiB of
working headroom. The immutable host and guest runtimes are gated at 2.25 GiB
combined; user databases, files, images, and AgentBox workspaces grow
separately.

## macOS installation

1. Open the [latest Lemma release](https://github.com/lemma-work/lemma-platform/releases/latest).
2. Download `Lemma_<version>_aarch64-online.dmg`.
3. Open the DMG and drag **Lemma** into **Applications**.
4. Eject the DMG and open `/Applications/Lemma.app`.
5. Choose **Local** and select **Install local services**.

Run Lemma from Applications, not from the mounted DMG. The public application
contains only the Desktop shell, `lemma-locald`, native runtime helpers, and
runtime metadata; CI rejects an installed public app larger than 25 MiB.

## Windows installation

1. Open the [latest Lemma release](https://github.com/lemma-work/lemma-platform/releases/latest).
2. Download `Lemma_<version>_x64-online-setup.exe`.
3. Run the signed installer and open Lemma.
4. Choose **Local** and select **Install local services**.

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
7. Start PostgreSQL and its `lemma` and `agentbox` databases.
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

- one all-in-one Python backend for API, workers, schedules, AgentBox
  management, surfaces, and document conversion;
- one Next.js frontend process;
- one private Linux runtime containing PostgreSQL, Redis, SuperTokens, and
  isolated AgentBox containers.

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

To run models on your own machine, start Ollama or LM Studio and press
**Use Ollama** or **Use LM Studio**. Each fills in that tool's loopback base
URL, which **Validate & apply** then probes for its model list. Lemma talks to
them as ordinary OpenAI-compatible providers, so the models, their memory, and
their lifecycle stay owned by the tool you already run. Local inference then
works without internet; connectors, web access, and other external services
still require their own networks.

Enter a base URL, default model, and API key when required, then choose
**Validate & apply**. Lemma discovers models and verifies that the default
model is usable. Configuration and secrets are applied transactionally; a
failed backend restart restores the prior configuration. Secrets live in
macOS Keychain or Windows Credential Manager.

Agents remain unavailable with a clear reason until a provider validates.
Non-AI features remain available.

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

AgentBox receives the resolved API bridge as
`http://host.lemma.internal:<backend-port>`. The backend never guesses a
container runtime and never rewrites localhost automatically.

## CLI control

The optional `lemma-stack` CLI discovers the installed Desktop daemon and its
dynamic endpoints. Complete one Desktop local installation first.

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

The separate `lemma` CLI operates pods. Register it against the resolved local
server rather than hardcoding ports:

```bash
lemma-stack self register-cli --use
lemma servers select local
lemma auth login
```

## Diagnostics and repair

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

## Updates, data, and uninstall

Managed state lives under:

- macOS: `~/Library/Application Support/Lemma`
- Windows: `%LOCALAPPDATA%\Lemma`

Important subpaths include:

- `runtime/releases/<version>` — immutable installed host/guest release;
- `locald/network.json` — resolved loopback ports;
- `locald/processes.json` — exact owned-process ledger;
- `locald/runtime/macos/data.raw` — sparse macOS persistent data disk;
- `locald/logs` and `runtime/install.log` — diagnostics.

The active macOS guest root is attached directly from its release directory as
read-only. Volatile OS state uses tmpfs; PostgreSQL, Redis, SuperTokens,
containerd, and AgentBox workspaces use the separate data disk. Updates replace
the immutable release and preserve data.

Removing the app does not silently remove user data. Quit Lemma, back up
anything important, remove the application, and only then remove the platform
state directory if a destructive reset is intended.

## Test an unreleased pull request

The `Release Local Images` workflow can be manually dispatched on a PR branch
with `publish=false`. It produces
`lemma-desktop-macos-pr-test-<commit>`, an ad-hoc-signed DMG containing the
branch’s compressed, digest-verified host and guest archives.

This is not a public offline installer. It exercises the same first-launch
installer using trusted application resources, while infrastructure and
AgentBox OCI images still require network access. CI rejects:

- combined host/guest compressed archives above 750 MiB;
- the PR application resources above 850 MiB;
- expanded immutable runtimes above 2.25 GiB;
- a macOS guest root above 1.25 GiB.

Download the artifact for the exact commit, copy Lemma to Applications, and
perform the clean-install checklist in
[the Desktop maintainer guide](../desktop/README.md).

## External-runtime compatibility

`lemma-stack install` still supports explicit Docker/Podman compatibility for
Linux, development, CI, and migrations. Desktop never auto-selects that path,
never adopts the user’s default runtime, and does not document fixed ports for
managed installations.
