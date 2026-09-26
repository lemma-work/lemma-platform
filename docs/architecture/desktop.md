# Lemma Desktop architecture

How the desktop app, its `lemma-locald` control plane, and the private local
runtime fit together: process ownership, the lifecycle protocol, ports, and
where state lives.

For installing and operating Desktop, see
[Install and run Lemma locally](../installation.md). For building and releasing
it, see the [Desktop maintainer guide](../../desktop/README.md).

## 1. System topology

```text
Tauri Desktop
  ├─ main workspace webview
  ├─ trusted `control` child webview (Local settings: health, recovery, diagnostics)
  │ authenticated local IPC
  ▼
lemma-locald ─────────────── process ledger / network state / logs / config vault
  ├─ lemma-agent-host sidecar (local coding agents over ACP)
  ├─ optional canonical-origin sharing gateway
  ├─ optional exact-owned ngrok or cloudflared child
  ├─ all-in-one Python backend (API + worker + scheduler + sandboxes + documents)
  ├─ lemma-frontend (Next.js behind its own server.mjs)
  └─ lemma-runtime bridge
       └─ private Linux runtime
            ├─ PostgreSQL: lemma + sandbox databases
            ├─ Redis
            ├─ SuperTokens
            ├─ containerd
            └─ workspace and function containers
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

A packaged build refuses to run from a translocated path
(`.../AppTranslocation/...`) or a mounted disk image (`/Volumes/...`) and asks to
be moved to Applications: locald, the VM helper and Start at Login are all
identified by path, and those paths change every launch.

Installation:

1. Validate manifest schema, release, target, source, digest, and sizes.
2. Reserve space for the compressed downloads, expanded sizes, and 4 GiB of
   working headroom before extraction.
3. Reuse a verified archive or resume its `.part` file with a strict
   `Content-Range`. A connection that drops, or is silent for 60 seconds
   (the header wait and every body read), is resumed automatically up to five
   times with backoff; a digest, range or client error is not retried.
4. Hash the existing prefix and new bytes as they transfer.
5. Reject redirects outside HTTPS, wrong status/size/digest, archive overlap,
   path escape, duplicate entries, symlinks, and unsafe expansion.
6. Extract into `.release-pid-time.staging`; create sparse holes for zero-filled
   raw-disk chunks.
7. Validate host/guest release markers and write artifact identity.
8. Sync the completed stage and parent directory, then atomically rename into
   a directory identified by the release and artifact digests. A same-version
   rebuild or repair gets its own directory; existing runtime trees stay in place.
9. Keep valid downloads across retry; delete archives only after staging succeeds.
10. Stop the previous runtime only after the candidate has been fully staged,
    then save the candidate binding. Retain previous releases; staging does not
    establish database compatibility or health and never authorizes pruning.

No file inside the archive is individually fsynced.

## 3. Immutable guest and persistent data

On macOS, `lemma-vz` receives separate `--release` and `--runtime` roots.
`vmlinuz`, `initrd`, and `disk.raw` remain in the immutable release directory.
The disk is attached read-only and the kernel boots with `ro` plus volatile
system state.

Both disk attachments explicitly use host caching with full synchronization,
so guest flushes retain their durability semantics. Automatic caching is
avoided on Apple Silicon; see the same disk-cache workaround in
[Lima's VZ driver](https://github.com/lima-vm/lima/blob/master/pkg/driver/vz/vm_darwin.go).

`locald/runtime/macos/data.raw` is the sole sparse mutable disk. Guest mount
setup binds persistent paths for PostgreSQL, Redis, SuperTokens, containerd,
and sandbox workspaces from that disk. Ephemeral runtime paths use tmpfs.

The guest formats the disk only when it has no filesystem signature *and* the
host says it is new. "New" is `data-disk-never-mounted` beside `data.raw`:
written before the disk is created and removed only when a boot first reaches
health, so a first boot interrupted before `mkfs` stays formattable instead of
reporting that it needs repair. At boot `e2fsck -p` repairs a dirty filesystem;
damage it declines to fix gets one `e2fsck -f -y` pass before the guest reports
`needs-repair` and the app offers a reset. The VM does not start with less than
2 GiB free on the Mac, because the sparse disk grows underneath the guest and a
full Mac fails its writes -- Postgres's among them.

The disk gives space back, as far as the host lets it. The guest mounts it
`noatime,discard`, and `lemma-guestd` runs `fstrim` on it (`core.trim`) after
a local-data reset and after every image prune, so blocks ext4 frees can be
returned as holes in the sparse `data.raw`. Whether they are depends on
Virtualization.framework passing discards through for the NVMe-attached data
disk; its headers expose no switch for it, and the guest's
`/sys/block/*/queue/discard_max_bytes` (in the guest diagnostics, non-zero
means discards are accepted) is the fact to check. When they are not, `fstrim`
answers "not supported" and `core.trim` reports `supported: false` rather than
failing.

`core.prune_images` removes container images no container uses -- stopped
ones count, since a stopped sandbox starts again from its image -- and that the
running release does not pin. locald asks for it after the first clean start of
a new release and otherwise weekly (`disk-hygiene.json` records the last one),
and This Mac's **Free up space** asks for it on demand. The guest refuses to
decide without a container listing, skips any image a pull holds a claim on,
and never passes `--force` to `rmi`, so the engine's own in-use refusal is a
second guard. The next `sandbox.ensure` that needs a removed image pulls it.

This Mac → Overview shows the data disk's allocated size (blocks, never the
24 GiB length), the pre-migration backup (§5), and the runtime releases, in
`control.snapshot`'s `disk_usage` plus the releases the app adds; the
workspace sees an allowlisted copy.

The build creates a 2 GiB maximum ext4 image, populates it with numeric
ownership preserved, shrinks it to minimum contents, and verifies the final
logical size. Boot files ship separately from the immutable root; the root
needs no space for in-place updates. ZIP extraction preserves sparse zero regions.

Windows imports the versioned root as Lemma’s private WSL distribution.
Persistent guest data currently lives inside that distribution. Replacing an
existing guest release is blocked until a data-preserving migration is available.

## 4. Lifecycle protocol

Guest readiness and host connectivity are separate startup gates. After the
guest starts PostgreSQL and Redis, locald checks their private service connections
before launching migrations or the backend. On macOS, database, cache, and auth
traffic uses Virtualization.framework's virtual sockets, independently of the
guest's NAT address. systemd owns fixed guest service listeners and its standard
socket proxy forwards each to the corresponding service. The VM helper exposes
private Unix sockets; locald publishes the assigned loopback ports for the host
backend. Each connection requires a guest acknowledgement before forwarding any
application bytes. Connections and buffers are bounded, and shutdown closes and
joins owned forwarding work. Sandbox callbacks and downloads still use normal
networking. Windows retains the private WSL service route.
This check also runs when migrations are cached. Authentication starts alongside
the backend, and all private services must be reachable before reporting ready.
Connectivity waits honor cancellation and their overall deadline. Failures name
the unavailable service and distinguish a denied connection, missing route,
refused connection, and timeout without presenting a backend traceback.
The app rejects macOS runtime packs without the matching service transport
version before launching or changing guest data; repair must install a compatible
pack. There is no silent fallback to a guest IP for internal Mac services.

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

After startup, macOS polls `core.sandbox_images_status` to prepare missing
workspace and function images. Downloads, entrypoint checks, and image repair
run outside the persistent control stream, allowing health and clock requests
to continue. Each response reports
`ready: false` until downloads and runtime-entrypoint checks complete. Polling
has a deadline, honors shutdown cancellation, and reports failed preparation
without restarting healthy infrastructure. A cache that remains corrupt after
repair still requests the existing recovery restart. WSL retains the blocking
`core.sandbox_images` request because its independent guest process exits with
the response.

The daemon watches each child during its health gate. Exit returns immediately
with status and a redacted tail. Crash recovery retains the current runtime
generation; a new user start creates a new one. After stable Ready, transient
recovery stays in the workspace. A sustained terminal failure opens recovery
after a grace interval.

Recovery is available from the welcome screen, desktop settings, and the tray,
including cloud mode and daemon failures. Restart into Recovery pauses automatic
service startup and runtime downloads. Reset Data from Recovery leaves Recovery (it
starts local services to perform the reset) and clears the workspace session
only once the daemon has accepted the reset. Force cleanup requires an app-owned
confirmation with Cancel focused. It deletes this installation's local data,
credentials, runtime downloads, Agent Host pairings and managed working folders;
external project folders and cloud data are retained. It is separate from updates
and makes no automatic backup. Failed runtime or credential cleanup preserves
its recovery records for a retry. The standalone daemon reset command requires
`--confirm=erase-local-lemma` and refuses an active control endpoint even if its
authentication token is corrupt.

Confirmations and menu errors use a bundled app overlay, with a single pending
operation and a dedicated IPC capability. Escape, Enter on the default Cancel,
and window close cancel the operation; old responses cannot authorize a later
operation. Closing the main window keeps services and the tray running. Confirmed
Quit closes daemon lifecycle admission even during startup. Startup checks for
cancellation between stages; a running migration finishes before cancellation
prevents application services from starting. Shutdown waits for active lifecycle
and Agent Host operations before stopping services, and recovery cannot admit new
work once shutdown starts. The Agent Host's desired-running preference is retained.
Background authentication and image requests use cancellable, owned bridge
processes; shutdown cancels their requests and joins their workers before stopping
the private VM. Service reconciliation and Stop are serialized so a restart cannot
leave a replacement child behind cleanup.

The shell observes shutdown under its own operation ID and ignores superseded
startup events. Slow shutdown offers an in-app choice to keep waiting or quit
with an explicit interruption/recovery warning. Repeating the Quit shortcut does
not silently take that fallback. Cleanup runs on a worker; the final event-loop exit
handler never waits on daemon I/O or process cleanup. A daemon handshake has both
a deadline and an allocation limit, including Windows named pipes.
The exit watchdog must exceed the combined sharing, handshake, graceful stop,
and verified VM/process fallback deadlines. A shorter watchdog can terminate
the cleanup worker itself and leave this installation's processes running.

Quits macOS issues itself -- Dock → Quit, log out, restart, shut down -- take
the same path. The shell adds `applicationShouldTerminate:` to tao's
application delegate, answers `NSTerminateLater`, runs the ordinary quit, and
replies once the stack is down (or the person declined). A logout, restart or
shutdown is not asked about. `AppHandle::restart` (Restart into Recovery,
restart after an update) is never treated as a quit. The daemon handles
`SIGTERM`, `SIGINT` and `SIGHUP` by running the same shutdown as
`shutdown-daemon`, so a session ending without the app still stops the VM
rather than cutting it off. A shutdown that fails part-way exits the daemon
anyway -- its admission is already closed -- and the next start reclaims what
is left by identity. "Quit Anyway" escalates the stop already requested rather
than sending a second one. A SIGTERM'd VM helper is given the guest's declared
stop budget (75s) plus a margin before it is killed, by the shell and by the
runtime manager's reclaim alike.

Only one daemon runs per installation root: `lemma-locald serve` takes an
exclusive lock on `<root>/locald.lock` before it reclaims anything, so a second
daemon exits without touching the first one's services.

## 5. Host process contract

The host-pack manifest requires exactly:

- setup: `migrations`;
- service: `backend`;
- service: `frontend`.

Setups are skipped when their recorded stamp matches. A setup may also name
environment variables in `stamp_env`, whose values in the environment it runs
with are hashed into its stamp: `connector-catalog` names
`COMPOSIO_API_KEY`, which arrives from the operator configuration rather than
the host pack, so saving, changing or removing a Composio key imports the
catalog again. A settings save re-runs that one setup beside the restarted
backend when its stamp changed, rather than waiting for the next start. On
macOS a stamp is bound to the data disk's identity (inode and birth time of `data.raw`), so a disk
that was replaced reruns its migrations instead of skipping them against an
empty database. `migrations` runs under a one-hour ceiling but is ended early
only after fifteen minutes with nothing written to its log. While it runs,
`update.json` records the `migrating` phase; a failed run stays recorded, and
the next start reports it and migrates forward again. Before migrating a
database that has been migrated before, locald takes an APFS clone of the data
disk to `runtime/macos/data.raw.before-migration` (one copy, replaced each time,
removed by a data reset) -- restoring it is a manual support step. It is kept
only as long as it can matter: locald deletes it after the first clean start
(backend healthy) of the release `schema-release` says the database was
migrated to, and at the latest three days after it was taken, dated by its
inode change time because `clonefile` copies the source's birth and
modification times. A migration `update.json` records as failed keeps it
whatever its age -- that is the case it exists for. Until then This Mac shows it
with a Delete button (`delete_update_backup`, asked natively). Its size is what
deleting it frees -- APFS's `ATTR_CMNEXT_PRIVATESIZE`, the blocks it no longer
shares with the live disk -- and "up to" its allocated size only where that
cannot be read. Measured on one installation: 16.0 GB allocated, 0.18 GB
private. `schema-release`
records the release that last completed migrations. When Alembic reports that it
cannot locate the database's revision -- data from a newer Lemma, after a
downgrade or a nightly-to-stable switch -- the start fails once, naming the
release to install, instead of retrying a generic setup error.

The backend environment selects the all-in-one app, local auth settings,
background embedding initialization, private service addresses, dynamic local
origins, and sandbox bridge. Frontend follows the backend dependency.

The pack also carries two things the hosted backend image ships, named to the
backend only when present so an older pack starts as it did: the sandbox
runtime bundle (`backend/assets/runtime-bundle`, `WORKSPACE_RUNTIME_BUNDLE_DIR`),
which the backend installs into each workspace sandbox exactly as the hosted
image's `/app/runtime-bundle` is, so a Desktop sandbox runs this release's
`lemma` CLI and SDK rather than its image's; and the `lemma` CLI for commands
on the Mac itself (`backend/bin/lemma` and `backend/cli`,
`WORKSPACE_HOST_CLI_ROOT`; see
[Host execution](desktop-host-execution.md#6-the-seatbelt-profile)).

Health endpoints:

- must use loopback HTTP;
- must return status 200–299;
- must return the exact runtime generation body;
- use a configured timeout and stabilization interval.

Capability health is separate from core readiness. AI and embeddings may be
preparing/degraded without making account creation or core workspace access
unhealthy.

### 5.1 Frontend hosting and runtime configuration

The frontend is `lemma-frontend`, the same app hosted Lemma serves, built once
per release and shipped in the host pack as Next's standalone output:

```text
frontend/
  node/                      packed Node runtime
  frontend-launcher.mjs      desktop/runtime/frontend-launcher.mjs
  lemma-frontend/
    server.mjs               the custom server locald starts (voice gateways)
    server.js                Next's generated server; not started
    server/ node_modules/ .next/ public/ content/
```

`scripts/build_local_host_pack.py` runs `LEMMA_STANDALONE=1 npm run build`
(standalone output is opt-in in `next.config.ts`) and then
`lemma-frontend/scripts/complete-standalone.mjs`, which traces `server.mjs`,
its gateways and their dependencies with Next's own tracer and copies them,
`public/` and `.next/static` into the tree. `server.mjs` recognises a
standalone tree by Next's `server.js` beside it and then loads the config Next
serialised into `.next/required-server-files.json` through
`__NEXT_PRIVATE_STANDALONE_CONFIG`, exactly as the generated server does. The
layout both sides probe is pinned by `desktop/contracts/host-pack-layout.json`.

locald starts `node frontend-launcher.mjs <server.mjs>`. Source mode
(`dev-local.sh --source`) starts `node frontend-launcher.mjs --dev
lemma-frontend`, which runs `server.mjs --dev` from the checkout. The launcher:

- refuses to start without `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_SITE_URL`,
  and defaults `NEXT_PUBLIC_AUTH_URL` to the site's `/auth`;
- writes `public/runtime-config.js`, whose body contains the runtime
  instance id — the frontend health check;
- maps locald's `HOSTNAME` to `LEMMA_FRONTEND_HOST`, so the server listens on
  loopback only. Sharing puts the gateway in front of it; the server itself
  never answers on the LAN.

Everything that differs per deployment is read when the server **starts**, not
when it is built. `GET /site-config.js` (never cached) answers
`window.__LEMMA_SITE__ = {...}` from the server's own environment, falling
back to each value's build-time `NEXT_PUBLIC_*` only when it is unset; the
root layout loads it before the app hydrates, and server rendering reads the
same values (`lemma-frontend/src/site/runtime.ts`):

| Field | Variable |
|---|---|
| `apiUrl` | `NEXT_PUBLIC_API_URL` |
| `authUrl` | `NEXT_PUBLIC_AUTH_URL` |
| `siteUrl` | `NEXT_PUBLIC_SITE_URL` |
| `sessionTokenDomain` | `NEXT_PUBLIC_SESSION_TOKEN_DOMAIN` |
| `appsDomainSuffix` | `NEXT_PUBLIC_APPS_DOMAIN_SUFFIX` |
| `deployment` | `NEXT_PUBLIC_LEMMA_DEPLOYMENT` (`local` for Desktop) |
| `analyticsKey`, `analyticsHost` | `NEXT_PUBLIC_ANALYTICS_KEY`, `NEXT_PUBLIC_ANALYTICS_HOST` |
| `desktopDownloadUrl` | `NEXT_PUBLIC_DESKTOP_DOWNLOAD_URL` (`null` when unset) |
| `voiceProvider` | `NEXT_PUBLIC_VOICE_PROVIDER` |
| `authEmailVerificationRequired` | `NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED` |
| `runtimeInstanceId` | `NEXT_PUBLIC_LEMMA_RUNTIME_INSTANCE_ID` |

So LAN or Public activation (7.2) needs no rebuild: locald restarts the
frontend with the rewritten `NEXT_PUBLIC_*` values and the next page load
reads them. `deployment: local` sends `/` and `/download` to `/t`, turns off
analytics and the consent banner, hides the Plan and Billing settings, and
hides download links. The auth portal honours `show=signup`, `redirect_uri`
and the shell's injected `window.__LEMMA_AUTH_CONFIG__`, whose
`AUTH_EMAIL_VERIFICATION_REQUIRED` wins over the site config. Fonts are
self-hosted by `next/font` at build time, so the workspace renders offline.

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

The workspace sandbox of the user this Mac's Agent Host is paired to also has
the loopback relay, which carries its
browser to a port on this Mac's own `127.0.0.1` over vsock and locald's
`run/host-loopback.sock`; see
[Desktop security](desktop-security.md#the-loopback-relay) for who has it and
which ports it refuses.

Guest-to-host callback relays own their connections in one asynchronous runtime
per listener. Admission is bounded; stopping a relay cancels and joins its
connection tasks, including idle and backpressured streams, before releasing
the runtime. An upstream half-close still allows the other direction to finish.

The backend bridge receives the runtime manager's configured WSL distribution
alongside the installation's control socket and capability file. It must not
fall back to the default distribution for a separate installation.

### 6.1 Domains

Everything is served under `lemma.localhost` (`locald/src/local_domain.rs`):

| Host | Serves |
| --- | --- |
| `app.lemma.localhost:<frontend port>` | the workspace |
| `app.lemma.localhost:<backend port>` | the API |
| `<slug>.apps.lemma.localhost:<backend port>` | a pod app, routed by `Host` in the backend |
| `app.lemma.localhost:<alias port>` | a pod app framed by the macOS workspace (below) |

One host for the workspace and the API, on two ports, so the two are one site
in every engine. `*.localhost` is loopback by resolver convention and resolved
by the webview itself, so nothing here depends on DNS or on the network being
up, no hostname leaves the machine, and every engine treats the workspace as a
secure context over plain `http` (microphone, async clipboard, `crypto.subtle`).
The session cookie is `Domain=lemma.localhost`, HttpOnly, SameSite=Lax, so it
reaches the app hosts too; apps call the API through their own origin at
`/_lemma` (`APP_API_VIA_APP_ORIGIN`, always on here). The CLI obtains endpoints
from locald status/state.

An earlier build served the public loopback wildcard `127.0.0.1.sslip.io`
instead, which needed public DNS, showed every app hostname to a third party,
and lost the secure context. It is gone; see 6.3.

### 6.2 Pod apps

The APIs return an app's canonical URL,
`http://<slug>.apps.lemma.localhost:<backend port>`, built from
`APP_BASE_DOMAIN` exactly as a hosted deployment builds `*.apps.lemma.work`.
That URL works top-level everywhere, and framed in Chromium, Edge and
WebView2, which treat `*.lemma.localhost` as one site.

WebKit does not. It derives a site from CFNetwork's list of top-level domains,
where `localhost` is not one, so every `*.localhost` *host* is its own site: an
app on `<slug>.apps.lemma.localhost` framed by `app.lemma.localhost` is
third-party and WebKit sends it no cookies. The same host on another port is
same-site. So the macOS workspace frames an **alias**:

1. The workspace, about to frame an app next to the agent, asks the shell
   (`app_frame_url`, local workspace only) for the frame address.
2. On macOS the shell asks locald (`app-alias.resolve`) and gets
   `http://app.lemma.localhost:<alias port>/<same path>`; elsewhere it returns
   the canonical URL unchanged.
3. The alias port is a loopback listener in locald (`locald/src/app_alias`)
   that forwards to `127.0.0.1:<backend port>` with `Host` rewritten to the
   canonical app host, so the backend routes it like any app request. A
   `Location` pointing at the canonical origin is rewritten to the alias; any
   other `Host` is refused (421).
4. The app's SDK has a relative `apiUrl` (`/_lemma`), so its API calls go to the
   alias origin, and the `Domain=lemma.localhost` cookie is sent: same host,
   same site.

The shell lets only alias ports it handed out load, and only in a frame: an
alias reaching the top frame sends the window back to the workspace and opens
the app in its own window, and a new-window request from one opens the
canonical app window. Alias ports live in `locald/app-aliases.json` (not
`network.json`, which an older locald would reject and answer by reallocating
the workspace's ports). One port per app host, taken from the OS when first
needed and asked for again by number on the next start, so the alias -- and
the app's origin, and its `localStorage` -- stays put; a port something else
took meanwhile is replaced. At most 16 apps hold an alias; one more evicts the
least recently used, whose listener closes. Where no alias can be had (an older
shell), the app opens in its own window, top-level and signed in. `make
desktop-app-alias-proof` proves the arrangement in WKWebView.

LAN and public sharing are unchanged: the shell does not answer a shared
origin, so a shared workspace frames canonical URLs, and alias listeners bind
loopback only.

**Duplicate session cookies.** A cookie is keyed by name, domain and path, and
this install writes the session cookies in more than one shape: host-only while
sharing is on (the overlay blanks `SESSION_COOKIE_DOMAIN`) and in releases
before the `Domain` cookie, `Domain=lemma.localhost` otherwise, and the refresh
token at SuperTokens' narrow refresh path or at `/` (`APP_API_VIA_APP_ORIGIN`).
Turning sharing on and off leaves two copies in the jar, and the browser sends
both. SuperTokens answers a refresh carrying two with a 200 that clears only the
copy at `SESSION_COOKIE_OLDER_DOMAIN` and has no `front-token` header; the
browser SDK throws on that, so a pod app, which refreshes on its first load
(its origin has no front token of its own), showed as signed out. While sharing,
the "older" domain is the live one, so that clear removed the session in use.

`DuplicateSessionCookieMiddleware`
(`identity/infrastructure/supertokens_auth/duplicate_session_cookies.py`) fixes
it on the server: any request carrying a repeated `sAccessToken` or
`sRefreshToken` gets a clear for every shape a stray could have -- host-only and
each parent domain of the `Host` and `Origin`, at every path that could have
sent it and at SuperTokens' refresh path -- except the live shape, and a clear
SuperTokens aimed at the live shape is dropped. The client retries the refresh
once when `doesSessionExist()` says no (`AuthManager.localSession`), and that
retry now carries one copy of each cookie. If the stray was the newer session,
the person signs in once; nothing loops.

### 6.3 Migrating from `127.0.0.1.sslip.io`

- locald rewrites recorded workspace and API URLs on the retired host to
  `app.lemma.localhost`, same port (`state.rs`).
- The Agent Host moves a local pairing's `base_url` the same way when it loads
  its config.
- Before the first navigation to the workspace, the shell copies every cookie
  its webview holds under `127.0.0.1.sslip.io` onto the matching
  `lemma.localhost` name and deletes the original (`cookie_migration.rs`),
  once, logged to the install log. People stay signed in.
- `localStorage` and IndexedDB are per origin and cannot be moved: the
  workspace's local preferences (open tabs, last pod, collapsed panes) reset
  once.
- `SESSION_COOKIE_OLDER_DOMAIN` stays `""`, which clears a host-only cookie an
  install from before the `Domain` cookie may still hold.

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

Tunnel ownership uses a separate private marker with installation identity,
provider, PID, canonical executable, and OS start identity. `locald` never
searches by process name and never stops an unrelated ngrok/cloudflared
process. Only one gateway/tunnel transition may run at a time.

## 7.1 Settings: This Mac and Local settings

A person changes this computer's settings in the workspace's own Settings,
under a **This Mac** group (This PC on Windows), next to *You* and the
organization. The group is drawn only in the desktop app's own window, on a
local install (`NEXT_PUBLIC_LEMMA_DEPLOYMENT=local`), and only while the page
is on this installation's loopback origin -- whoever is signed in. There is no
account check: the shell's `require_local_settings_caller` decides by where
the call comes from, and the frontend mirrors that (`thisMacAvailability`).
On a shared origin the app's window shows one line saying where the settings
are instead of controls the shell would refuse.

| Section | Owns | Data source |
| --- | --- | --- |
| Overview | One health line, Start at login, Verify & repair, Open logs, a Server setup summary, disk space (data disk, the pre-update backup with Delete, runtimes, Free up space) | `local_settings_snapshot`, `check_for_app_update`, `set_start_at_login`, `repair_runtime`, `open_logs`, `delete_update_backup`, `free_up_disk_space` |
| Server setup | One card per capability — AI model (required), Email, Connectors, Channels, Voice, Web search — each with its status, what it unlocks, a Test, and where to get its keys; then an Advanced part with state paths, addresses, log tails and anonymous install health | `local_settings_snapshot`, `apply_local_settings`, `test_server_setup`, `discover_provider_models`, `diagnostic_logs`, `telemetry_status`, `set_telemetry_enabled` |
| Coding agents | This computer's Agent Host card, Run commands on this Mac, the workspace sandbox image | `agent_host_*`, `set_host_execution`, `local_settings_snapshot`, `prepare_sandbox_image` |
| Sharing | This Mac / Local network / Public (ngrok or Cloudflare), who can join, a link to invite people | `local_sharing` |
| Updates | Current version, check, install, what the channel means | `check_for_app_update`, `install_app_update` |

**Server setup** configures what this computer's server needs a key for,
each card saving one operator section:

- **AI model** writes the `ai` section: a provider (OpenAI-compatible or
  Anthropic-compatible; presets fill the address, and Ollama or LM Studio
  answering on their default loopback ports are marked as found), its key,
  the model teammates use, an optional model that reads images, and an
  optional fast model. That profile is the backend's `system:lemma`, which it
  falls back to for a pod with no default runtime. locald also names the
  side jobs' models from it: `VISION_MODEL` (the image model, which it adds
  to the vision names, or the default model when that reads images),
  `CONVERSATION_TITLE_MODEL` (the fast model, else the default) and
  `HISTORY_SUMMARIZATION_MODEL` (the fast model, when there is one). Test lists
  the provider's models and asks the default one for a one-word answer.
- **Email** writes the `email` section (`none`, `resend` or `smtp`, a sender
  address, and the SMTP server with its password in the vault). Until it is
  set up the backend keeps its local mail spool; once it is, locald switches
  `EMAIL_TRANSPORT` to `smtp` and renders `RESEND_FROM_EMAIL` or `SMTP_*`. The
  Resend key is the channels' `surfaces.resend_api_key` — one Resend account
  carries mail in and out — so a Resend setup saves that section first. Test
  checks the key's domains and then asks the backend to email the signed-in
  person.
- **Connectors** holds the Composio key (saving one also sets
  `composio_enabled`) and this computer's OAuth apps for Google, Microsoft,
  GitHub and Slack, each showing the redirect URL to register.
- **Channels** holds Telegram's bot token and Slack's app-level token, which
  switch polling and Socket Mode on by themselves (no public address is
  needed), Resend's inbound domain, and WhatsApp and Teams, which say they
  need Public sharing.
- **Voice** is the Deepgram key for voice notes, and the voice-call keys
  (Gemini for the voice, TypeSafe for routing) that the workspace's own server
  reads: locald keeps those in the frontend's environment, never the
  backend's, and restarts only the frontend when they change; **Web search** works with no key (DuckDuckGo)
  and switches to Brave Search when a Brave key is stored.

Tests are read-only requests locald makes (`config.test`): with the typed
credential, or the stored one when nothing was typed, to the one host each
service publishes — so a stored key never goes anywhere a page chose. The AI
test follows discovery's rule: a stored key only goes to the address it was
saved for.

A new local install opens a first-run checklist of the same capabilities
once, after sign-up; everything but the AI model can be skipped, and the
Server setup entry in Settings carries a dot while the model is missing.

Organization → Models is the organization's own list. On a local install it
also suggests Ollama and LM Studio when they answer
(`discover_provider_models`, sent with an empty key so the stored provider key
never reaches a probed endpoint), and offers this install's AI model as
**Add to workspace**, which creates an organization provider and leaves the
operator profile in place. A keyed provider's key is asked for again, because
the page can only learn that one is stored.

Connectors and channels that need an OAuth app or bot credentials this
install does not have yet show **Set up on this Mac** where they fail, which
opens Server setup at that form (`lemma:open-settings` with
`{section: "this-mac-setup", focus}`); `this-mac-advanced` from an older
caller opens Server setup at its Advanced part.

The menu's Desktop settings… (⌘,) and the tray item raise
`lemma:open-settings` in the workspace when it is local, ready and on its own
origin (`settings_destination` in `desktop/src/workspace_settings.rs`), and
otherwise open **Local settings**, the bundled native page. Local settings
keeps what has to work when the workspace does not: health and the running
services (Start missing services, Restart), what is exposed with a *Return to
This computer* button, updating the app, This computer's Agent Host (the only
settings a cloud workspace has on the machine), Recovery (restart Lemma,
restart into recovery, stop, reset data, force cleanup) and Diagnostics.
Stopping sharing stays native because sharing moves the app's window to the
shared origin, where the workspace is deliberately given nothing. Page names
that moved (`ai`, `sharing`, `integrations`, `channels`, `runtime`) still
resolve, to Overview. While the background service is not answering, Overview
says so instead of drawing the last snapshot's health.

**Updates in both modes.** `updates` names the update panel: on Overview
locally, and moved under This computer in cloud mode, which has no Overview
(This Mac → Updates is local-only too). The Lemma menu's **Check for
Updates…** opens it, and opening it checks again. About 20 seconds after
launch (`schedule_launch_update_check`; not from Recovery, and only in a build
that can update itself) the shell asks the feed once; if it offers a newer
version, the tray and the Lemma menu gain **Lemma X is available — Install…**,
which opens the same panel. Nothing is downloaded from there: installing is
still `install_app_update`, which asks natively.

**Startup warnings.** What locald's start had to repair or found unfinished
— an update that stopped mid-migration (`update-interrupted`, naming the
version to install, or saying to start the one already installed), an
unreadable update record, settings writes switched off because the operation
journal could not be read, anything else it healed — is carried as
`warnings: [{code, message, version?}]` in `hello` and `control.snapshot`.
The shell narrows the list (`daemon_warnings`) into `lemma:state` for the
splash and into `local_settings_snapshot` for This Mac; Local settings shows
it above every page. Each screen adds a title and one next step (Check for
updates, or the logs/Diagnostics). The one `local.healed` broadcast went out
before any client had connected, which is why these were never seen.

### Tauri IPC commands and who may call them

Each command is granted to a webview by a capability in
`desktop/capabilities/`, and then checked again in Rust. The rules:

- **control**: `require_control_window` — the `control` webview on the
  packaged `control.html`.
- **local workspace**: `require_local_settings_caller` — the `main` webview,
  in local mode, on the origin this app navigated to, and that origin a
  shipped loopback workspace host (or the debug-only `LEMMA_DESKTOP_LOCAL_URL`)
  whose name still resolves only to loopback when the command is called.
  Refuses the hosted site, any shared LAN or tunnel origin, and a pod-app
  alias (same host, another port).

`capabilities/workspace.json` lists only the hosted site. The local workspace
is granted the same permissions at runtime on its exact origin, port included
(`local_workspace_capability`), when locald names it: a static file could only
say `http://app.lemma.localhost:*`, which would also match every alias port.
- **settings**: `require_settings_caller` — control, or local workspace.
- **agent host**: `require_agent_host_caller` — control, the splash, or the
  workspace on the origin this app navigated to: the hosted site in hosted
  mode, and in local mode only a shipped loopback workspace host (the same
  rule as local workspace), so a shared LAN or tunnel origin is refused even
  while the app's own window shows it. `agent_host_pair` and
  `agent_host_session` take the page's workspace URL only to check it: the
  shell pairs with, and reports the signed-in person to, the Lemma it itself
  navigated to (`agent_host_workspace_url`).

| Command | Granted to | Rust check | Notes |
| --- | --- | --- | --- |
| `local_settings_snapshot` | workspace | local workspace | An allowlisted view of `control.snapshot`: no install id, schema, operation ids or process details |
| `apply_local_settings` | workspace | local workspace | `config.apply` for `ai`, `email`, `integrations` or `surfaces`; replacing or removing a credential already set asks natively first (for `ai` and `email`, only their keys and passwords count) |
| `test_server_setup` | workspace | local workspace | locald `config.test`: forwards only `service`, `ai`, `api_key`, `credential` and `from_email`; writes nothing |
| `local_sharing` | workspace | local workspace | Local network, Public and opening *Who can join* ask natively first, in words built from the request; the page cannot set the consent flag |
| `set_start_at_login` | workspace | local workspace | Rebuilds the menus so the tray's check stays true |
| `set_host_execution` | workspace | local workspace | locald `agent-host.host-execution`; sends only `enabled`, asks natively before enabling, refuses to enable without Seatbelt, answers with the fresh Agent Host status. See [Host execution](desktop-host-execution.md) |
| `prepare_sandbox_image` | workspace | local workspace | |
| `delete_update_backup` | workspace | local workspace | Asks natively, naming what deleting frees, then locald `disk.cleanup` with `delete_backup`; locald refuses while a start, stop or reset holds the lifecycle |
| `free_up_disk_space` | workspace | local workspace | Removes runtime releases beyond the running one and one previous, then locald `disk.cleanup`: unused images and a trim, plus the backup only after the same native question. The request carries two booleans and nothing else |
| `open_logs`, `diagnostic_logs` | main, control, workspace | native page, or local workspace | Log tails are redacted |
| `repair_runtime` | control, workspace | settings | From the workspace it asks natively first |
| `check_for_app_update`, `install_app_update` | control, workspace | settings | Install asks natively and pins the version shown; once installed the app restarts without asking again, because the stack is already stopped and the bundle replaced |
| `telemetry_status`, `set_telemetry_enabled` | control, workspace | settings | |
| `discover_provider_models`, `configure_ai_provider` | workspace | agent host | Onboarding and the Models suggestions |
| `agent_host_*`, `sandbox_image_status`, conversation folders | workspace | agent host (folders also local mode) | See [Agent Host](agent-host.md#the-privilege-boundary) |
| `app_frame_url` | workspace | local workspace | The address to frame a pod app at: its locald alias on macOS, its own URL elsewhere. Refuses anything but this install's own apps; see §6.2 |
| `open_control_center` | main, workspace | page name validated | |
| `return_to_mode_chooser` | workspace | hosted sign-in page | Cancel on the hosted sign-in: `mode_chooser_return_allowed` refuses unless the caller is the `main` webview, the app is in hosted mode, and the page is the hosted origin's `/auth` or `/auth/…` (not `/auth/desktop`). Clears the saved mode and rebuilds the window on the splash, which shows the chooser |
| `sharing_action` | control | control | Local settings' sharing: the same request builder and native questions as `local_sharing` |
| `control_snapshot`, `agent_host_action`, `runtime_info`, `start`, `stop`, `restart`, `open_developer_tools`, `close_local_settings`, `confirm_destructive_action` | control (some also main) | control or native page | Local settings only |
| `reset_local_data`, `reset_full_reinstall`, `restart_into_recovery` | control, main | native page | Destructive: never granted to a remote origin |

`desktop/src/tests/misc.rs` holds every registered command to a grant and
every bundled page to exactly the commands it calls;
`desktop/src/tests/navigation.rs` holds the workspace grant to a closed list;
`lemma-frontend/tests/desktop-ipc.test.ts` holds the page's own list to the
grant and the registration.

### Local settings, the native page

The main Tauri window owns the remote workspace webview and creates one
full-client-size `control` child webview on demand. Creation always begins on a
worker thread before `add_child`, avoiding Tauri's synchronous child-webview
deadlock on Windows. Auto-resize follows the parent.

The child loads only the bundled `control.html` in release builds, served at
`tauri://localhost/control.html` on macOS and
`http://tauri.localhost/control.html` on Windows by Tauri's protocol handler.
Navigation and privileged IPC use the same platform-specific origin check;
lookalike domains, other ports and credential-bearing URLs are denied. Debug
builds additionally accept the exact Tauri asset server URL
`http://127.0.0.1:1430/control.html`; other hosts, ports, and paths remain
denied. Privileged commands verify both webview label and current URL.
Escape, Close, and Back to Lemma destroy the child and focus the original
workspace. Destructive actions use the trusted confirmation webview shared
with recovery and Quit, which opens with Cancel focused and traps keyboard
focus until answered.

The HTML, CSS, JavaScript modules, fonts, and icons are bundled without CDN
dependencies. Navigation is This computer; Overview, Recovery, Diagnostics.

Settings content paints immediately without a page-entry fade. A child webview
can suspend animation frames while its parent changes; starting the page at
zero opacity can leave usable controls in the accessibility tree while the
window looks blank. Native qualification checks both the visible page and its
accessibility tree, including opening settings before deployment setup.

Native credential reads, writes and removals run in a short-lived copy of
`lemma-locald`, retaining its signed identity. A bounded supervisor owns and
reaps that process on timeout; retry cannot accumulate blocked native calls.
The helper also enforces its own deadline and exits if its Unix parent dies;
Windows uses the supervisor's owned Job Object.
Requests and credential values use private stdin/stdout pipes, never command
arguments, files or logs. Malformed input fails before accessing the store;
native errors are reported without their potentially sensitive details.
The encrypted vault serializes unlock and migration. A timed-out read cannot
populate its cache. A timed-out mutation reports an uncertain outcome rather
than success: pending empty-vault initialization remains recoverable, and the
user must recheck or retry the change. No timeout resets application data.
Destructive credential cleanup stops on the first failure and retains the
installation identity and wrapping key for a deliberate retry.

The first screen recommends Lemma Cloud, with team collaboration, hosted
integrations, and cloud agents that can run while this computer is off. Its
primary button has initial keyboard focus; choosing a mode remains explicit.
It describes both deployment choices before sign-in: Lemma Cloud stores
workspace data online and can use this computer's agents; Local Lemma
stores application data and runs services on this computer. Both can send
requested data to configured providers and connectors. Agents executing on
this computer require it to remain on in either mode. Local setup requires a
separate install action; returning to the choices performs no installation.
The shell owns automatic startup on launch and mode changes. Loading or
reloading the splash only observes state, so it cannot race a second start
against the shell. Start and Retry remain explicit user actions.

On macOS, host services reach the private VM over virtio vsock, not over its
network address. PostgreSQL, Redis and SuperTokens each have a vsock port that
the guest's `lemma-service@<port>.socket` hands to `systemd-socket-proxyd` on
the guest's loopback; guestd's control channel is vsock 42411; the backend
reaches a sandbox's published ports through guestd's tunnel on vsock 42412
(`sandbox_tunnel.rs`, `desktop_tunnel.py` in the backend); and the paired
user's loopback relay comes back the other way on vsock 42413. None of these
is a connection to a device on the local network, so none is subject to macOS
Local Network privacy, which a background process cannot be prompted for.

The guest still takes a DHCP lease from vmnet, and a sandbox's reported URL
names that address -- the tunnel dials it from inside the guest. A guest with
no lease keeps serving its core services and reports
`guest_network_unavailable` for sandbox operations. The app and daemon still
carry `NSLocalNetworkUsageDescription`, but no host path depends on the
permission being granted; a guest that is unreachable is diagnosed through its
vsock health channel rather than by suspecting the permission. See
[Desktop security](desktop-security.md) for what a sandbox can reach.

## 7.2 Sharing and canonical origin

`SharingController` starts in This computer mode on every daemon launch. Its
persisted schema contains only the last provider/interface/named-tunnel/
hostname preferences. Active LAN/Public intent is never persisted.

LAN binds an OS-selected gateway port to exactly one selected private IPv4
interface. Public binds the same gateway to loopback and starts one owned
tunnel adapter. The gateway:

- streams request and response bodies without buffering, including SSE;
- preserves WebSocket upgrades and relays both directions;
- strips `/_lemma/api` before forwarding API requests to the backend;
- forwards all other paths to the frontend;
- removes client `Forwarded`/`X-Forwarded-*` headers and writes trusted values;
- preserves the external Host value for canonical-origin behavior.

Activation first starts the gateway/tunnel and discovers the final origin,
then overlays backend/frontend environments and restarts both services.
`API_URL`, frontend/auth URLs, matching `NEXT_PUBLIC_*`, exact CORS,
`/_lemma/api/st`, and cookie security all derive from that origin. Health
validation commits the transition. Failure restores the previous overlays,
restarts the previous origin, stops the attempted gateway/tunnel, and reports
both activation and rollback errors if needed.

ngrok preflight checks the executable, version, and `config check` output
without reading the token. Lemma supplies an additional app-owned agent config
with a dedicated loopback inspection port, discovers HTTPS through the local
Agent API, and validates it.

Cloudflare preflight uses `cloudflared tunnel list --output json`. After the
user completes `cloudflared tunnel login`, automatic setup creates a stable
installation-scoped tunnel using `tunnel create --output json`, writes its
generated credential directly to private app storage, and creates a DNS route
without overwriting an existing record. Provisioning metadata is persisted
before activation so partial failure is recoverable; disabling stops only the
connector and preserves the Cloudflare resource for later reuse. Existing
named tunnels with local credentials remain an advanced option. Lemma writes a
temporary ingress config that points the selected hostname at its dynamic
gateway and starts cloudflared with autoupdate disabled, loopback metrics,
bounded logs, and graceful shutdown. Quick Tunnels are intentionally absent
because they do not provide the SSE behavior Lemma requires.

Desktop disconnect/crash, interface loss, tunnel exit, or full Quit restores
This computer mode. Closing to tray leaves the Desktop connection and sharing
active.

## 8. VM memory

The macOS VM uses a fixed 4 GiB allocation and no balloon device. Readiness
polls do not change guest memory. An empty sandbox count cannot distinguish
idle time from image pulls, database initialization, migrations or shutdown.

Guest health includes the active sandbox count. Sandbox resource admission must
preserve a core-service reservation and return capacity errors rather than
induce guest OOM. Changes to the allocation require guest lifecycle and workload
qualification, including repeated startup, shutdown and existing-data checks;
see [the native guest checks](../local-runtime-vm.md).

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
Kernel Oops, bad-page and machine-check failures reject new guest work and
health checks. The host also inspects the current boot's bounded console tail
when guestd cannot answer. Recovery restarts or repairs the runtime; a kernel
crash does not request a data reset.

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

Operator configuration is schema validated. Operator secrets and the backend
encryption keyset live in `locald/credentials.enc`, encrypted with AES-256-GCM
and bound to the installation identity. One random encryption key is kept in
the OS credential vault and loaded once per daemon process. Legacy per-secret
vault items migrate on access; migration failures preserve the existing items.
Explicit removal clears both stores. A missing key or damaged encrypted file
blocks access rather than minting a replacement key or overwriting credentials.
The desktop shell serializes its own configuration writes, replaces the file
atomically, and refuses to overwrite malformed saved configuration. Window and
navigation updates cannot erase a concurrently saved runtime binding. Recovery
remains available when this file is damaged.
This Mac → Server setup sends one section per save with its expected revision;
the daemon serializes writes and rejects a stale revision with
`config-conflict`. Credentials use explicit `keep`, `replace`, and `remove`
actions and are never read back: the page only learns whether one is stored.
Reusing a saved AI key requires the same protocol and provider URL; changing
the destination requires a replacement or explicit removal.

Apply validates the provider, persists configuration, and restarts only the
backend when it is running. Reconfiguration holds crash-reconciliation ownership
without setting the global Stop flag, so the restarted backend must pass its
normal health gate. A real Stop cancels that wait and cannot be reversed by a
late restart. The frontend stays running. Failed activation restores the prior configuration
and secrets. `locald/config-operations.json` records operation IDs and outcomes
without credential values. A snapshot exposes these outcomes so settings can
recover after missing an event. A daemon restart marks unfinished writes
interrupted; it does not replay them or claim that activation succeeded.
Review the saved configuration before retrying an interrupted save. Unreadable
operation history disables settings writes while keeping other services usable.

The section payload for `config.apply` is:

```json
{
  "expected_revision": 3,
  "section": {
    "name": "integrations",
    "value": {
      "composio_enabled": false,
      "google_client_id": "",
      "microsoft_client_id": "",
      "github_client_id": "",
      "slack_client_id": ""
    }
  },
  "secrets": {"integrations.deepgram_api_key": {"action": "remove"}}
}
```

`value` is the selected section's full schema; it never includes other sections.
Valid names are `ai`, `integrations`, `surfaces` and `email`. Credential
names must belong to that section. A change that spans sections sends
`sections: [...]` instead of `section`, and restarts the backend once; its
credentials must each belong to one of the listed sections. Replacement requires a nonempty `value` alongside
`action: "replace"`.

A local model is reached the same way as any other provider: Ollama and LM
Studio prefill a loopback OpenAI-compatible endpoint that the user already
runs, so Lemma never owns, downloads, or supervises a model process.

Onboarding binds model discovery to the selected provider and credential draft.
Switching either invalidates the pending result and its model choices. A failed
apply preserves the draft with an inline error; an admitted apply freezes its
fields until completion. A saved coding agent counts as ready only while its
profile is active and the host reports it available. First-pod default selection
skips unavailable agents, and the setup banner links saved-agent failures to
Models rather than asking for an unrelated installation provider.

The backend exposes safe capability health. `open_control_center` still
accepts `ai`, `connectors`/`integrations`, `surfaces`, services and updates
from older callers, and opens Local settings at Overview for each; the
current workspace opens its own Settings instead.

## 11. Packaging workflows

`release-local-images.yml`:

- builds digest-pinned OCI images;
- builds/prunes host packs;
- builds/shrinks guest runtimes;
- writes archive sidecars and size breakdown;
- enforces 6 GiB compressed and 8 GiB expanded gates;
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
- The sandbox runtime bridge to the dynamic API.

Packaged E2Es and the manual PR-DMG checklist remain merge gates because source
browser tests cannot reproduce WKWebView, Finder installation, code signing,
Virtualization.framework entitlements, or WSL2 setup.

CI launches the app once per Desktop change: the `Desktop launch smoke` job
builds the debug bundle and runs `desktop/e2e/launch_smoke.py`, which starts
it in hosted mode, checks its WebView loads the workspace and that it brings up
locald and the Agent Host itself, and carries one conversation through a
scripted agent. GitHub's macOS runners cannot nest a VM, so local mode and the
guest are not part of it, and nothing clicks inside WKWebView. The
[Desktop test matrix](../../CONTRIBUTING.md#desktop-test-matrix) says which lane
each kind of change extends.
