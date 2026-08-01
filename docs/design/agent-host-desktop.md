# Agent Host in the desktop app

How the Agent Host is supervised, controlled, and reported from Lemma Desktop.
For the sidecar's own architecture — the ACP bridge, adapters, journal, and the
device half of the API — see [`agent-host/README.md`](../../agent-host/README.md).

## What it is for

The Agent Host lets a Lemma workspace run coding agents that live on a user's
own machine: Claude Code, Codex, OpenCode, Cursor. Those agents hold the user's
own credentials and see the user's own files, so they cannot move into the
cloud. The machine reaches Lemma over outbound HTTPS and needs no inbound port.

This was originally a CLI feature. The desktop app is now the primary way to
use it, and the CLI is the headless path.

## Who supervises it

**locald, in both connection modes.** It already owns process supervision —
own process group so stopping also stops every ACP adapter, restart backoff,
log rotation — and duplicating that in the shell would risk two `serve`
processes polling the same backend and claiming the same runs.

| Mode | How locald starts | What it manages |
|---|---|---|
| Local | `ensure_locald`, as today | The whole local stack, plus the Agent Host |
| Hosted | `ensure_locald_without_host_pack` | The Agent Host only |

A hosted workspace has no local stack, so the shell never brought locald up for
it, and a cloud user therefore had no Agent Host at all. The hosted path starts
the same daemon but downloads no runtime artifacts and matches no host-pack
release — with no host pack, locald does nothing but hold its socket and
supervise the sidecar. It is started at launch only when this machine is already
paired and switched on, and otherwise lazily when the workspace page asks, so a
cloud user who never touches the feature never gets a daemon.

## Lifetime

**The Agent Host runs while Lemma is open.** Lemma lives in the tray and can
start at login, so this covers ordinary use with one rule the UI can state
plainly. There is no OS service install from the desktop app; `lemma agent-host
install-service` remains for headless machines.

Full quit stops it through the `desktop.release` handshake — the same hook that
closes an open LAN or public tunnel, because the daemon deliberately outlives
the app and cannot infer either from its own shutdown.

Quitting is **not** the user turning it off. `suspend()` stops the process and
leaves the preference alone; `stop()` is the deliberate off switch and records
it.

## The on/off preference

Persisted at `<app support>/Lemma/agent-host/supervisor.json` as
`{"enabled": bool}` — deliberately in the *agent-host* data directory, not
locald's, because the CLI supervises the same host when the app is not running
and both must agree on what the user last chose.

When the file is absent, the default is derived: **enabled if this machine is
paired**. A paired host has work waiting; an unpaired one would only idle.

## The three status planes

These do not always agree, and the difference is the whole point.

| Plane | Source | Answers |
|---|---|---|
| Process | locald's supervisor | Is the sidecar installed, and is it alive? |
| Connection | the host's own journal, via `agent-host status --json` | Is it paired, is it reaching the workspace, what work does it hold? |
| Cloud | `GET /me/runtime/agent-hosts` | Did the backend hear a heartbeat in the last 90s, and what harnesses were published? |

**The UI reports reachability, not liveness.** A running host that is unpaired,
or whose connection is down, is a live process that will never pick up a run;
reporting it as simply "on" is a lie the user discovers only when nothing
happens. So both the tray and the "This computer" card rank the planes:
not installed → off → not paired → reconnecting → connected.

locald merges the process and connection planes into `agent-host.status` and the
`agent_host` key of `control.snapshot`, caching the journal read for two seconds
so a polling page cannot fork the sidecar on every tick.

`targets[].host_id` is the join key between the local planes and the cloud one:
it is the same id `/me/runtime/agent-hosts` returns. That is how the workspace
page recognises which paired computer is the one you are sitting at, without a
new endpoint.

## Surfaces

| Surface | Scope | Purpose |
|---|---|---|
| Workspace → Models → Paired computers | local, hosted, and plain browser | The canonical surface. "This computer" card in the desktop app; cloud-only view elsewhere |
| Tray | desktop | Glanceable state, a toggle, and the log, without opening a window |
| Local settings → Runtime | local mode only | Status row, restart, log — recovery when the workspace itself will not load |

Local settings is local-mode only, so it must not be the canonical surface;
connecting, choosing agents and turning it off all live in the workspace page,
where a cloud user can reach them too.

## The privilege boundary

The workspace page is a **remote origin** to Tauri — locald serves it over
http, and the hosted build loads `lemma.work` — so it can only reach the shell
through a capability naming its URL. `capabilities/workspace.json` grants
exactly `open_control_center` plus the six `agent_host_*` commands, and nothing
that touches the local stack.

This is why the Agent Host commands cannot sit behind `require_control_window`:
that guard is what blocks a remote origin in the first place.
`require_agent_host_caller` replaces it, accepting Local settings as a trusted
bundled page, or the main webview while it is on the origin this app actually
navigated to.

Sharing republishes the same workspace on a LAN address or tunnel host. Those
are different origins, are deliberately absent from the capability, and fail the
Rust-side check too — a visitor's browser can drive the shared Lemma, but never
this Mac's Agent Host.

Because the app declares an ACL manifest (`desktop/build.rs`), *every* app
command now needs an explicit grant, including from the bundled pages. Adding a
command without adding it to a capability makes every call to it fail at
runtime, which is why `desktop/src/main.rs` tests that every registered command
is granted somewhere.

## Pairing

Inside the desktop app pairing is one click: the page mints a code through the
session it already has open and hands it straight to the bundled sidecar over
`agent-host.pair`. Nothing is displayed and nothing is copied.

The copyable commands remain for pairing a *different* machine, which is the
case they were always for. Failures are reported with the pairing code stripped:
the host quotes its argument list back on error, and one of those arguments is a
live single-use credential.
