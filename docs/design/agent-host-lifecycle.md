# Agent Host lifecycle

## The model

**The app being open is the clock.** Everything the Agent Host needs in order to
take work is warmed once, at that moment, by one reconciliation that reports one
state. Nothing else has its own trigger, and nothing waits to be asked.

**One derived answer crosses the seam.** Internally there are several
lifecycles and that is fine — they are genuinely different concerns with
different failure modes. What leaves the subsystem is a single value: *can this
computer take work right now, and if not, why*. Every internal state maps onto it
exhaustively, in one place, and that mapping is the only thing a caller sees.

**One definition of the state machine, in one language.** The wire states exist
twice today — once in Rust, once in Python — and agree only because someone kept
them in step by hand.

**The host is the sole author of its own readiness.** It is the only component
that can observe the truth; the backend and the shell cache what it last said.
The backend may report that it has not heard from a host in 90 seconds — that is
a fact about the cache, not about the host — but it may not compose a status of
its own. The shell adds exactly one thing the host cannot say about itself: the
process is not running. Three components answering the same question, and a UI
ranking their answers, is the arrangement that produced a card reporting a dead
local stack as a live workspace's status.

**The host models transport, never conversation.** Its run states describe what
happened to a dispatch. Whether a conversation is waiting on a human is the
conversation's business, derived from the event stream by the layer that owns
it.

These three follow from the same observation. "Run Claude Code from a chat"
crosses seven lifecycles, and no component owns the composition. Each one is
individually reasonable. The bugs live between them, which is why they are hard
to see in review and why they present to the user as "it's slow" or "it's stuck"
rather than as anything nameable.

## The seven

| Lifecycle | Owner | Keyed on | Ends when |
|---|---|---|---|
| Host process | locald supervisor | data directory | app quits |
| Pairing | host config `targets[]` | workspace origin | revoked |
| Adapter cache | `adapters.rs` | adapter key + version | never (verified per launch) |
| Harness discovery | `runtime.rs` refresh loop | `refresh_generation` | republished every 15 min |
| Runtime profile | backend | harness id + org | archived |
| Run | backend dispatch + host journal | run id | terminal state |
| ACP session | `acp.rs` | session id | agent ends turn |

Seven owners, four of them durable across restarts, three of them keyed on
something the other four cannot see.

### The timers

Nine independent clocks, before the frontend adds three more:

| | |
|---|---|
| poll scan | 2s |
| harness retry | 10s |
| first-harness wait | 60s |
| harness refresh | 15 min |
| permission decision | 30 min |
| cancel grace → kill | 10s → 15s |
| shutdown grace | 30s |
| journal cleanup | 24h |
| backend heartbeat staleness | 90s |

Frontend: 2s while settling, 20s settled, 90s "arriving" window.

None of them are wrong individually. Collectively nobody can say what the worst
case is between a user installing Claude Code and being able to pick it in a
chat, because the answer is a sum over four of these that depends on which
15-minute window you landed in.

## What has to stay true

**Every state variant has a producer and a consumer.** `HarnessHealth::Installing`
existed in `protocol.rs`, had UI copy written for it in `agent-runtime-helpers.ts`,
and was emitted by nothing at all — for as long as installing finished before
anything could look. Nobody could tell, because no test asserts that the state
machine is total. A gate that walks each enum and fails on a variant with no
producer would have caught it the day it was added.

**A remedy named in an error must be reachable by the person reading it.** A
corrupt adapter cache says `run doctor --repair`. Desktop is now the only way to
install an Agent Host, the binary lives inside the bundle, and the shell exposes
no doctor surface — so the advice is a dead end for every user who can receive
it.

**Refusal has to be able to heal.** Revoking a computer invalidates its
credential server-side and the host finds out by being refused. It has no path
from there: the local target survives, every poll bounces, and the card reports
"Unreachable" indefinitely. The frontend now re-pairs around this, which fixes
the symptom at the layer least able to reason about it.

**Constants must state what they are sized against.** `FIRST_HARNESS_WAIT` is 60
seconds because "probing every adapter can genuinely take this long" — true when
probes ran in sequence with a 5s timeout each. Probes now run concurrently, so
the number is measuring something that no longer exists.

## `WaitingInput` comes out of `RunState`

The host's `RunState` is a transport state machine: queued, leased, accepted,
dispatching, running, recovering, and how the dispatch ended. `WaitingInput` is
the one conversation fact in it, and the seams show.

**The host never authors it.** There is no producer in `runtime.rs`, `acp.rs` or
`service.rs`. It exists in `protocol.rs`, in one `journal.rs` match arm that maps
it to `Checkpoint::Terminal`, and in SQL `NOT IN` lists — a variant the host must
handle and cannot emit.

**It is terminal and not terminal at once.** It sits in
`TERMINAL_AGENT_HOST_RUN_STATES` with a parenthetical explaining that it is not
really terminal, at 6 in `_RUN_STATE_ORDER` where every real terminal is 7, and
with a hand-written exemption in `run_state_progresses` letting it go back to
`RUNNING`. Three encodings of one ambiguity. It is terminal for the lease and
open for the conversation, and the name cannot say which.

**The conversation layer already has the state.** `agent_host_event_text.py`
maps it straight to `AgentEventType.WAITING`. The information is derived from
the event stream regardless; the run state is a second, weaker copy.

So the host reports that its turn ended without a final answer — the dispatch
completed, the agent stopped, nothing failed — and the conversation decides that
means it is waiting on a human. `_RUN_STATE_ORDER` becomes monotonic,
`run_state_progresses` loses its special case, and the terminal set loses its
parenthetical.

The rename is the small part. The rule it follows from is that a transport does
not get to have opinions about conversations.

## The transition

Four phases, each independently shippable and independently revertable. Nothing
here requires the others to have landed.

**0. Stop downloading the agents. — done.** The adapters reached their agent by
guessing: `codex-acp` falls back to the bare name `codex` and finds whatever is
on `PATH`, `claude-agent-acp` falls back to a copy vendored in its own package
and never consults `PATH` at all. So Lemma probed the Claude Code on this
machine, published that version, and ran a different binary carrying none of the
user's configuration — a correctness bug, not only a slow one. Adapters are now
told outright, through `upstream_path_env`, and the vendored copies are omitted
at install. 602 MB and 6,006 files becomes 50 MB.

**1. Move the remaining work onto the app-open clock, and make discovery cheap.
— partly done.** Adapter warming and detection have moved; pairing
reconciliation and the first publish have not. Detection and probing used to be
the same operation, which is why noticing a new agent cost four spawned
processes and therefore ran quarter-hourly. `installed_fingerprint` separates
them: resolving four commands is a handful of `stat` calls, cheap enough to run
every two seconds, and only a change pays for a probe. The fifteen-minute
interval stays as the safety net behind it.

**2. Collapse the seam.** One function, one enum, one reason string: the ranking
that currently lives in prose in `docs/architecture/agent-host.md` and is
re-implemented in `this-computer-status.ts`, the tray label builder, and the
harness row helpers. Three implementations of one ranking is why they disagree.

**3. Stop the wire states drifting. — done.** Generating the Rust protocol from
one definition was the ambition; a test that reads both and refuses to let them
disagree buys the same guarantee for a fraction of the change. `protocol.rs` is
parsed for its variants and compared, in both directions, against the six
`AgentHost*` enums in the committed client spec — which CI already holds to the
backend, so pinning to it pins to the backend transitively. An addition on either
side now fails the build with the two sets printed side by side.

**4. Make refusal self-healing. — done.** `HostStatus::Revoked` turned out to be
another state nothing could produce: a revoked host is refused during
authentication, before a body exists, so it gets a 401 carrying
`AGENT_HOST_REVOKED_OR_MISSING` and never a poll response. The host now
distinguishes that from an ordinary 401 — the first can never become valid again,
the second can — and drops the pairing rather than retrying it until the app
closes. The frontend workaround came out with it, restoring the host as the sole
author of whether it is paired.

**5. Split transport from conversation.** `WaitingInput` leaves `RunState`; the
conversation derives it from the events it already reads. Independent of the
other four, and the only phase that changes a wire contract — so it wants the
generated states from phase 3 landed first, or it is two hand-edits that must
agree.

## The budget

**Thirty seconds, from a supported agent appearing on disk to that agent showing
as ready in Models → Computers.**

The endpoint stops there deliberately. Adding it as a model is a human decision
and cannot be budgeted; what can be budgeted is how long the machine takes to be
honest about what it has.

Two cold paths, and only one of them is close.

### Steady state: an agent is installed on a paired machine

| Stage | Budget | Was | Now |
|---|---|---|---|
| Notice it on disk | 2s | up to 900s | 2s |
| Probe adapters | 5s | up to 20s | 5s |
| Publish to backend | 3s | ~1s | ~1s |
| Backend ingest | 1s | <1s | <1s |
| Frontend observes | 5s | up to 20s | 5s |
| **Total** | **16s** | **~940s** | **~13s** |

`HARNESS_REFRESH_INTERVAL` was 30× the entire budget on its own, and dominated
because detection and probing were one operation. Separating them is what moved
it.

Four constants were sized for the world that interval created and have been
resized to the budget: `SETTLED_REFETCH_MS` 20s → 5s (a settled list is exactly
where a new agent arrives, so twenty seconds was the common case, not the cheap
one), `ARRIVING_WINDOW_MS` 90s → 30s, `HARNESS_DISCOVERY_WINDOW_MS` 10 min →
60s, and `FIRST_HARNESS_WAIT` 60s → 15s.

A fifth went entirely. locald gave `connect` a ten-minute deadline, because
connect was not a request but an installation. It no longer installs anything,
so it takes the ordinary 45-second deadline like every other verb.

### First run: nothing cached

Was dominated by one number: the adapter cache installed **602 MB across 6,006
files**, of which 548 MB was vendored copies of the agents themselves — a 259 MB
`codex` and a 245 MB `claude` — to run code the host never invoked. It is now
50 MB and 5,995 files; the eleven files removed are the whole difference.

Installing also no longer sits on the pairing path, so the first run's remaining
cost overlaps with the user reading the screen rather than blocking the thing
they pressed.

What is *not* yet true: a machine with an existing 602 MB cache keeps it. The
integrity digest still matches, so nothing re-installs, and the saving arrives on
the next adapter version bump or a `doctor --repair`. The correctness fix does
apply immediately — `upstream_path_env` points at the user's own binary
regardless of what is sitting in `node_modules`.

## Open questions

These are the parts I do not think should be decided by whoever writes the code
first.

**Should the adapter cache be a lifecycle at all?** Adapters are pinned by
version in a committed lockfile. Shipping them inside the bundle would delete an
entire lifecycle, the npm dependency, the integrity hash, the repair path, and
the `Installing` state — at the cost of bundle size and decoupled adapter
updates. Worth pricing before optimising the machinery further.

**Does the adapter cache still need to hold 602 MB?** See the budget below; the
answer decides whether the cold path can meet it at all.
