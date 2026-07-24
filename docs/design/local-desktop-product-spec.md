# Lemma Local Desktop: Product Specification

**Status:** Accepted; implementation in progress · **Audience:** Product, design, desktop, platform, backend, frontend, AgentBox, release engineering

**Scope:** `desktop`, `lemma-stack`, and the local-only runtime contract they own · **Last updated:** 2026-07-22

**Implementation baseline:** The current branch includes the all-in-one backend,
two-process host pack, PostgreSQL-backed embedded AgentBox, in-process document
conversion, durable local daemon, managed VZ/WSL guest providers, narrow guest
runtime API, production-parity local app routing, explicit sandbox callback
configuration and reachability gates, privileged Control Center, OS-vault
secrets, shared managed lifecycle/configuration commands in `lemma-stack`, and
signed online/offline packaging. Backup/update/uninstall policy, resource UX,
local-auth replacement, and the full supported-client acceptance matrix remain
release gates rather than implied completed work.

## 1. Executive summary

Lemma Local should install, configure, run, update, diagnose, and uninstall like a first-class desktop product. A user should not need Homebrew, Python, Node.js, Docker Desktop, Podman, a shell, a manually sized VM, or knowledge of Lemma's service topology.

The proposed product has two visible surfaces:

1. **Lemma**, the workspace application the user works in.
2. **Local Control Center**, a native desktop surface for setup, AI providers, integrations, service health, resources, storage, updates, logs, and repair.

Under the hood, a small `lemma-locald` supervisor is the single local control plane used by the desktop app and `lemma-stack` CLI. Lemma itself has only two long-lived application processes: a signed **all-in-one backend** and the frontend. The backend combines the API, Streaq worker, scheduler, native surface receivers, AgentBox manager, and in-process document conversion. A private app-owned Linux runtime runs PostgreSQL, Redis, initially SuperTokens for compatibility, and on-demand AgentBox sandbox workloads. Users never install or manage Docker or Podman.

AgentBox stores its durable control state in a separate `agentbox` database on the same PostgreSQL instance. After a compatibility-gated local-auth replacement, the steady-state infrastructure can shrink from PostgreSQL + Redis + SuperTokens to PostgreSQL + Redis. `lemma-locald` and the guest agent are product infrastructure helpers, not additional Lemma application services.

This is a hybrid architecture, but it is presented as one product. The local runtime is an implementation detail with adaptive resources and automatic idle reclamation, not a machine the user must configure.

The design makes an explicit distinction between:

- **Core readiness:** Lemma can open, persist data, authenticate locally, and expose its settings.
- **AI readiness:** at least one validated model profile exists.
- **Integration readiness:** optional OAuth apps and agent surfaces are configured only when the user wants them.
- **Sandbox readiness:** the larger workspace runtime is downloaded lazily on the first operation that needs it.

A fresh installation must never end in a technically “healthy” but functionally unusable state with no model, unexplained missing credentials, or no route to fix configuration.

## 2. Why the current experience falls short

The present implementation has good foundations but exposes too much infrastructure and too little product guidance.

- `lemma-stack` runs the frontend, backend, AgentBox manager, PostgreSQL, Redis, SuperTokens, and optionally Kreuzberg as containers. It requires a Docker or Podman API and exposes three host ports.
- On macOS and Windows, Podman requires a Linux VM. The current managed machine is configured for 6 GiB RAM, four CPUs, and a 100 GiB disk. These values are presented as a machine allocation instead of adapting to work.
- The one-line installer may install Homebrew/Podman prerequisites, downloads multiple application images, and teaches the user runtime-provider concepts before Lemma itself provides value.
- The desktop is a thin shell over the same supervisor. Its first run chooses cloud or local, confirms installation, shows phases, and ends at account creation. It does not configure the required model profile or make system OAuth and surface capabilities legible.
- Secrets are placed in `~/.lemma/local/config.toml` as backend environment variables. The file is permission-restricted, but the desktop has no guided validation, capability model, or operating-system credential vault.
- Local browser access depends on the public `sslip.io` DNS name for subdomains and cookie scope. A fully local product should not require a public DNS resolver to address its own loopback service.
- Status is container-centric and mostly binary. It does not express degraded-but-usable states, configuration readiness, resource pressure, update safety, data backup status, or actionable recovery.
- macOS is the only desktop distribution path currently described. Windows has CLI installation coverage but not an equivalent signed desktop installation and first-run experience.

The existing developer workflow is important evidence for the target direction: `standalone_app:app` already combines the API, Streaq worker, and scheduler in one host process, while AgentBox already supports PostgreSQL state. The proposal extends that proven composition by embedding the AgentBox manager lifecycle in the same backend process and removes the separate local AgentBox server. This is a local composition choice; cloud/scale-out deployments may continue splitting these roles.

## 3. Product vision

> Install Lemma, choose how it should think, and start working. Lemma quietly manages everything else on this computer.

The benchmark is not merely “easier than the current installer.” The experience should borrow the best traits of:

- a normal signed desktop installer;
- Postgres.app's self-contained, no-terminal ownership of a complex service;
- Ollama and LM Studio's discoverable local model endpoints and simple background-server mental model;
- OrbStack's low-idle-overhead, invisible-VM posture;
- Docker Desktop's service dashboard, diagnostics, and resource-saver behavior;
- WSL2's managed Linux compatibility on Windows without asking users to administer a traditional VM.

## 4. Goals and non-goals

### 4.1 Goals

1. Install on a clean supported Mac or Windows PC from one signed package.
2. Require no separately installed developer toolchain or container product.
3. Make the default path local-only and bind all product endpoints to loopback.
4. Guide the user to a validated LLM profile before their first agent run.
5. Make optional OAuth apps, connectors, and messaging surfaces discoverable and testable.
6. Provide one coherent control center for lifecycle, health, configuration, resource use, updates, logs, backup, repair, and uninstall.
7. Hide virtualization details in normal operation while preserving an expert diagnostics path.
8. Start quickly, consume negligible CPU while idle, and reclaim sandbox resources automatically.
9. Preserve local data across application upgrades and migrate existing `lemma-stack` installations safely.
10. Keep the desktop app, CLI, and automation behavior in sync by using the same control-plane API.
11. Maintain a strict trust boundary: the remotely served Lemma app never receives general native IPC privileges.
12. Support offline operation after required artifacts and a model are available locally.
13. Keep the local application topology to one backend process and one frontend process; background database/cache/runtime helpers remain implementation details.
14. Serve built pod React/web apps locally with production-equivalent per-app origins at `<public-slug>.apps.lemma.localhost`.
15. Limit always-on infrastructure containers to PostgreSQL, Redis, and compatibility auth initially, with PostgreSQL + Redis as the gated target; count sandbox compute separately because it is on-demand.

### 4.2 Non-goals

- Replacing Lemma Cloud or synchronizing local and cloud workspaces.
- Turning the desktop app into a general-purpose Docker or VM manager.
- Making every external messaging surface work without provider-side setup or public ingress.
- Bundling a large local foundation model in the base installer.
- Supporting arbitrary third-party OCI workloads through the public product UI.
- Eliminating Linux virtualization at any cost. Full AgentBox isolation requires a Linux kernel on macOS and Windows; the goal is to eliminate user-managed virtualization.
- Preserving every current low-level `lemma-stack` flag as a primary UX. Expert compatibility may remain in the CLI.
- Supporting Intel Macs in the first redesigned release. See the platform matrix.

## 5. Product principles

1. **One product, two application processes.** The UI speaks in capabilities—Data, AI, Sandboxes, Integrations—not backend subcomponents or container names unless the user opens diagnostics.
2. **Usable before perfect.** Missing optional configuration creates an explicit degraded state with a direct action, not a fatal install failure.
3. **Configuration is a journey.** Every credential has an explanation, test action, restart impact, and clear storage policy.
4. **Progressive download.** Install the core first. Download browser/sandbox tools only when requested; document conversion ships in the backend pack.
5. **Adaptive, not reserved.** Resource settings are ceilings and policies. Lemma reclaims idle memory and stops unused compute automatically.
6. **Local means loopback.** No LAN listener, public tunnel, or telemetry is enabled without explicit consent.
7. **No secret in the webview.** Secrets are entered through a privileged local control surface and stored in the operating-system vault.
8. **Repair before reinstall.** Diagnostics must identify a failed layer and offer the smallest safe repair.
9. **Expert access without expert burden.** The CLI, logs, SQL shell, and runtime details remain available, but are not prerequisites.
10. **Updates are reversible until data migration makes that impossible.** The UI states rollback boundaries before applying an update.

## 6. Users and jobs

### 6.1 Primary users

- **Local knowledge worker:** wants Lemma's full experience with a hosted model key and no infrastructure knowledge.
- **Privacy-first user:** connects Ollama, LM Studio, or another loopback model and expects normal work to remain on-device.
- **Builder:** creates pods, functions, apps, and agent workflows and needs reliable Linux sandboxes and inspectable workspace files.
- **Integration-heavy operator:** configures Google, Slack, Microsoft, Telegram, or other provider credentials and needs truthful callback/ingress guidance.
- **IT-managed user:** installs from a signed/offline package, needs predictable data locations, proxy support, update policy, support bundles, and silent commands.

### 6.2 Core jobs

- Install Lemma without preparing the computer first.
- Know whether Lemma is ready and what is missing.
- Connect and test an AI provider.
- Add an integration without understanding environment variables.
- Start and stop Lemma without worrying about orphaned processes.
- See what is using CPU, memory, and disk.
- Recover from an interrupted update or damaged runtime without losing data.
- Back up or move local data.
- Completely remove Lemma and understand which data will remain.

## 7. Research synthesis and product lessons

| Source | Observed pattern | Lesson for Lemma |
| --- | --- | --- |
| [Apple Virtualization framework](https://developer.apple.com/documentation/virtualization) | Native VIRTIO devices include networking, storage, directory sharing, and memory ballooning. | A Mac runtime can be app-owned and adaptive; Podman Machine need not be a user-facing dependency. |
| [Apple Containerization](https://github.com/apple/containerization) and [Apple container](https://github.com/apple/container) | OCI containers run in lightweight per-container VMs with sub-second starts and per-container file sharing; current support is Apple silicon/macOS 26+. | Add a native provider path where supported, but do not make a macOS-26-only framework the sole first release path. |
| [Podman Machine](https://docs.podman.io/en/latest/markdown/podman-machine.1.html) | macOS and Windows must use a VM provider. | Replacing Docker with Podman does not remove the VM; it mainly changes who exposes it. |
| [Docker Resource Saver](https://docs.docker.com/desktop/use-desktop/resource-saver/) | The Linux VM stops after idle and restarts automatically in seconds. | Idle reclamation should be automatic, visible, and configurable as a policy. |
| [Docker Desktop VMM options](https://docs.docker.com/desktop/features/vmm/) | macOS performance depends heavily on the VMM and file-sharing path. | Lemma must own and benchmark its exact workload rather than inherit an arbitrary user runtime. |
| [Finch architecture](https://runfinch.com/architecture/) | A small native client composes Lima, containerd, BuildKit, and WSL2. | Use focused upstream primitives behind a Lemma control plane instead of shipping a full general-purpose desktop runtime. |
| [WSL2 architecture](https://learn.microsoft.com/en-us/windows/wsl/compare-versions) | Windows provides a managed Linux utility VM with full syscall compatibility and dynamic resource behavior. | A private imported Lemma distribution is the lowest-friction Windows base for Linux-only infra and sandboxes. |
| [WSL import](https://learn.microsoft.com/en-us/windows/wsl/basic-commands) | Applications can import a custom tar/VHD distribution to a chosen location. | Lemma can ship its own minimal distribution without requiring Ubuntu or Docker Desktop. |
| [WSL advanced settings](https://learn.microsoft.com/en-us/windows/wsl/wsl-config) | Memory reclaim and sparse VHD behavior are available. | Favor sparse storage and reclaim; avoid overwriting global `.wslconfig` without consent. |
| [OrbStack](https://orbstack.dev/) | Users value instant start, negligible idle CPU, dynamic disk, and management from a native UI. | “Invisible runtime” is a product requirement, not a marketing afterthought. |
| [Postgres.app](https://postgresapp.com/) | A complicated database is packaged as a standard Mac app with status and optional CLI integration. | Lemma should own dependencies and make advanced tools optional. |
| [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) | A stable local OpenAI-compatible endpoint is available at `localhost:11434`. | Auto-detect and validate local models rather than asking users to transcribe base URLs and model lists. |
| [LM Studio local server](https://lmstudio.ai/docs/developer/core/server) | Local OpenAI- and Anthropic-compatible endpoints are available and model metadata can be queried. | Present detected local providers as one-click choices with capability checks. |
| [OAuth for native apps, RFC 8252](https://www.rfc-editor.org/rfc/rfc8252) | System browsers, PKCE, and loopback redirects are the recommended native flow. | Use browser + PKCE + random loopback callback; never put access/refresh tokens in a custom URL. |
| [RFC 6761](https://www.rfc-editor.org/info/rfc6761/) and [secure contexts](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts) | `localhost` and its subdomains are reserved for loopback and treated as potentially trustworthy. | Replace the public `sslip.io` dependency with `*.lemma.localhost`, subject to a cross-WebView compatibility gate. |
| [Tauri capabilities](https://v2.tauri.app/security/capabilities/) | Native permissions can be scoped per window/webview; remote access requires an explicit capability. | Keep the workspace webview unprivileged and isolate the privileged Control Center in bundled local UI. |

## 8. Supported platform and runtime matrix

### 8.1 Initial product support

| Platform | Minimum | Architecture | Local runtime | Notes |
| --- | --- | --- | --- | --- |
| macOS | 14 | Apple silicon | Lemma-managed lightweight Linux VM using Apple Virtualization.framework and containerd | No Homebrew, Podman, Docker, Rosetta, or admin access in the normal path. |
| Windows | Windows 11 23H2 | x86-64 | Private `LemmaRuntime` WSL2 distribution with containerd | If WSL is disabled, one elevated enablement step and possibly a reboot are required. |

Windows on Arm and macOS 26's Apple Containerization provider are follow-on targets gated by release artifact parity and end-to-end sandbox tests. Existing CLI-only Linux installation remains supported but is outside this desktop specification.

### 8.2 Why not “no VM”

PostgreSQL can technically run as a host process, but Redis has no equivalent first-party Windows host distribution and AgentBox sandboxes require Linux container semantics, process isolation, Chromium, and production-compatible toolchains. A host-only implementation would either weaken sandbox isolation, create divergent Windows behavior, or reimplement a Linux compatibility layer. Removing the SuperTokens service later reduces the guest service count but does not remove this Linux requirement.

The target therefore removes the **managed-VM burden**, not the Linux kernel. Normal UI never mentions memory reservations or machine names. Diagnostics may show “Lemma Runtime” and its current/peak resources.

## 9. Product surface and information architecture

### 9.1 Main Lemma window

The main window continues to load the shared Lemma frontend. It gains a desktop-aware readiness banner and links into Control Center, but it never receives broad filesystem, shell, secret, or lifecycle IPC.

Required desktop states:

- **Ready:** all essential capabilities work.
- **Needs AI setup:** data and settings work; agent actions route to the AI setup card.
- **Sandbox pack downloading:** ordinary browsing works; sandbox actions show scoped progress.
- **Integration needs attention:** only the affected integration is disabled.
- **Updating:** read-only or maintenance screen with progress when a data migration requires downtime.
- **Repairing:** shows the affected subsystem and preserves access to support actions.

Built pod apps are first-class local product surfaces. An app served at `sales.apps.lemma.work` in production is served at `sales.apps.lemma.localhost:<gateway-port>` locally, with the same built assets, injected Lemma SDK configuration, authenticated API access, SPA routing, streaming, and origin isolation. App links may open in a normal browser or an unprivileged desktop webview, but never inherit Control Center IPC.

### 9.2 Local Control Center

Control Center is bundled UI in a separately permissioned window. It opens from the app menu, tray, first-run flow, or `lemma-stack ui`.

Navigation:

1. **Overview** — readiness scorecard, start/stop, version/update, resource summary, recent problems.
2. **AI Providers** — system default, profiles, detected local servers, cloud/custom endpoints, test results, model capabilities.
3. **Integrations** — connector OAuth apps, user connections, callback URLs, validation, reconnect actions.
4. **Agent Surfaces** — Slack, Teams, Telegram, WhatsApp, email; receiver mode and ingress requirements.
5. **Services** — capability-oriented health with an expandable technical service list, logs, restart, and dependency graph.
6. **Storage & Backups** — location, sizes, backup/export, restore, move data, retention.
7. **Resources** — current/peak CPU and memory, runtime state, disk/image usage, sandbox idle policy.
8. **Network & Privacy** — loopback binding, optional LAN access, optional public relay/tunnel, proxy, telemetry consent.
9. **Updates** — app/runtime/data versions, channel, changelog, scheduled policy, rollback availability.
10. **Diagnostics** — doctor results, support bundle, raw logs, ports, runtime details, repair/reset actions.
11. **Advanced** — CLI integration, raw non-secret config, experimental providers, developer mode.

## 10. Installation journeys

### 10.1 macOS

1. User downloads a notarized DMG and drags Lemma to Applications.
2. Lemma launches and verifies:
   - supported macOS and Apple silicon;
   - virtualization availability;
   - at least 10 GiB free for the recommended path or 5 GiB for core-only;
   - loopback port availability;
   - network/proxy reachability.
3. The screen explains what will be installed with two expandable sizes:
   - **Core:** all-in-one backend and frontend packs plus database/cache runtime and the compatibility auth service while it remains required.
   - **Workspace tools:** Chromium, Node/Python toolchains, and sandbox image; downloadable now or later.
4. The user chooses a data location or accepts the platform default.
5. Lemma downloads signed, resumable, content-addressed artifacts. Installation can resume after quit or network loss.
6. The core starts and applies migrations.
7. Onboarding continues to local identity and AI setup.

No Homebrew prompt, terminal window, VM name, or fixed RAM/disk allocation appears.

### 10.2 Windows

1. User runs a signed per-user installer (MSIX where requirements permit; signed NSIS fallback for broader distribution).
2. Lemma checks WSL version and virtualization support.
3. If WSL2 is ready, Lemma imports its private distribution into `%LOCALAPPDATA%\Lemma\runtime` and continues without installing Ubuntu.
4. If WSL is available but stale, Lemma explains and invokes the standard WSL update flow.
5. If the Windows feature is disabled, ordinary startup remains read-only and shows **Set up Windows runtime**. Only that explicit user action asks once for elevation and runs `wsl.exe --install --no-distribution --no-launch`; cancellation is recoverable.
6. If Windows requires a reboot, Lemma records a private resume marker, explains the single required restart, and continues startup on the next launch. It does not leave the user at a generic installer error.
7. The private distribution never becomes the user's default distro and does not modify other distributions.
8. Core and optional workspace packs install and onboarding continues.

### 10.3 Existing runtime adoption

Docker and Podman are no longer defaults. Advanced users may enable an external-runtime provider after installation. This provider is unsupported for the zero-config SLO and cannot be silently selected merely because Docker is running.

### 10.4 Offline and managed deployment

Release engineering produces:

- a small online installer/application;
- a full offline bundle containing the core runtime and both platform architectures in scope;
- machine-readable checksums, signatures, SBOMs, and licenses;
- silent install/uninstall switches and update-policy configuration for IT.

Offline install must clearly state that cloud model providers and OAuth authorization still need network access. A local model endpoint can make normal agent use offline.

The online artifact is the recommended consumer download. Current macOS arm64
measurements are approximately 10 MiB installed for the online `.app`, 307 MB
compressed for the host pack, and 224 MB for the guest pack after installation
selects Local mode. The air-gapped `.app` is approximately 3.0 GiB installed
because it embeds both expanded packs and the guest's sparse disk image. The
download page and preflight UI must label these as different products and show
download, expanded runtime, and writable-data headroom separately.

## 11. First-run onboarding

Onboarding is resumable and versioned. Closing Lemma never loses completed work. Each screen has Back, Continue, and—only where safe—Do this later.

### Step 1: Welcome and privacy

- State that workspace data, database contents, and sandbox files are stored locally.
- State that prompts or integration data leave the machine when a configured model/provider/integration requires it.
- Link to exact data locations and the privacy controls.

### Step 2: Install core

- Show download/install progress by meaningful artifact and total bytes.
- Allow pause/resume.
- On failure, retain downloaded chunks and offer Retry, Change proxy, Use offline bundle, and View details.

### Step 3: Create the local owner

The desktop creates a single local owner identity using a short-lived bootstrap capability. The user supplies a display name; email is optional unless an enabled feature requires it. No cloud account or OAuth client is necessary for local ownership.

Browser access remains protected. The user may set a local password or use an OS-mediated desktop unlock. Adding Google/Microsoft login is optional and happens later.

The first managed release keeps SuperTokens behind the loopback gateway so current frontend session and recipe behavior remains compatible. A later lightweight local-auth mode may replace the SuperTokens core only after a captured compatibility suite proves login, refresh, logout, password reset/change, third-party callback, session revocation, CSRF, CLI tokens, MCP tokens, and multi-window behavior. This must not be treated as a container-removal shortcut: auth migration is security-critical and needs its own data migration and rollback plan.

### Step 4: Choose how Lemma thinks

The screen first scans loopback for supported providers:

- Ollama at `http://localhost:11434`;
- LM Studio at `http://localhost:1234`;
- previously configured custom OpenAI- or Anthropic-compatible endpoints.

For a detected provider, Lemma lists available models and flags whether each supports tools and vision. The user selects a default and runs a real, low-cost validation request.

Cloud options include OpenAI, Anthropic, and Custom compatible endpoint. The flow asks only for fields relevant to that provider, stores secrets in the OS vault, discovers models when the provider supports it, and lets the user explicitly mark capabilities the API cannot report.

The user may defer AI setup. If deferred, the result is **Needs AI setup**, not “Ready.” The main product opens, but the first agent action returns to this screen with its context preserved.

### Step 5: Workspace tools

Explain that functions, app builds, browsing, and shell/Python sessions need an isolated workspace pack. Recommend downloading in the background, but allow Later. Show exact additional disk size.

### Step 6: Optional integrations

Offer high-level choices, not a wall of environment variables:

- Google apps;
- Slack;
- Microsoft apps and Teams;
- Telegram;
- Other connectors.

Each choice opens its own guided setup. This step is skippable and never blocks core readiness.

### Step 7: Ready summary

Show a checklist:

- Backend and frontend running;
- Local database/cache/auth dependencies healthy;
- Local owner created;
- AI provider configured or deferred;
- Workspace tools installed or deferred;
- Integrations configured or deferred;
- Backup location and automatic-update policy.

The primary CTA is **Open Lemma**. Secondary actions are **Finish AI setup** or **Download workspace tools**, depending on readiness.

## 12. AI provider experience

### 12.1 Profile model

Control Center manages first-class runtime profiles rather than raw `LEMMA_*` variables. Each profile contains:

- provider/protocol;
- base URL;
- credential reference;
- discovered and manually added models;
- default model;
- tool, vision, reasoning, and embedding capabilities;
- last validation time and result;
- scope: system, organization, or personal;
- outbound-data disclosure.

The existing `system:lemma` profile remains the compatibility default. Local configuration renders to it until the backend gains a native operator-settings API.

### 12.2 Validation

Saving a profile performs:

1. endpoint reachability and TLS validation;
2. authentication validation;
3. model-list discovery where supported;
4. a small non-streaming generation;
5. a minimal tool-call probe for agent-default candidates;
6. optional vision probe when the user marks vision support.

Failures preserve edits and distinguish DNS, refused connection, TLS, authentication, model-not-found, quota, and incompatible tool calling.

### 12.3 Local-provider safety

Loopback servers are accepted by default. LAN endpoints require an explicit “Trust this network endpoint” confirmation. Lemma must not auto-discover or send prompts to arbitrary LAN services.

## 13. Integrations, OAuth apps, and agent surfaces

### 13.1 Separate three concepts in the UI

1. **Login providers:** optional Google/Microsoft sign-in for the local Lemma identity.
2. **Connector OAuth apps:** system-wide client ID/secret used when a Lemma user connects Gmail, Calendar, Drive, Slack, Jira, and similar apps.
3. **Agent surfaces:** credentials and receiver mechanisms for agents that participate directly in Slack, Teams, Telegram, WhatsApp, or email.

They may use the same external vendor but are not interchangeable. The UI must never label all three simply “OAuth.”

### 13.2 Connector setup

For every native connector, Control Center provides:

- purpose and data-access explanation;
- provider console deep link;
- exact redirect URI with copy action;
- required scopes derived from the connector catalog;
- client ID and secret fields stored in the vault;
- Validate configuration;
- Connect test account;
- affected services and restart behavior.

Google connector credentials remain separate from Google login credentials, even if a compatibility fallback continues internally.

### 13.3 Surface receiver modes

The product must be truthful about local reachability:

| Surface | Preferred local mode | Public ingress required? |
| --- | --- | --- |
| Slack | Socket Mode | No |
| Telegram | Long polling | No |
| Teams | Bot Framework callbacks | Usually yes |
| WhatsApp | Meta webhooks | Yes |
| Email/Resend inbound | Provider webhooks | Yes |

When public ingress is required, Lemma offers:

- **Lemma Relay** (future/optional): authenticated outbound tunnel with a stable URL;
- **Custom tunnel:** user supplies an HTTPS base URL;
- **Manual only:** show callback paths and health checks.

No tunnel is enabled silently. The Overview page shows a prominent “Public ingress on” state while active.

## 14. Lifecycle and daily operation

### 14.1 Start behavior

- Opening Lemma starts the host control daemon if needed.
- Core services start automatically when the user opens the app or invokes a local CLI command.
- `lemma-locald` starts one all-in-one backend and one frontend process; worker, scheduler, surfaces, AgentBox manager, and document conversion are backend subsystems rather than independently managed services.
- The main window becomes interactive in degraded mode as soon as the gateway and configuration UI are ready; it does not wait for optional workspace images.
- Warm launch goes directly to the product while health verification continues unobtrusively.

### 14.2 Close and quit semantics

- Closing the window hides Lemma by default if “Keep Lemma ready” is enabled.
- **Quit Lemma** offers:
  - Quit UI, keep services ready;
  - Quit and stop application services;
  - Quit and stop everything.
- The selected default is remembered and visible in Settings.
- “Start at login” separately controls the daemon and UI. Users should not have to infer this behavior from tray persistence.

### 14.3 Idle behavior

- Sandbox containers stop after 10 minutes without active sessions by default.
- A sandbox is not shown as ready until it has reached the local API from inside
  its isolation boundary; the release qualification flow also executes a real
  authenticated `lemma-cli` operation from a fresh sandbox.
- The Linux runtime enters warm idle after no containers need compute.
- On macOS it balloons memory down and can stop fully after the configured delay.
- On Windows it terminates the private WSL distribution when Lemma is the only active consumer and no work remains; Lemma never shuts down unrelated WSL distributions.
- Resume happens automatically and reports the expected delay only when the user initiates an action.

### 14.4 Service status model

Every component reports one of:

`not_installed`, `installing`, `stopped`, `starting`, `healthy`, `degraded`, `blocked`, `stopping`, `updating`, `repairing`, `failed`.

The overall state is computed from capabilities, not by counting healthy processes. For example, a failed AI profile means “Needs AI setup,” while a failed database means “Lemma unavailable.”

## 15. Configuration system requirements

1. Every setting has a schema: type, label, help, secret classification, default, validation, affected processes, and restart scope.
2. The UI is generated or at least validated against the same schema used by `lemma-stack`.
3. Non-secrets live in a versioned TOML document.
4. Secrets live in Keychain/Credential Manager and are referenced by opaque IDs.
5. Exported diagnostics contain setting names and redacted presence/last-four metadata, never values.
6. Changes are applied atomically. Validation happens before process restart.
7. A failed restart rolls back the rendered runtime config and reports what happened.
8. Advanced raw editing cannot display secret material and runs schema validation before save.
9. The CLI provides equivalent `config`, `secret`, `profile`, `integration`, and `surface` commands.

## 16. Health, diagnostics, repair, and support

### 16.1 Health model

Checks are layered:

- host prerequisites;
- control daemon and gateway;
- Linux runtime/guest agent;
- database and cache, plus the compatibility auth service when enabled;
- migrations and catalog seed;
- all-in-one backend process: API, worker loop, scheduler, surface receivers, document processor, and AgentBox manager;
- frontend;
- AgentBox PostgreSQL state and sandbox-provider smoke test;
- model profile;
- configured integrations/surfaces;
- update and backup integrity.

Each failure returns a stable code, short user message, technical detail, affected capability, retryability, and safe repair actions.

### 16.2 Repair actions

From least to most invasive:

1. Retry check.
2. Restart affected process/container.
3. Re-render configuration.
4. Re-download one corrupted artifact.
5. Recreate the Linux runtime while preserving exported volumes/data.
6. Restore the latest pre-update snapshot.
7. Reset application state while retaining user data.
8. Factory reset with explicit typed confirmation.

No repair action deletes data without naming the exact data and creating a recoverable snapshot when possible.

### 16.3 Support bundle

The bundle includes versions, manifests, health results, recent redacted logs, crash reports, port/proxy state, migration history, resource samples, and a directory-size inventory. The preview lists every included file. Secrets, prompts, document contents, database rows, OAuth tokens, and sandbox workspace files are excluded by default.

## 17. Storage, backup, restore, and uninstall

### 17.1 Storage categories

- **Data:** PostgreSQL databases (`lemma`, `lemma_datastore`, `agentbox`, and `supertokens` while retained), object store, pod files, and AgentBox durable workspace state.
- **Configuration:** non-secret settings and install state.
- **Secrets:** operating-system credential vault.
- **Runtime:** guest image, OCI images, host process packs.
- **Cache:** logs, downloads, model/document caches.

Control Center reports each category separately. “Clear cache” must not touch data.

### 17.2 Backup

- One-click encrypted backup includes data, configuration, and an encrypted secret export only if the user supplies a backup password.
- Consistent backups use `pg_dump` or a quiesced database snapshot; copying a live database volume is not acceptable.
- Automatic pre-update snapshots are retained according to a bounded policy.
- Restore validates platform version, schema compatibility, free space, and archive integrity before stopping services.

### 17.3 Uninstall

The uninstaller offers:

- Remove app only, keep local data;
- Remove app and runtime/cache, keep workspace data;
- Remove everything.

It shows exact paths and estimated sizes. Windows unregisters only the private Lemma WSL distribution. macOS removes only Lemma's runtime service and data. Existing Docker/Podman installations are never modified.

## 18. Security and privacy requirements

1. Bind all HTTP and runtime control endpoints to loopback by default.
2. Use `*.lemma.localhost` or a single loopback gateway; remove the public DNS dependency.
3. Authenticate the local control API with OS-user ACLs plus a per-install capability token. Prefer Unix domain sockets/named pipes over TCP for privileged operations.
4. Keep workspace remote/local web content in a webview with no native IPC capability. Control Center uses bundled assets and narrowly scoped commands.
5. Use the system browser, PKCE, state, nonce, and a random loopback callback for native OAuth.
6. Never include bearer, access, refresh, or session tokens in custom-scheme URLs.
7. Store provider keys, OAuth client secrets, encryption roots, and relay credentials in the OS vault.
8. Sign the desktop app, daemon, host packs, guest image, OCI images, and release manifest. Verify before activation.
9. Generate per-install database/auth/internal credentials with cryptographic randomness; do not use `postgres/postgres` or a shared development seed in production local installs.
10. Agent sandboxes receive only explicitly mounted paths and short-lived delegated tokens.
11. Public ingress is off by default and visibly indicated while active.
12. Product telemetry is opt-in. Local operational metrics remain local unless the user explicitly sends a support bundle.
13. A local-auth implementation must use established password hashing, signed/rotated session keys, CSRF protection, rate limits, secure cookie defaults, and constant-time token checks; it may reproduce only the SuperTokens endpoints the frontend and local clients demonstrably use.
14. Treat each `<slug>.apps.lemma.localhost` origin as untrusted user-authored code. Preserve per-app origin separation, restrict navigation/native IPC, validate Host headers, and allow credentialed API CORS only for syntactically valid local app hosts.

## 19. Accessibility and interaction quality

- All onboarding and Control Center functions are keyboard accessible.
- Progress is announced through accessible live regions without excessive chatter.
- Color is never the only health indicator.
- Error messages use a stable title, cause, effect, and action order.
- Long downloads show bytes, throughput, and resumability, not invented ETAs.
- Screen-reader labels name secret visibility controls and clipboard actions.
- Reduced-motion settings disable decorative splash animation.
- UI supports 200% zoom without hiding primary actions.
- Windows and macOS use platform conventions for menus, close behavior, permission prompts, and credential dialogs.

## 20. Performance and reliability targets

Targets are measured on the minimum supported hardware with no external Docker/Podman installation.

| Metric | Target |
| --- | --- |
| Base installer/application download | ≤ 200 MiB compressed |
| Core first-run download | ≤ 1.5 GiB compressed; exact release budget enforced in CI |
| Core installed disk | ≤ 3 GiB excluding user data |
| Optional workspace pack | Independently downloadable; ≤ 3 GiB compressed |
| Warm app window interactive | p50 ≤ 2 s, p95 ≤ 4 s |
| Warm core ready | p50 ≤ 5 s, p95 ≤ 10 s |
| Cold runtime core ready | p50 ≤ 15 s, p95 ≤ 30 s |
| Idle CPU after five minutes | p95 < 1% total on minimum hardware |
| Idle resident memory, core healthy | p50 ≤ 1.25 GiB, p95 ≤ 2 GiB |
| Idle runtime memory after reclaim | ≤ 512 MiB attributable to guest/runtime |
| Resume an existing sandbox | p50 ≤ 3 s, p95 ≤ 8 s |
| Configuration save/validate feedback | local validation ≤ 200 ms; network test begins immediately |
| Crash recovery | No manual cleanup after abrupt desktop or daemon termination |
| Update failure | Previous application/runtime remains bootable unless an explicitly irreversible migration began |

The release is blocked if artifact-size and idle-resource budgets regress without an approved exception.

## 21. Functional requirements

| ID | Requirement | Priority |
| --- | --- | --- |
| INST-01 | One signed installer works on a clean supported OS without developer tools. | P0 |
| INST-02 | Downloads are signed, resumable, and content-addressed. | P0 |
| INST-03 | Windows WSL enablement survives reboot and resumes onboarding. | P0 |
| INST-04 | Data location and required free space are shown before download. | P0 |
| CONF-01 | A guided, validated LLM profile flow exists in first run and Settings. | P0 |
| CONF-02 | Ollama and LM Studio are detected on loopback and models are enumerated. | P0 |
| CONF-03 | Secrets are stored in the OS vault and never returned to the webview. | P0 |
| CONF-04 | Connector OAuth and surface credentials have provider-specific guidance and tests. | P0 |
| CONF-05 | Settings declare restart scope and roll back after a failed apply. | P0 |
| LIFE-01 | Desktop and CLI use the same versioned local control API. | P0 |
| LIFE-02 | Start, stop, restart, quit, login-start, and idle policies are explicit. | P0 |
| LIFE-03 | Optional sandbox downloads do not block core use. | P0 |
| LIFE-04 | The runtime reclaims idle resources automatically. | P0 |
| APP-01 | Local Lemma runs as exactly one all-in-one backend process plus one frontend process, excluding the supervisor and guest runtime helpers. | P0 |
| APP-02 | Backend readiness reports API, worker, scheduler, AgentBox manager, surfaces, and document-processing subhealth. | P0 |
| APP-03 | A built pod app has production-parity local serving at `<public-slug>.apps.lemma.localhost`, including assets, SPA routes, SDK config, API auth/CORS, and streaming. | P0 |
| DATA-03 | AgentBox durable state uses an `agentbox` database on the managed PostgreSQL instance. | P0 |
| DIAG-01 | Health is dependency-aware and uses stable failure codes. | P0 |
| DIAG-02 | The UI offers targeted repair before reset/reinstall. | P0 |
| DIAG-03 | Users can preview and export a redacted support bundle. | P1 |
| DATA-01 | Updates create a consistent pre-migration snapshot when needed. | P0 |
| DATA-02 | Backup, restore, move-data, and tiered uninstall flows exist. | P1 |
| SEC-01 | All default listeners are loopback or OS-local IPC. | P0 |
| SEC-02 | Workspace web content has no privileged desktop IPC. | P0 |
| SEC-03 | Release artifacts and manifests are verified before activation. | P0 |
| AUTH-01 | SuperTokens remains a hidden compatibility dependency until a local-auth contract suite and migration/rollback path pass. | P0 |
| AUTH-02 | Removing SuperTokens reduces local infrastructure without changing frontend-observable auth behavior. | P1 |
| SURF-01 | Receiver mode and public-ingress requirements are explicit per surface. | P0 |
| UX-01 | Onboarding is resumable and never labels a no-model install fully ready. | P0 |
| UX-02 | Progress reports downloaded bytes and retained/resumable state. | P1 |
| UPD-01 | App/runtime packs support staged activation and rollback. | P0 |
| UPD-02 | Update policy and channel are visible and configurable. | P1 |

## 22. Success metrics

Metrics are collected only with consent and must not contain prompts, file names, connector payloads, model input/output, credentials, or workspace content.

- ≥ 90% of supported clean-machine installs reach the final readiness summary without external documentation.
- ≥ 80% of users who intend to use agents validate a model profile in the first session.
- Median time from app launch to core-ready on a new install is under five minutes on a 100 Mbps connection.
- < 5% of weekly active local users encounter an unrecovered start failure.
- ≥ 70% of recoverable service faults initiated through experiments are resolved by the first recommended repair.
- Support tickets involving Docker, Podman, Homebrew, or manual memory allocation fall to near zero for the managed runtime path.
- Idle CPU and memory stay within the release budgets for 95% of sampled sessions.

## 23. Migration from the current local stack

The redesigned desktop detects `~/.lemma/local` and current `lemma-local-*` containers.

1. Explain that a managed runtime is available and the old install will remain untouched until migration succeeds.
2. Validate current config, release manifest, database version, and free space.
3. Stop app containers, create logical PostgreSQL backups including the Lemma, datastore, AgentBox (when present), and SuperTokens databases, copy object/workspace data, and import non-secret configuration.
4. Move detected secrets into the OS vault. Do not delete the original config until the user confirms cleanup; warn that it still contains secrets.
5. Start the new stack, migrate, and run semantic checks: owner/session, pod count, record counts, object inventory, and a sandbox smoke test.
6. Keep the old containers stopped and rollback-capable for a bounded grace period.
7. Offer cleanup only after successful verification.

Docker/Podman volumes are never deleted as an implicit side effect of adopting the managed runtime.

## 24. Delivery phases

### Phase 0: Measure and slim

- Capture image sizes, startup spans, idle RSS/CPU, and current failure taxonomy.
- Replace `redis-stack` with standard Redis if the no-module audit remains clean.
- Make in-process MarkItDown the only document processor shipped by the managed local product; do not download or run Kreuzberg locally.
- Add an AgentBox lifecycle component to `standalone_app:app`, use the same PostgreSQL instance with a separate `agentbox` database, and preserve the current AgentBox HTTP contract under an internal route during migration.
- Add explicit readiness checks for model configuration and surface capabilities.

### Phase 1: Control plane and Control Center

- Introduce the versioned local daemon API while retaining current Docker/Podman providers.
- Build Control Center, config schema, OS-vault storage, provider validation, and capability health.
- Make desktop and CLI clients of the same daemon.
- Replace `sslip.io` with the localhost gateway after compatibility testing.

### Phase 2: Host application packs

- Ship one signed all-in-one backend pack containing API, worker, scheduler, AgentBox manager, surface receivers, and MarkItDown, plus one Next standalone frontend pack.
- Keep infra and sandbox workloads on the existing provider during transition.
- Gate release on cross-platform crash recovery and atomic pack activation.

### Phase 3: Managed runtimes

- macOS: ship the app-owned Virtualization.framework runtime.
- Windows: ship/import the private WSL2 distribution.
- Add the `lemma_local` AgentBox provider and remove the nested container-socket design from the managed path.
- Make Docker/Podman opt-in advanced providers.

### Phase 4: Native sandbox optimization

- Add an Apple Containerization provider on macOS 26+ after functional, networking, filesystem, resource, and upgrade parity.
- Add Windows on Arm when host packs and sandbox images are multi-architecture.
- Add optional relay/tunnel and managed/offline enterprise packaging.

### Phase 5: Lightweight local auth

- Inventory the SuperTokens Core endpoints and SDK behavior actually exercised by the frontend, backend, CLI, MCP clients, and desktop.
- Build a black-box compatibility suite against SuperTokens before implementing a replacement.
- Implement the smallest local-only Python auth provider behind the existing gateway paths, backed by the managed PostgreSQL instance and OS-vault signing roots.
- Ship behind a feature flag, migrate a copy of auth data, support rollback, and remove the SuperTokens container from new installs only after security review and compatibility gates pass.
- Keep SuperTokens supported for migrated installs until their auth data has been verified under the new provider.

## 25. Launch gates

The managed local experience is not generally available until:

- macOS and Windows clean-machine install tests run in CI or dedicated hardware automation;
- no step needs Homebrew, Node, Python, Docker, Podman, or a terminal;
- fresh install surfaces and validates AI configuration;
- built React/static pod apps pass the same serving/auth SDK journey at production and local per-app origins;
- all P0 functional requirements pass;
- abrupt-kill tests recover each process and the guest runtime;
- all-in-one backend subhealth and injected-failure tests cover API, worker, scheduler, AgentBox, surfaces, and document conversion;
- migration from the last two released local manifest versions passes with data verification and rollback;
- update interruption tests pass at download, activation, migration, and restart boundaries;
- the security review covers local control IPC, webview capabilities, OAuth handoffs, guest isolation, artifact signing, and secret redaction;
- idle resource and artifact size budgets pass on minimum hardware;
- accessibility review passes on VoiceOver and Narrator;
- uninstall is verified to leave unrelated WSL distributions and external container runtimes untouched.

## 26. Decisions requiring validation

These are bounded validation items, not unresolved product direction:

1. Confirm `*.lemma.localhost` credentialed CORS, redirects, service workers, WebSockets, and SSE in WKWebView, WebView2, Safari, Chrome, Firefox, and provider OAuth consoles. The macOS matrix rejected a parent-domain `.lemma.localhost` session cookie in WKWebView, so local auth uses a host-only cookie on `api.lemma.localhost`; every frontend authenticates through credentialed API-origin requests. A single-host path fallback may keep the core UI available, but it does not provide safe production-parity app origins; local app serving is release-blocked until the remaining supported-client matrix passes or another private per-app origin mechanism is implemented.
2. Benchmark a shared Virtualization.framework VM against Apple Containerization on macOS 26 for PostgreSQL, Redis, the compatibility auth service when enabled, and one to five sandboxes.
3. Confirm the smallest redis image supports all production-used commands; current code search finds no Redis Stack module API.
4. Choose the Windows installer format after testing WSL feature enablement, per-user background startup, and enterprise signing requirements.
5. Decide whether local owner unlock uses a password, OS biometric/keychain presence, or both. Browser-access security must remain independent of desktop-webview convenience.
6. Set final artifact and SLO budgets from baseline measurements before Phase 2 implementation.
7. Capture the exact SuperTokens compatibility surface and decide whether the saved service/RAM/startup cost justifies a local-only auth implementation after the two-process backend ships.

## 27. Definition of done

A first-time user on either supported platform can install Lemma, configure or intentionally defer an AI provider, create the local owner, open the workspace, run an agent, and open a built pod app at its isolated local subdomain without reading external infrastructure documentation. Lemma application logic runs in one all-in-one backend process and one frontend process; AgentBox control state is durable in PostgreSQL and document processing needs no sidecar. Users can later configure integrations, see resource use, diagnose a failure, update safely, back up data, and uninstall with clear data choices. Docker, Podman, Homebrew, Python, Node.js, VM sizing, container sockets, Kreuzberg, and `sslip.io` are absent from the normal journey.
