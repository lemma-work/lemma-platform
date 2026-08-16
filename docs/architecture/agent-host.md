# Agent Host in the desktop app

How the Agent Host is supervised, controlled, and reported from Lemma Desktop.
For the sidecar's own architecture — the ACP bridge, adapters, journal, and the
device half of the API — see [`desktop/agent-host/README.md`](../../desktop/agent-host/README.md).

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
plainly. There is no OS service install at all: Desktop compiles and supervises
the only copy of the sidecar, which is what retired the separate CLI-managed
install and its per-user launchd/systemd/schtasks job. A machine that cannot run
Desktop cannot run an Agent Host.

Full quit stops it through the `desktop.release` handshake — the same hook that
closes an open LAN or public tunnel, because the daemon deliberately outlives
the app and cannot infer either from its own shutdown.

That is the entire lifecycle the user has. **There is no off switch**, and
running is therefore not a preference anyone has to hold: it is a consequence of
the app being open, the way an open window is.

## Connecting is automatic

This computer pairs itself, once per workspace per page load, from
`lemma-frontend/lib/desktop/auto-connect.ts`, which is mounted wherever
`protected-route` is. The user is never asked to connect and cannot disconnect
the machine they are sitting at — those buttons are gone, along with the
`localStorage` flag that used to referee between them.

The attempt guard is module-level and keyed by workspace origin, not a `useRef`.
Two components on one page call the hook — the Computers card and the setup
banner — and a per-mount guard let both of them mint a pairing code, so one
machine arrived in the workspace twice and the first of the two was orphaned
offline.

The reasoning is worth keeping, because the surface reads as under-built without
it:

- **Connecting was consent that was already implied.** You are signed in, on
  this machine, in an app that supervises the sidecar itself. Pressing a button
  to agree to what you already arranged is ceremony, and it read as *broken* —
  pairing takes a moment and the harness scan takes longer, so pressing it
  looked like nothing, then nothing, then "no agents found".
- **Disconnecting this computer could not be honest.** The next authenticated
  page pairs it straight back, so the button only worked while a flag remembered
  you meant it — and that flag was a sixth state plane, kept per-origin, that
  nothing else in the system could see. Turning the host *off* set the same flag,
  collapsing "pause this laptop" and "never auto-pair me" into one bit.
- **The only real "no" is removing a machine you are not at**, and that already
  had a durable home: `agent.host.revoke` sets `revoked_at`, and the poll
  endpoint refuses a revoked host. It sticks because the machine is not there to
  re-pair itself. The Remove control is hidden on this computer's own card for
  exactly that reason.

Hosted workspaces connect the same way. The gate used to be
`isLocalDeployment()`, which left the cloud user — the one whose laptop and
workspace are genuinely in different places — as the only person still pressing
buttons.

### What pairing does and does not grant

This is what makes an automatic connection safe to have, and it is a property of
the backend rather than of the UI, so it is worth stating where it can be
checked.

**A paired computer belongs to the person who paired it.** `AgentHostModel`
carries a `user_id` and no organization at all. `GET /me/runtime/agent-hosts` is
`/me`-scoped, so nobody else in the workspace can even list the machine.

**Pairing alone dispatches nothing.** A workspace reaches an agent only through a
runtime profile, and `AgentRuntimeProfileService.require_ready_harness` resolves
the host through `get_for_user(host_id, user_id)` — so only the owner can bind
one. Creating that profile is the deliberate act, it defaults to
`RuntimeProfileScope.PERSONAL`, and deleting it is the undo.

So the consent boundary is the profile, not the pairing. Which is why there is
no connect/disconnect: it would be ceremony in front of a step that grants
nothing, standing in for a decision that is made one screen later.

**"Paired" means paired to the workspace on screen**, judged from this machine's
own `targets` by origin. Not the backend's host list: the host learns about a
revocation by being *refused*, and it drops the target itself when Lemma refuses
it repeatedly — so the target stops existing and the ordinary "not paired here"
path re-pairs. Repeatedly, because `AGENT_HOST_REVOKED_OR_MISSING` is also what a
host pointed at the wrong backend gets, and one refusal is not enough to destroy
a pairing over.

The one thing that cannot be made silent is macOS's file-access prompt, raised
by an adapter's own binary the first time it probes. Connecting early at least
puts it in front of someone who is still in setup.

Failure is reported rather than hidden. A pairing that cannot complete says so on
the card, with a Try again that clears the attempt guard — the guard exists so a
machine that cannot pair does not mint pairing codes in a loop, not to refuse
someone who asked.

## The three status planes

These do not always agree, and the difference is the whole point. They are an
internal distinction: the UI ranks them into a single reported state.

| Plane | Source | Answers |
|---|---|---|
| Process | locald's supervisor | Is the sidecar installed, and is it alive? |
| Connection | the host's own journal, via the sidecar's `status --json` | Is it paired, is it reaching the workspace, what work does it hold? |
| Cloud | `GET /me/runtime/agent-hosts` | Did the backend hear a heartbeat in the last 90s, and what harnesses were published? |

**The UI reports reachability, not liveness.** A running host that is unpaired,
or whose connection is down, is a live process that will never pick up a run;
reporting it as simply "on" is a lie the user discovers only when nothing
happens. So both the tray and the "This computer" card rank the planes. The card
ranks them, in `describeThisComputer`:

not available → connecting → starting → connected → unreachable → reconnecting

with "couldn't connect" displacing *connecting*, and the tray adds "not starting"
for a sidecar whose spawn failed. Connecting comes before starting because the
card asks the more specific question first: a machine with no pairing for *this*
workspace is not connected to it whatever the process is doing.

Every one of those is a report, never a prompt. "Off" used to sit in that
ranking and was the only rung that needed a user to act; with the switch gone,
"installed but not running" and "running but not paired here" are both stages of
a connection on its way up, and they say so.

**A stage a thing cannot leave is a failure wearing its clothes.** Three of these
were introduced without one — an adapter whose install failed still said
"Setting up", a sidecar that could not spawn still said "starting", a pairing
that threw still said "Connecting" — and each was indistinguishable from progress
for as long as the app stayed open. Every optimistic state on this surface owes
the reader a way to stop being optimistic.

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
| Workspace → Models → Computers | local, hosted, and plain browser | The canonical surface. "This computer" card in the desktop app; cloud-only view elsewhere |
| Tray | desktop | Glanceable state and the log, without opening a window |
| Local settings → Runtime | local mode only | Status row, restart, log — recovery when the workspace itself will not load |

Local settings is local-mode only, so it must not be the canonical surface;
choosing which agents this workspace may use lives in the workspace page, where a
cloud user can reach it too. That choice is now the *only* decision on the page —
everything else about this computer connects itself and reports.

The tray line is a disabled label. Its "Turn Agent Host On/Off" item went with
the switch, which also removed the mirrored `running` flag on `Shell::ui` that
existed only to tell the toggle which way to point.

## The privilege boundary

The workspace page is a **remote origin** to Tauri — locald serves it over
http, and the hosted build loads `lemma.work` — so it can only reach the shell
through a capability naming its URL. `capabilities/workspace.json` grants
exactly `open_control_center` plus five `agent_host_*` commands, and nothing
that touches the local stack.

Note what those five *cannot* do. `agent_host_start` has no counterpart, and
`agent_host_unpair` is gone: the workspace can ask this computer to be running,
to pair, and to look for agents again — never the reverse. A remote off switch
and an automatic connection would have spent their lives undoing each other, and
the grant is narrower for not having both.

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

Pairing is no clicks: the page mints a code through the session it already has
open and hands it straight to the bundled sidecar over `agent-host.pair`.
Nothing is displayed and nothing is copied.

The pairing it looks for is **this workspace's**, not any pairing at all. A Mac
paired to its own local stack and then opened against a hosted workspace needs a
second one, and `status.paired` — "paired to something" — said it was already
done. `selectWorkspaceTarget` answers the narrower question, and both the card
and the automatic connection go through it so they cannot disagree.

A *different* machine pairs the same way — install Desktop there and sign in —
so there is no code to carry and no copyable command to get wrong. Failures are
still reported with the pairing code stripped: the host quotes its argument list
back on error, and one of those arguments is a live single-use credential.

## The lifecycles underneath

Seven of them sit between typing in a chat and Claude Code answering, and no one
component owns the composition. They are genuinely different concerns with
different failure modes, so they are not going to collapse into one — but the
bugs live between them, which is why they present to a user as "it's slow" or
"it's stuck" rather than as anything nameable.

| Lifecycle | Owner | Keyed on | Ends when |
|---|---|---|---|
| Host process | locald supervisor | data directory | app quits |
| Pairing | host config `targets[]` | workspace origin | revoked |
| Adapter cache | `adapters.rs` | adapter key + version | never (verified per launch) |
| Harness discovery | `runtime.rs` refresh loop | `refresh_generation` | republished on change |
| Runtime profile | backend | harness id + org | archived |
| Run | backend dispatch + host journal | run id | terminal state |
| ACP session | `acp.rs` | session id | agent ends turn |

What has to stay true across them:

**One derived answer crosses the seam.** What leaves the subsystem is a single
value — can this computer take work right now, and if not, why — mapped from the
internal states exhaustively. The ranking above is that mapping.

**The host is the sole author of its own readiness.** It is the only component
that can observe the truth; the backend and the shell cache what it last said.
The backend may report that it has not heard from a host recently — a fact about
the cache, not about the host — but it may not compose a status of its own. The
shell adds exactly one thing the host cannot say about itself: the process is not
running. Three components answering the same question, with a UI ranking their
answers, is the arrangement that produced a card reporting a dead local stack as
a live workspace's status.

**The host models transport, never conversation.** Its run states describe what
happened to a dispatch. `RunState::WaitingInput` is the one exception and should
not be: nothing in the host authors it, it sits in the terminal set *and* carries
a hand-written exemption out of it, and the conversation layer already derives
the same fact from the event stream. Removing it needs a migration, because it is
persisted on both sides.

**Every state variant has a producer.** `HarnessHealth::Installing` had UI copy
written for it and was emitted by nothing for as long as installing finished
before anything could look. `HostStatus::Revoked` is unreachable on the poll path
because authentication fails before a response body exists — the host learns it
from a 401 instead. Nothing fails when a variant has no producer; it just quietly
never happens.

**A remedy named in an error must be reachable by the person reading it.** A
corrupt adapter cache says `run doctor --repair`, and Desktop exposes no doctor
surface — so the advice is a dead end for every user who can receive it.

### Timing

Two clocks decide how long "install an agent, use it in a chat" takes, and both
are written where they are used rather than inferred:

- `DISK_SCAN_INTERVAL` — how often the supervisor asks whether the agents on this
  machine changed. Affordable because detection is no longer probing: resolving
  four commands is a handful of `stat` calls, not four spawned processes. One
  sweep for the process, announced to every worker over a `watch` channel.
- `POLL_HOLD` — how long Lemma holds a poll open. The worker loop spends
  essentially all of its time inside one, so **anything that must happen sooner
  needs its own arm in that select**. A check placed at the top of the loop runs
  once per held poll however short its own interval says it is, which is how a
  two-second scan came to answer in up to twenty-five.

`HARNESS_REFRESH_INTERVAL` is the safety net behind the scan, not the mechanism.
