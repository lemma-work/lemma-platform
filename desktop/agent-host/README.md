# Lemma Agent Host

Lemma Agent Host is the durable local boundary between a Lemma runtime and
user-owned coding agents. It lets Lemma use Codex, Claude Code, Cursor, and
OpenCode without putting a provider credential in Lemma Cloud or maintaining a
custom parser for every provider CLI.

Agent Host is intentionally a separate native process. Lemma Desktop supervises
it through `lemma-locald`; headless installations can run it in the foreground
or install a per-user operating-system service.

How Desktop supervises, controls, and reports it - including the privilege
boundary for the workspace page - is in
[Agent Host in the desktop app](../../docs/architecture/agent-host.md).

## Architecture

```mermaid
flowchart LR
    L["Lemma control plane"] <-->|"outbound HTTPS<br/>leases + durable events"| H["Lemma Agent Host"]
    H <-->|"ACP v1 over stdio"| A["Certified local agent"]
    A <-->|"run-scoped stdio MCP bridge"| M["Lemma MCP route"]
    D["Lemma Desktop"] --> LD["lemma-locald"]
    LD -->|"supervise + diagnose"| H
```

The control-plane transport is at-least-once. Before any provider side effect,
Agent Host writes the command, lease epoch, dispatch intent, checkpoints, and
event sequence to a SQLite WAL journal. The server fences every run with a lease
epoch and de-duplicates the append-only event stream. If the host dies after a
prompt may have been dispatched, the run becomes `DISPATCH_UNKNOWN`; Lemma does
not silently repeat it.

### One conversation, one provider session

A Lemma conversation maps to a single Codex/Claude Code/OpenCode session for its
whole life, so the agent keeps its own history instead of meeting the user again
on every message. Each run is a fresh process; continuity comes from the session
id, not from a held connection.

The host opens the session and journals its id with the dispatch intent.
`pending_control` puts that id on *every* checkpoint the run reports — a run has
one pending-checkpoint slot, so pinning it to a single checkpoint loses it to the
next state change. Lemma stores it against the conversation and returns it as the
next run's `resume_session_id`; the host then sends `session/load` rather than
`session/new`.

Three properties this depends on:

- **A failed load starts a new session, and the prompt brings the history.**
  Providers expire sessions on their own schedule, so a stale id is normal
  operation. Lemma only sends the latest message when it knows the agent can
  resume; when it cannot — a harness with no `loadSession`, or a session the
  provider has forgotten — the prompt carries the conversation instead, so a
  fresh session is a fresh session rather than an amnesiac one.
- **Replayed history is dropped.** `session/load` streams the whole prior
  conversation back before returning. Lemma already has those turns, so session
  updates are suppressed until this run's own prompt is dispatched.
- **The id is scoped to its harness.** A Codex rollout id means nothing to Claude
  Code, so a conversation moved between harnesses starts fresh rather than
  failing a load every turn.

Profile configuration and the model are re-applied to a resumed session exactly
as to a new one, so editing a profile still takes effect on the next turn.

## Certified integrations

The built-in adapter pack is pinned in
[`agent-adapters.lock.json`](agent-adapters.lock.json):

| Agent | Local transport | Distribution |
| --- | --- | --- |
| Codex | ACP v1 | Pinned official Codex ACP npm adapter |
| Claude Code | ACP v1 | Pinned official Claude Agent ACP npm adapter |
| Cursor | ACP v1 | Native `cursor-agent acp` |
| OpenCode | ACP v1 | Native `opencode acp` |

The release lock records exact npm adapter versions and their registry SRI
digests. The current bootstrap uses npm's own integrity verification for those
exact packages; a fully offline, Lemma-signed adapter pack remains a GA release
gate. Local upstream versions are probed and minimum supported versions are
enforced. Models and other session configuration are discovered from ACP at
runtime, so a newly available model does not require a Lemma backend release.

Headless services also search the standard Homebrew, local-bin, Volta, asdf,
mise, pnpm, Bun, Cargo, and installed NVM Node locations. Set
`LEMMA_AGENT_HOST_PATH` to an OS path list when an agent or adapter is installed
somewhere else.

## Security properties

- Every Lemma target pairing issues an independent opaque host secret,
  rotatable by re-pairing and revocable per host from the web UI or the host
  itself. The server stores only the SHA-256 hash, so a database leak exposes
  no usable credentials; locally the secret lives only in the owner-only
  `config.json` (mode 0600 on Unix).
- Pairing uses a short-lived, single-use code; the issued secret is returned
  exactly once at enrollment and all device traffic is scoped to the five
  Agent Host device endpoints.
- Target URLs require HTTPS. Plain HTTP is accepted only for an explicitly
  opted-in loopback development target.
- Provider OAuth/API credentials remain inside the provider's own local
  credential store.
- Lemma MCP credentials are encrypted at rest, scoped to one run and lease
  epoch, and exposed only to an adapter-private MCP bridge.
- Agent Host does not advertise ACP client-side filesystem or terminal
  capabilities, permission requests fail closed, and known unrestricted or
  pre-approved provider modes are filtered and rejected again at dispatch.
- Adapter subprocesses start in private scratch directories. A cancellation is
  first an ACP `session/cancel`, so the agent ends its own turn and the provider
  flushes the session file the next turn resumes from; a process-tree kill is
  the backstop for an adapter that ignores it, and for shutdown. ACP is not an
  operating system sandbox; deployments needing stronger isolation should run
  Agent Host under a dedicated OS account or sandbox policy.
- Logs contain redacted operational metadata, never host secrets, run-scoped
  bearer tokens, or MCP authorization values.

See the design document for trust boundaries, replay defenses, SSRF controls,
credential rotation, and incident response.

## Build

The crate requires Rust 1.88 or newer.

```bash
cargo build --release --locked
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --all-targets
```

The release binary is `target/release/lemma-agent-host`. Lemma's macOS and
Windows Desktop builds compile and bundle the target-triple sidecar
automatically.

## Connect a target

Lemma Desktop owns this. **Models → Connect this computer** mints a pairing code
through the session the app already holds and hands it to the bundled sidecar
over locald's private IPC — nothing is displayed and nothing is copied.

The binary's own CLI is what Desktop and locald drive, and it is available for
development and debugging:

```bash
lemma-agent-host connect \
  --url https://api.example.lemma.ai \
  --pairing-code 'one-time-code'
```

One process can connect to multiple local, self-hosted, or Lemma Cloud targets.
Each connection has an independent secret, journal state, and revocation
boundary.

## Lifecycle and diagnostics

Desktop routes every lifecycle change through locald's authenticated private
IPC, and locald refuses a competing standalone service while it owns the
lifecycle. Against a host locald is not supervising, the binary answers directly:

```bash
lemma-agent-host discover
lemma-agent-host doctor
lemma-agent-host status
lemma-agent-host refresh
lemma-agent-host drain
lemma-agent-host resume
lemma-agent-host logs --follow
```

There is no OS service install any more. `install-service` built a launchd
LaunchAgent, a systemd user unit, or a per-user Task Scheduler entry pinned to a
downloaded release — a second install channel that could run alongside Desktop's
own copy against the same pairing, speaking an older protocol. Desktop is the
supported way to keep a host running; `lemma-agent-host serve` runs one in the
foreground for containers or an external supervisor.

Disconnect revokes the remote device before deleting its local identity:

```bash
lemma-agent-host disconnect --target my-workspace
```

If a target is permanently unreachable, `--force-local` removes only the local
state. The remote device must then be revoked from Lemma separately.

## Direct adapter smoke tests

The `run` subcommand exercises the real ACP adapter and provider authentication
without connecting to Lemma or injecting tools:

```bash
lemma-agent-host run --agent codex --prompt 'Reply exactly: CODEX_OK'
lemma-agent-host run --agent claude-code --prompt 'Reply exactly: CLAUDE_OK'
lemma-agent-host run --agent opencode --prompt 'Reply exactly: OPENCODE_OK'
```

Pass `--json` for NDJSON containing the negotiated session, every ACP stream
event (including non-text content blocks), and the terminal outcome. This keeps
agent thoughts separate from assistant-message chunks and is the preferred
format for repeatable diagnostics.

This mode is intended for release qualification and local diagnosis. It creates
an isolated scratch directory under the Agent Host data directory.

## Durable local state

Default data locations:

| Platform | Location |
| --- | --- |
| macOS | `~/Library/Application Support/Lemma/agent-host` |
| Linux | `$XDG_STATE_HOME/lemma/agent-host` |
| Windows | `%LOCALAPPDATA%\Lemma\agent-host` |

`config.json` contains target metadata and the per-target host secret (the
file is owner-only). `journal.sqlite3` contains durable commands, run states,
and the event outbox; run-scoped MCP configurations are journaled with their
runs until the backend acknowledges delivery. Set
`LEMMA_AGENT_HOST_DATA_DIR` only for development or isolated test runs.

## Testing

The crate has:

- unit tests for manifest pinning, version gates, configuration, lease
  heartbeats, journal replay, stream upsert synthesis, and service
  definitions;
- a fake-process ACP end-to-end test using the official Rust ACP SDK;
- a loopback HTTP end-to-end test covering pairing, polling, harness
  publication, event replay, and self-revocation;
- backend PostgreSQL migration and full protocol tests; and
- Desktop/locald supervision tests that verify restart and full process-tree
  cleanup.

The ignored real-harness test runs the same ACP driver against authenticated
Codex, Claude Code, and OpenCode installations, then pairs a complete
`HostRuntime` with an isolated loopback control plane and dispatches one durable
Codex command through polling, the journal, event append/ack, and terminal
state reporting. It asserts that expected answers arrive in assistant-message
stream events rather than thought events:

```bash
LEMMA_REAL_AGENT_HOST_DATA_DIR=/path/to/agent-host-data \
  cargo test --test real_harness_e2e -- --ignored --nocapture
```

Set `LEMMA_REAL_AGENT_E2E_AGENTS=codex,opencode` to select a subset. These tests
are release qualification, not public CI: they require the developer's provider
accounts and spend real quota.

Codex native image generation has a separate opt-in smoke test because it spends
image-generation quota. It verifies that `$imagegen` produces a real PNG in the
explicit Agent Host artifact handoff directory:

```bash
LEMMA_REAL_AGENT_HOST_DATA_DIR=/path/to/agent-host-data \
LEMMA_REAL_AGENT_E2E_IMAGE=1 \
  cargo test --test real_harness_e2e \
  codex_native_image_generation_creates_a_publishable_artifact \
  -- --ignored --nocapture
```

## License

Lemma Agent Host is part of Lemma and is licensed under
AGPL-3.0-or-later. Certified third-party adapters retain the licenses recorded
in the locked adapter manifest.
