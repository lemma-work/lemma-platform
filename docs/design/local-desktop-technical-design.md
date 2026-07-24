# Lemma Desktop technical design

Status: PR #215 stabilization architecture
Companion: [product specification](local-desktop-product-spec.md)

## 1. System topology

```text
Tauri Desktop
  │ authenticated local IPC
  ▼
lemma-locald ─────────────── process ledger / network state / logs / config vault
  ├─ all-in-one Python backend (API + worker + scheduler + AgentBox + documents)
  ├─ Next.js frontend
  └─ lemma-runtime bridge
       └─ private Linux runtime
            ├─ PostgreSQL: lemma + agentbox databases
            ├─ Redis
            ├─ SuperTokens
            ├─ containerd
            └─ AgentBox workspace/function containers
```

macOS uses an app-owned Virtualization.framework VM. Windows uses a private
WSL2 distribution and places host children in a kill-on-close Job Object.

## 2. Artifact model

The public app embeds `lemma-local.json` and native control binaries only.
Host and guest runtime records contain:

```json
{
  "url": "https://…/artifact.zip",
  "sha256": "<64 lowercase hex>",
  "size": 123,
  "expanded_size": 456,
  "format": "zip"
}
```

PR test manifests replace `url` with one safe basename in `resource`; exactly
one source is allowed. `file://` is accepted only when both
`LEMMA_DESKTOP_RELEASE_MANIFEST` and
`LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS=1` select that exact source-level test
manifest.

Installation:

1. Validate manifest schema, release, target, source, digest, and sizes.
2. Sum expanded sizes and require that amount plus 4 GiB free.
3. Reuse a verified archive or resume its `.part` file with a strict
   `Content-Range`.
4. Hash the existing prefix and new bytes as they transfer.
5. Reject redirects outside HTTPS, wrong status/size/digest, archive overlap,
   path escape, duplicate entries, symlinks, and unsafe expansion.
6. Extract into `.release-pid-time.staging`; create sparse holes for zero-filled
   raw-disk chunks.
7. Validate host/guest release markers and write artifact identity.
8. Sync the completed stage and parent directory, then atomically rename.
9. Keep valid downloads across retry; delete archives only after activation.

No file inside the archive is individually fsynced.

## 3. Immutable guest and persistent data

On macOS, `lemma-vz` receives separate `--release` and `--runtime` roots.
`vmlinuz`, `initrd`, and `disk.raw` remain in the immutable release directory.
The disk is attached read-only and the kernel boots with `ro` plus volatile
system state.

`locald/runtime/macos/data.raw` is the sole sparse mutable disk. Guest mount
setup binds persistent paths for PostgreSQL, Redis, SuperTokens, containerd,
and AgentBox workspaces from that disk. Ephemeral runtime paths use tmpfs.

The build creates a 1.25 GiB maximum ext4 image, populates it with numeric
ownership preserved, shrinks it to minimum contents, adds 128 MiB headroom,
and verifies the final logical size. ZIP extraction preserves sparse zero
regions.

Windows imports the versioned root as Lemma’s private WSL distribution and
keeps persistent application state separate from replaceable release
artifacts.

## 4. Lifecycle protocol

Every mutating operation has an `operation_id`. Events include:

```json
{
  "operation_id": "shell-start-…",
  "runtime_generation": "…",
  "component": "postgres",
  "stage": "postgres",
  "current": 0,
  "total": 1,
  "bytes": false,
  "log_source": "guest"
}
```

Desktop maintains one active operation and ignores late events from older
operations. Repeated Start is informational and joins the broadcast progress.

Managed startup calls idempotent guest operations in order:

```text
runtime resolve/install
VM start
core.images
core.postgres
core.redis
core.supertokens
migrations
backend
frontend
stabilization
```

The guest operations are retained individually and `core.ensure` remains a
compatibility aggregate. Successful image/archive work is cached between
retries.

The daemon watches each child during its health gate. Exit returns immediately
with status and a redacted tail. Crash recovery retains the current runtime
generation; a new user start creates a new one. After stable Ready, transient
recovery stays in the workspace. A sustained terminal failure opens recovery
after a grace interval.

## 5. Host process contract

The host-pack manifest requires exactly:

- setup: `migrations`;
- service: `backend`;
- service: `frontend`.

The backend environment selects the all-in-one app, local auth settings,
background embedding initialization, private service addresses, dynamic local
origins, and AgentBox bridge. Frontend follows the backend dependency.

Health endpoints:

- must use loopback HTTP;
- must return status 200–299;
- must return the exact runtime generation body;
- use a configured timeout and stabilization interval.

Capability health is separate from core readiness. AI and embeddings may be
preparing/degraded without making account creation or core workspace access
unhealthy.

## 6. Ports and routing

`locald/network.json` contains:

```json
{
  "schema_version": 1,
  "frontend_port": 49152,
  "backend_port": 49153,
  "allocated_at_ms": 0
}
```

Ports are high, distinct, OS-selected, and persisted. On each daemon creation,
both must be bindable. A collision rotates the pair without signaling or
terminating the owner.

The native host-pack renderer derives:

- frontend and backend origins;
- CORS and local-app suffixes;
- SuperTokens website/API domains;
- OAuth callbacks;
- Next public API URL;
- built-app/workspace routing;
- `WORKSPACE_CALLBACK_*`;
- `FUNCTION_RUNTIME_GATEWAY_URL`;
- `host.lemma.internal`.

The same `app.lemma.localhost` hostname is used for frontend and API on
different ports to satisfy WKWebView cookie behavior. The CLI obtains endpoints
from locald status/state.

## 7. Exact process ownership

`locald/installation.id` is a random stable installation identity.
`locald/processes.json` is an atomically written private ledger:

```json
{
  "schema_version": 1,
  "installation_id": "…",
  "entries": [{
    "service_id": "backend",
    "pid": 123,
    "executable": "/canonical/path/python",
    "start_identity": "<OS creation identity>",
    "installation_id": "…",
    "runtime_generation": "…"
  }]
}
```

Before reserving ports, locald checks a prior entry against the same
installation, declared service, canonical executable, live executable, and OS
process start identity. It terminates only a complete match. Missing,
ambiguous, reused-PID, changed-executable, or foreign entries are never killed.

Normal stop and observed exits remove their entries. The stdin EOF watchdog
remains a first-line cleanup path. Windows additionally assigns setup and
service children to one Job Object with `KILL_ON_JOB_CLOSE`.

## 8. VM memory

The macOS VM ceiling is adaptive from 4 GiB to 8 GiB based on host memory.
There is exactly one traditional virtio balloon device.

The helper state machine:

- boot/initialization target: ceiling;
- `sandbox.ensure`: restore ceiling immediately;
- observed active sandboxes: retain ceiling;
- zero active sandboxes for 60 seconds: request 1.5 GiB;
- unsupported/refused request: report degraded balloon state, continue.

Guest health adds active sandbox count. Locald exposes that plus balloon state
and target. Sandbox resource admission must preserve a core-service
reservation and return capacity errors rather than induce guest OOM.

Explicit full stop shuts down the VM and releases its memory.

## 9. Diagnostics

Desktop’s diagnostic API enumerates installer, events, locald, migrations,
backend, frontend, VM, and guest sources. It opens only known paths.

An opaque cursor contains version, source file identity, and offset. If a file
rotates or truncates, the cursor safely resets to the bounded tail. Every
response reads at most 128 KiB.

Redaction covers passwords, secrets, tokens, bearer values, API keys, cookies,
and credential-bearing URLs. locald also redacts child-log excerpts before
placing them in lifecycle errors. Guest console is captured and rotated before
the VM is discarded so infrastructure failures remain diagnosable.

Logs are append-only with bounded rotation. The UI provides source tabs, live
refresh, timestamps, copy, and Open logs folder without covering action
controls.

## 10. Local authentication and configuration

Desktop injects a local context before application scripts. Local mode:

- keeps signup in the main webview;
- disables email verification;
- uses relaxed auth rate limits only for loopback local configuration;
- uses cookie-compatible same-host local origins;
- enables developer tools through an explicit UI/debug toggle.

Hosted mode retains browser handoff and production auth policy.

Operator configuration is schema validated. Secrets are stored in the OS vault.
Apply writes a candidate, probes the provider, restarts only the backend,
health-checks it, and commits; failure restores prior config and secrets.

The backend exposes safe capability health. The frontend local banner calls
the validated native `open_control_center` command with `ai`; accepted
destinations include `ai`, `connectors`/`integrations`, `surfaces`, services,
updates, and diagnostics.

## 11. Packaging workflows

`release-local-images.yml`:

- builds digest-pinned OCI images;
- builds/prunes host packs;
- builds/shrinks guest runtimes;
- writes archive sidecars and size breakdown;
- enforces 750 MiB compressed and 2.25 GiB expanded gates;
- publishes runtime assets for a release;
- on manual non-publish dispatch, builds the compressed PR test DMG.

`release-desktop.yml`:

- downloads and verifies its exact runtime manifest;
- builds native sidecars;
- signs/notarizes the online macOS app and DMG;
- signs the Windows app/helpers/NSIS installer;
- enforces the 25 MiB online application payload;
- publishes no offline artifacts.

The PR DMG is ad-hoc signed and capped at 850 MiB. It embeds compressed
archives, not expanded Python/Node/root files, and uses the production
installer path.

## 12. Verification matrix

Unit/integration coverage must include:

- manifest source/digest/size validation and resumable downloads;
- traversal/link/overlap/expansion rejection;
- sparse extraction and atomic activation;
- dynamic-port persistence and unrelated-listener rotation;
- process ledger match and PID-reuse rejection;
- strict 2xx/exact-generation health;
- immediate child-exit error;
- operation generation and navigation stability;
- configuration rollback and vault restoration;
- background/nonfatal embeddings;
- VM read-only root, data persistence, and balloon state;
- bounded rotated diagnostic cursors/redaction;
- local session retention and built-app routing;
- AgentBox bridge to the dynamic API.

Packaged E2Es and the manual PR-DMG checklist remain merge gates because source
browser tests cannot reproduce WKWebView, Finder installation, code signing,
Virtualization.framework entitlements, or WSL2 setup.
