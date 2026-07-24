# Install and run Lemma Local

This is the authoritative installation and operations guide for the managed
local product. Lemma Desktop is the supported local installation on macOS and
Windows. It owns the runtime, starts the application, stores secrets safely,
and exposes the same controls through its UI and the optional `lemma-stack`
CLI.

The normal path does **not** require Docker Desktop, Podman, Homebrew, Node.js,
Python, a Linux distribution, or manual VM sizing.

## Choose a package

Each Desktop release has two variants:

| Package | Use it when | What happens on first local setup |
| --- | --- | --- |
| **Online** | The computer can reach the release download | The small signed app downloads the exact, digest-pinned host and guest runtime for its own version. This is the recommended package. |
| **Offline** | The computer is air-gapped or runtime downloads are blocked | The complete host and guest runtime is already inside the installer. No runtime download is required. |

The current macOS online app is about 10 MiB installed. The offline app is
about 3 GiB installed because it includes the relocatable backend, frontend,
and managed Linux runtime. The offline size is expected. Leave at least 10 GiB
free for the installed runtime, writable database, files, and sandbox
workspaces.

Supported Desktop targets for this release are:

| Platform | Minimum | Architecture | Managed runtime |
| --- | --- | --- | --- |
| macOS | macOS 14 | Apple silicon | App-owned Virtualization.framework guest |
| Windows | Windows 11 23H2 | x86-64 | Private `LemmaRuntime` WSL2 distribution |

Intel Macs, Windows on Arm, and desktop Linux are not part of this release.
The external Docker/Podman compatibility path remains available for Linux,
development, and advanced migrations.

## Install on macOS

1. Open the [latest Lemma release](https://github.com/lemma-work/lemma-platform/releases/latest).
2. Download `Lemma_<version>_aarch64-online.dmg`, or the `offline` variant for
   an air-gapped installation.
3. Open the DMG and drag **Lemma** into **Applications**.
4. Eject the DMG, then open `/Applications/Lemma.app`.

Do not keep running Lemma from the mounted installer. The CLI and repair flow
look for the signed application in `/Applications` or your user Applications
folder.

The release DMG and nested runtime helpers are Developer ID signed, notarized,
and stapled. Normal setup does not ask for administrator access.

Pull-request DMGs produced by CI are ad-hoc signed and are for maintainers
only. They are paired with a short-lived Actions runtime bundle rather than
durable GitHub Release assets. Follow
[the Desktop branch-test procedure](../desktop/README.md#test-an-unreleased-branch-end-to-end);
do not treat that developer path as an end-user installation channel.

## Install on Windows

1. Open the [latest Lemma release](https://github.com/lemma-work/lemma-platform/releases/latest).
2. Download `Lemma_<version>_x64-online-setup.exe`, or the `offline` variant for
   an air-gapped installation.
3. Run the signed installer and open Lemma.

Lemma uses its own minimal WSL2 distribution; it does not install Ubuntu,
Docker Desktop, or Podman, and it does not change the default WSL distribution
or global `.wslconfig`.

If WSL2 is unavailable, Lemma shows **Set up Windows runtime**. That separate,
explicit action opens the Windows elevation prompt and enables the required
WSL components. Windows may require one restart. Reopen Lemma after the
restart and setup continues automatically.

## First local setup

The first launch asks where the workspace should run:

1. Choose **Local**.
2. Review the local-data notice and select **Install local services**.
3. Let setup finish. The online package downloads and verifies its exact
   runtime; the offline package activates its bundled runtime.
4. Select **Create your account**.
5. Create the local owner inside the Lemma app.

Local account creation stays in the app. It does not open the system browser,
does not require SMTP, and does not require email verification. Hosted
`lemma.work` sign-in intentionally uses the system browser instead.

The first start performs database initialization and may take several minutes.
Later starts reuse the installed runtime and persistent data. A single backend
process runs the API, worker, scheduler, AgentBox manager, surface receivers,
and document conversion; the frontend is the only other Lemma application
process. PostgreSQL, Redis, compatible local auth, and sandbox workloads remain
private implementation details.

## Open and manage Lemma in the UI

Open **Local Control Center** from Lemma's application or tray menu. Its pages
are:

- **Overview** — readiness, configured capabilities, recent health, and daily
  start/stop actions.
- **AI Providers** — the required system model profile.
- **Integrations** — Composio plus Google and Microsoft connector OAuth apps.
- **Agent Surfaces** — Slack, Telegram, Teams, WhatsApp, and Resend settings.
- **Services** — application and private-runtime capability health.
- **Updates** — Desktop/runtime version matching and runtime repair.
- **Diagnostics** — local paths, loopback endpoints, logs, and repair actions.

The daily controls have deliberate meanings:

| Control | Effect |
| --- | --- |
| **Open Lemma** | Opens the local workspace. |
| **Start** | Reconciles the desired state and starts anything missing. |
| **Restart application** | Restarts the backend and frontend while preserving private infrastructure and data. |
| **Stop application** | Stops the backend and frontend; private infrastructure stays warm. |
| **Stop everything** | Stops the application and private runtime. The next Start brings them back. |
| **Verify & repair runtime** | Replaces damaged signed runtime files without deleting configuration, databases, files, or workspaces. |

Closing the window does not silently destroy local services. Use the explicit
stop controls when you want Lemma to sleep.

## Configure the system AI profile

Agents require one validated system AI profile. In **Control Center → AI
Providers**:

1. Choose **OpenAI compatible** or **Anthropic compatible**.
2. Enter the provider base URL and a default model ID.
3. Enter an API key when the provider requires one.
4. Select **Validate & apply**.

Lemma connects to the configured provider, discovers its available models, and
requires the default model to be present. The backend is health-checked before
the new configuration becomes active. A failed apply restores the previous
configuration and secret values.

For local models, **Use Ollama** and **Use LM Studio** fill the standard
loopback endpoint. Loopback model servers do not require a placeholder API
key. A model server on another private-network address requires both an
explicit **Trust this private-network endpoint** choice and whatever
authentication that server expects. Lemma never scans the local network.

## Configure integrations and agent surfaces

All of these settings are optional:

- **Composio** enables its connector catalog using an API key and optional
  webhook secret.
- **Google connector OAuth app** configures Gmail, Calendar, and Drive
  connections. Its local callback is
  `http://api.lemma.localhost:8711/api/v1/connectors/oauth/callback`.
- **Microsoft connector OAuth app** configures Microsoft connections and uses
  the same local callback path. These credentials are separate from Teams bot
  credentials.
- **Slack Socket Mode** and **Telegram long polling** work without public
  ingress.
- **Teams**, **WhatsApp**, and webhook modes require a public callback that you
  configure. Lemma never creates a tunnel silently.
- **Resend** configures inbound email for an owned domain. It is not needed for
  local account creation.

Secret fields are write-only after saving. They live in macOS Keychain or
Windows Credential Manager, never in the operator configuration file, status
events, or logs.

## Local URLs

Use the exact loopback names shown by Lemma:

| Surface | URL |
| --- | --- |
| Workspace | `http://app.lemma.localhost:3711` |
| API and auth | `http://api.lemma.localhost:8711` |
| Built React app | `http://<slug>.apps.lemma.localhost:8711` |
| Live sandbox app | `http://<sandbox>-<app>.workspaces.lemma.localhost:8711` |

`lemma.localhost` is reserved for loopback and does not require public DNS.
Built React apps use the same routing and session behavior as
`<app-name>.apps.lemma.work` in production.

Agent and function sandboxes receive the explicit `host.lemma.internal` bridge
configured by the managed runtime through `WORKSPACE_CALLBACK_*` and
`FUNCTION_RUNTIME_GATEWAY_URL`. A new sandbox must reach the API health
endpoint before it is reported ready. The backend does not guess the container
topology and does not rewrite `localhost` or `127.0.0.1`.

## Install the optional stack-control CLI

The Desktop installer supplies the signed application and runtime. Complete
Local setup in Desktop once; after that `lemma-stack` can start and control the
same runtime even while the Desktop window is closed.

The CLI is not required for normal Desktop use. Its bootstrap installs `uv`
and the `lemma-stack` Python tool, but it does not install Docker or Podman
when used in CLI-only mode.

macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh |
  bash -s -- --cli-only
```

Windows PowerShell:

```powershell
$env:LEMMA_STACK_CLI_ONLY = "1"
irm https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.ps1 | iex
Remove-Item Env:LEMMA_STACK_CLI_ONLY
```

The `lemma-stack` distribution is currently installed from this repository;
it is not a separately published PyPI package. If the command is not visible
in a new terminal, run `uv tool update-shell`, reopen the terminal, and retry.

Verify discovery:

```bash
lemma-stack self info
lemma-stack status
```

If the CLI says no managed runtime is installed, put Lemma in Applications (on
macOS), open Desktop, choose Local, and let the first setup finish. The CLI
does not replace the signed Desktop/runtime installer in this release.

## Run and diagnose from the CLI

```text
lemma-stack start
lemma-stack stop
lemma-stack stop --infra
lemma-stack restart
lemma-stack status
lemma-stack status --json
lemma-stack doctor
lemma-stack doctor --json
lemma-stack logs locald
lemma-stack logs backend
lemma-stack logs frontend
lemma-stack logs backend --follow
lemma-stack self info --json
```

On Windows, `lemma-stack prepare` performs the same explicit one-time WSL2
enablement as **Set up Windows runtime**. It may report that a Windows restart
is required. Ordinary `start` does not elevate.

`doctor` intentionally fails until the application services are healthy and an
AI provider is validated. Use `status` when you only need lifecycle state.
Private PostgreSQL and Redis internals are shown in Control Center rather than
exposed as host services.

## Configure from the CLI

Managed CLI configuration uses the same transactional schema and OS credential
vault as Control Center:

```bash
lemma-stack config list
lemma-stack config list --json
lemma-stack config get ai.protocol
lemma-stack config path
```

Apply all fields of a new provider in one command so the profile can be
validated as a unit. For a loopback Ollama-compatible server:

```bash
lemma-stack config set ai.protocol=openai_compat ai.base_url=http://127.0.0.1:11434/v1 ai.default_model=qwen3
```

For a hosted OpenAI-compatible provider:

```bash
lemma-stack config set ai.protocol=openai_compat ai.base_url=https://provider.example/v1 ai.default_model=MODEL_ID ai.api_key=YOUR_API_KEY
```

Useful managed keys include:

```text
ai.protocol
ai.base_url
ai.default_model
ai.vision_models
ai.allow_private_network
ai.api_key

integrations.composio_enabled
integrations.composio_api_key
integrations.composio_webhook_secret
integrations.google_client_id
integrations.google_client_secret
integrations.microsoft_client_id
integrations.microsoft_client_secret

surfaces.slack_socket_mode
surfaces.slack_app_token
surfaces.slack_bot_token
surfaces.slack_signing_secret
surfaces.telegram_polling
surfaces.telegram_bot_token
surfaces.telegram_webhook_secret
surfaces.teams_app_id
surfaces.teams_tenant_id
surfaces.teams_app_password
surfaces.whatsapp_phone_number_id
surfaces.whatsapp_waba_id
surfaces.whatsapp_access_token
surfaces.whatsapp_verify_token
surfaces.whatsapp_app_secret
surfaces.resend_inbound_domain
surfaces.resend_api_key
surfaces.resend_signing_secret
```

Boolean values accept `true` or `false`. List values such as
`ai.vision_models` are comma-separated. `ai.models` is read-only because it is
the result of provider discovery.

Unset a normal field or remove a vault secret with the same command:

```bash
lemma-stack config unset surfaces.telegram_polling
lemma-stack config unset surfaces.telegram_bot_token
lemma-stack config unset ai.protocol
```

Managed secret values can never be printed back; `list` and `get` report only
`<configured>` or `<not configured>`. `config edit` is intentionally
unavailable for managed Desktop because direct file edits would bypass
validation, health checks, rollback, and the OS vault.

## Install the pod CLI

`lemma-stack` operates the local installation. The separate `lemma` command
builds and operates pods:

```bash
uv tool install lemma-terminal
lemma servers select local
lemma auth login
lemma skills install
```

Local auth opens at the managed `lemma.localhost` origins. Do not replace those
server URLs with raw `localhost`, `127.0.0.1`, or `sslip.io`.

See the [Lemma CLI setup guide](../lemma-cli/SETUP.md) for server and
project-scoped configuration.

## Updates, data, and removal

Installing a newer signed Desktop package stages the exact matching immutable
runtime on the next local launch. The prior verified runtime is retained for
recovery, but this release does not offer manual downgrade because its data
schema does not declare downgrade compatibility. **Verify & repair runtime**
repairs only runtime files and preserves data.

Managed state lives under:

- macOS: `~/Library/Application Support/Lemma`
- Windows: `%LOCALAPPDATA%\Lemma`

Removing the application does not silently delete this state. A supported
managed-data removal UI is not shipped in this release, so back up important
work before deleting that directory manually. `lemma-stack uninstall` applies
only to the external Docker/Podman compatibility install; it does not uninstall
managed Desktop data.

## Troubleshooting

### Lemma says “Asleep”

The application is intentionally stopped. Select **Start Lemma** once. A
second Start during the same operation follows the existing progress instead
of launching another operation.

### “Another local operation is running”

Setup, start, stop, restart, configuration apply, and repair are serialized.
Wait for the current progress to finish. Current Desktop builds attach repeated
Start requests to the operation already in progress.

### “No such file or directory” or a missing runtime bridge

Confirm Lemma was copied to Applications and is not running from the DMG.
Install the online/offline package for the same version, then use **Control
Center → Updates → Verify & repair current runtime**. Repair verifies signed
runtime files and preserves user data.

### “Runtime package … is not published yet”

The online shell is present, but its matching immutable host or guest runtime
asset is not available at the URL in the signed release manifest. This is
expected only for an unpublished development build. Use that build's matching
offline installer or publish the runtime assets before distributing the online
installer; repeatedly selecting Retry cannot repair a missing release asset.

For an unreleased branch build, use its localized test manifest for the first
verified installation as described in
[`desktop/README.md`](../desktop/README.md#test-an-unreleased-branch-end-to-end).
After activation, quit Lemma and reopen it normally from Applications or the
Start menu. The app reuses the complete verified runtime without contacting the
artifact host. The manifest override is required again only for repair or a
different unpublished runtime version.

### Windows asks for setup or restart

Select **Set up Windows runtime**, approve the one-time Windows prompt, and
restart Windows if requested. Reopen Lemma; do not install Ubuntu or Docker
Desktop for this flow.

### A port is already in use

The managed gateway needs loopback ports `3711` and `8711`. Stop the other
process using the port, then select **Start** or run `lemma-stack start`. Lemma
does not silently move to a different origin because cookies and app subdomains
depend on the stable contract.

### AI validation fails

Check that the base URL is the provider's API root, the API key can list
models, and the default model is returned by that provider. For a LAN endpoint,
enable the explicit private-network trust option. The previous working
configuration remains active after a failed apply.

### More diagnostics

Select **View log** directly on the setup/error screen to see the persistent
installer transcript, including downloads, verification, activation, and the
exact error from the latest attempt. It is retained at
`runtime/install.log` below the managed state directory and rotates at 1 MiB.
It remains available even when `lemma-locald` has not started.

Use **Control Center → Diagnostics → Open logs folder** for the installer,
daemon, backend, frontend, and VM logs, or:

```bash
lemma-stack status --json
lemma-stack doctor --json
lemma-stack logs locald
lemma-stack logs backend
lemma-stack logs frontend
```

## External-runtime compatibility and source development

The installer without `--cli-only` retains the old Docker/Podman stack for
Linux, CI, development, and explicit migration work:

```bash
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh | bash
```

That path stores its own configuration under `~/.lemma/local`, exposes
container-oriented `db`, `redis`, and `uninstall` commands, and is never
auto-selected by Desktop. Set `LEMMA_STACK_FORCE_EXTERNAL_RUNTIME=1` only when
you intentionally want it on a machine that also has managed Desktop.

For hot-reload work from a source checkout, follow
[CONTRIBUTING.md](../CONTRIBUTING.md). Developer ports and prerequisites are
separate from the supported Desktop installation above.
