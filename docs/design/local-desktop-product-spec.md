# Lemma Desktop product specification

Status: implementation target for PR #215
Platforms: macOS 14+ Apple silicon; Windows 11 23H2 x86-64
Product mode: local-first managed Desktop, with hosted Lemma as a connection option

## 1. Product promise

A user installs Lemma like a normal desktop application, creates a local
account, configures an AI provider, and uses the complete Lemma workspace
without installing or understanding infrastructure tools.

Lemma remains available in the tray for workers and schedules. It consumes
minimal idle resources, exposes useful progress and diagnostics, preserves
data across repair/update, and releases all resources on explicit full stop.

## 2. Release architecture

The host runs Desktop, a durable local control daemon, one all-in-one Python
backend, and one Next.js frontend. One invisible app-owned Linux runtime holds
PostgreSQL, Redis, SuperTokens, containerd, and AgentBox sandboxes.

This release deliberately retains the shared VM. Native PostgreSQL/Valkey,
backend-owned auth, Apple Containerization providers, and trusted host
AgentBox execution are future architecture options, not hidden scope in this
release.

The normal journey never presents Docker, Podman, WSL distributions, VM sizing,
container sockets, PostgreSQL, Redis, SuperTokens, or fixed ports.

## 3. User journeys

### 3.1 Install

- The public installer is signed, notarized where applicable, online, and at
  most 25 MiB installed.
- The first local start downloads version-matched host and guest archives.
- Progress names the stage and uses measured byte counts, throughput, and ETA
  only when measurable.
- Setup preflights expanded size plus 4 GiB headroom.
- Interrupted downloads resume and completed artifacts are reused.
- No public offline or air-gapped claim is made.

### 3.2 Create a local account

- Signup completes in the main Desktop webview.
- Email verification and internet abuse rate limits are disabled only for the
  local profile.
- SMTP is unnecessary.
- WKWebView sessions persist through same-host cookie-compatible local origins.
- Hosted sign-in continues to use the system browser.

### 3.3 Configure capabilities

- A missing AI profile does not block signup or non-AI workspace features.
- Authenticated local pages show **Configure an AI provider** until validation
  succeeds.
- The action opens Control Center directly to AI Providers.
- Ollama, LM Studio, OpenAI-compatible, and Anthropic-compatible profiles can
  be validated.
- Agents show an explicit unavailable reason until a profile is ready.
- Integrations, custom OAuth apps, and agent surfaces are independently
  configurable.
- Secrets are write-only and stored in the OS credential vault.

### 3.4 Daily lifecycle

- Close hides to tray; schedules continue.
- Start is single-flight and reconciles desired state.
- During install/start no misleading Start/Open/Create Account control appears.
- Stop application preserves warm infrastructure.
- Stop all releases the VM/WSL resources.
- Quit leaves the durable desired state running.
- Quit and stop Lemma stops everything before exit.
- A stable workspace is not navigated back to the installer for transient
  component recovery.

### 3.5 Diagnose and repair

- Every failure identifies component, stage, and log source.
- A child exit fails immediately with status and recent redacted output.
- Diagnostics include installer, events, locald, migrations, backend,
  frontend, VM helper, and guest/infrastructure logs.
- Log requests are cursor-based and bounded to 128 KiB.
- Repair replaces immutable runtime files and preserves the data disk.
- Destructive reset is always an explicit user choice.

## 4. Functional requirements

### Installation and packaging

1. Host and guest archives are separately versioned and SHA-256 verified.
2. Manifest metadata includes source, digest, compressed size, expanded size,
   format, platform, architecture, and version.
3. Extraction uses a disposable staging directory and atomic activation.
4. Archive traversal, links, overlap, duplicate paths, expansion bombs, and
   source downgrades are rejected.
5. Sparse root disks remain sparse when extracted.
6. A stage-level durability barrier replaces per-file fsync.
7. Hard gates are 750 MiB compressed combined, 850 MiB PR bundled resources,
   2.25 GiB expanded immutable runtime, and 1.25 GiB macOS root.
8. CI reports Python, backend assets, Node, Next, kernel, initrd, root, and OCI
   image identities.

### Startup

1. Stages are idempotent: runtime, VM, images, PostgreSQL, Redis, SuperTokens,
   migrations/backend, frontend, stabilization.
2. PostgreSQL and Redis are core prerequisites.
3. Embedding initialization is background/non-fatal.
4. Backend, frontend, and health responses carry a fresh runtime generation
   for each user start.
5. Readiness accepts only 2xx and the exact expected generation.
6. Both processes remain healthy through a stabilization interval.
7. 401, 404, 429, 503, malformed bodies, and stale processes fail readiness.

### Ports and ownership

1. The OS chooses persistent high frontend/backend ports in `network.json`.
2. All CORS, auth, OAuth, app, sandbox, frontend, backend, and CLI URLs derive
   from that pair.
3. If an unrelated listener owns a persisted port, Lemma allocates a new pair
   and never terminates the listener.
4. An ownership ledger records service, PID, canonical executable, OS start
   identity, installation identity, and runtime generation.
5. Lemma reclaims an orphan only when every persisted identity matches.
6. Windows descendants belong to a kill-on-close Job Object.

### Private runtime

1. The guest root is immutable, read-only, and attached directly from the
   active release.
2. Volatile system state uses tmpfs/volatile root state.
3. One sparse data disk stores database, cache, auth, containerd, and
   workspaces.
4. Updates replace only immutable release files.
5. One virtio balloon device supports best-effort idle reclamation.
6. No-sandbox idle target is 1.5 GiB after 60 seconds.
7. Sandbox ensure restores the adaptive 4–8 GiB ceiling immediately.
8. Unsupported ballooning degrades optimization, never core startup.
9. Sandbox admission fails clearly when requested memory plus core reservation
   exceeds capacity.

## 5. Local network contract

The workspace and API use `app.lemma.localhost` on different dynamic ports so
Safari/WKWebView treats auth cookies consistently. Built apps use
`<slug>.apps.lemma.localhost`; sandbox apps use the workspace subdomain.

AgentBox uses the explicit `host.lemma.internal` bridge. The backend does not
rewrite localhost or infer Docker topology. Production React apps continue to
use `<app-name>.apps.lemma.work`.

## 6. Security and privacy

- Native IPC is available only to trusted Desktop/control windows.
- locald uses a per-user authenticated Unix socket or named pipe.
- State directories and ledgers are private to the user.
- Runtime artifacts and image references are digest pinned.
- The VM root is read-only.
- Logs and events never expose secrets.
- Provider private-network access requires explicit trust.
- Local auth relaxations cannot affect hosted production.
- AgentBox remains isolated in the private Linux runtime.

## 7. Performance and quality targets

- cached cold core readiness: 45 seconds or less;
- warm application restart: 4 seconds or less;
- idle CPU: below 1%;
- idle guest core memory: at or below 2 GiB after ballooning;
- public installed app: at most 25 MiB;
- no terminal configuration for a fresh supported install.

## 8. Release gates

Required automated gates:

- macOS Apple-silicon fresh-install packaged E2E;
- Windows 11 WSL2 packaged E2E;
- strict health/generation/port/ownership tests;
- runtime archive integrity and size checks;
- read-only root and persistent-data tests;
- balloon active/idle transition test with nonfatal unsupported case;
- WKWebView signup/session test;
- built-app routing test;
- AgentBox-to-dynamic-API CLI operation;
- rotated bounded diagnostics test.

Before merge, a maintainer performs a clean PR-DMG install, local signup,
provider setup, AgentBox operation, restart, diagnostics, repair, and full-stop
test.

## 9. Deferred work

- true offline installation including every OCI image;
- native PostgreSQL/Valkey/auth;
- Apple Containerization or alternate AgentBox providers;
- Intel Mac, Windows Arm, and Desktop Linux releases;
- automatic public tunnel/relay;
- destructive managed-data uninstall UI.
