# Host execution on Desktop

**Status:** macOS, local deployments. Windows and Linux keep VM execution.

**Related:** [Agent Host](agent-host.md#the-link) ·
[Desktop security](desktop-security.md) ·
[Sandbox provider adapters](sandbox/provider-adapters.md)

## 1. What it is for

On Desktop, every command a Lemma agent ran used to execute inside a container
in the guest VM. That is the right boundary for a Lemma shared with other
people, and the wrong one for the person whose Mac it is: their `gh` login,
git credentials, Homebrew tools and checked-out repositories are on the Mac,
and the VM cannot see any of them.

Host execution runs a user's agent commands **on their own Mac** -- the one
whose Agent Host is paired to them -- inside an OS
sandbox modelled on Claude Code's, so `gh pr create` or `npm run dev` just
works. The browser stays in the VM. The VM reaching a server the agent started
on the Mac is a separate piece, the loopback relay.

## 2. Who gets it

There is no installation owner and no privileged account. A run is routed by
the **Agent Host pairing**: a host is paired to exactly one user, and only that
user's runs are ever routed to it. A run executes on the host only when **all**
of these hold; otherwise it gets the VM sandbox, exactly as before
(`app/modules/agent/services/host_execution_selection.py`).

1. The deployment is a Desktop local install (`DEPLOYMENT_KIND=desktop`): the
   backend runs beside the hosts it routes to. A hosted deployment never routes
   a run to a machine.
2. The run acts as the conversation's user -- the workspace is theirs.
3. The run's **triggering human** is that user, in Lemma's own app
   (`triggered_by_run_user`): their message, the queued follow-up of messages
   they sent, a retry, an approval resume, an answer to a question, or a
   continuation of a run they started. A run started by a schedule or by an
   inbound channel message (Slack, email, Telegram, WhatsApp -- the platform's
   assertion, not the user's session), and every sub-agent conversation, never
   executes on the host.
4. The user the run is for has a paired Agent Host that is **online**, with
   **host execution turned on** (Settings → This Mac → Coding agents). The host
   reports this on `hello` and on every `control` as `host_execution: {enabled,
   platform, available}`; Lemma routes here only when `enabled` and
   `available` are both true. `available` is macOS with
   `/usr/bin/sandbox-exec`. The setting is `host_execution` in the Agent Host's
   `config.json`, toggled by `lemma-agent-host host-execution enable|disable`
   or locald's `agent-host.host-execution` (`{"enabled": bool}`); a running
   host notices within five seconds and says so on its next `control`, without
   a reconnect.

Somebody else on the same installation is routed to their own paired host if
they have one, and to the VM otherwise. Whoever controls a host's machine
chooses whether it runs anything at all: the switch lives on that machine.

   In the app it is the "Run commands on this Mac" switch under Settings →
   This Mac → Coding agents. The switch calls the Tauri command
   `set_host_execution`, which only the app's own window on this installation's
loopback origin may call
   (`require_local_settings_caller`) and which sends locald nothing but the
   boolean. It reads its state from the Agent Host status locald reports,
   `host_execution: {enabled, available}`, and is disabled with the reason
   when `available` is false.

This check happens once, when a run's sandbox is chosen, and the choice is
recorded on the sandbox. A run never moves between the two mid-flight. If the
host goes offline, an operation fails with a sentence the agent can act on
("This Mac is not connected"). It does not fall back to the VM, because a
command half-run in two places is worse than one that clearly did not run.

## 3. Shape

```
backend (on the Mac)                      lemma-agent-host (on the Mac)
  AgentHostSandboxProvider                  link worker
    │  op request ── Redis ──► link session ──ws `op`──►  exec relay
    │                                                        │ stdio, JSON lines
    │  ◄── Redis reply ◄── link session ◄──ws `op_ok`──      ▼
                                              lemma-agent-host exec-server
                                              one per open workspace, each under
                                              sandbox-exec + host-sandbox.sb
                                                 processes · PTYs · files
```

- **The provider** (`app/modules/workspace/providers/agent_host.py`) implements
  `SandboxProvider` and `SandboxOpsProvider`. Each operation becomes one `op`
  request to the host paired to the run's user.
- **Routing.** Only one replica holds a host's socket (newest connection wins),
  and the provider may be running on another replica. The provider publishes
  the request on the host's notice channel with a one-off reply channel. The
  session holding the socket forwards it as an `op` frame and publishes the
  answer to the reply channel. If no session picks the request up within
  `OP_PICKUP_TIMEOUT` (2 s), the host is treated as offline.
- **The exec-server** is `lemma-agent-host exec-server`, the same binary,
  spawned by the link worker under `sandbox-exec -p <host-sandbox.sb>` with
  the parameters below, **one per open workspace**. Every process it starts
  and every file it touches inherits that confinement. It speaks JSON lines on
  stdin/stdout: `{id, workspace, method, params, deadline_ms}` in,
  `{id, result}` or `{id, error: {kind, message, retryable}}` out, answered in
  whatever order ops finish. When its stdin closes it kills every process
  group it started and exits.
- **Why one per workspace.** Seatbelt fixes a process's confinement when it
  starts, and a workspace's root and granted folders are only known at
  `workspace.open`. One exec-server per host would have to be confined to the
  union of every workspace's folders -- so a command in one conversation could
  write into another's bound project -- or be restarted with wider parameters
  whenever a workspace opened somewhere new, killing every other workspace's
  running commands. One per workspace costs a process each and confines each
  to exactly its own root and grants. A `workspace.open` naming a different
  root or grants than the running exec-server was started with replaces it.
- **Restarts.** An exec-server that exits is restarted with backoff (250 ms
  doubling to 30 s) and its workspace reopened. Its processes died with it, so
  reads of them answer `process_not_found`. While it is down, ops answer
  `exec_server_unavailable` (retryable). An Agent Host restart forgets every
  open workspace: ops then answer `workspace_not_open`, and **the provider
  reopens the workspace and retries once**.

## 4. The `op` frames

Lemma → host requests. The id namespace is separate from host-originated ids;
an answer is matched by `re` against the requester's own ids only.

```json
{ "type": "op", "id": "s42", "body": { "workspace": "<uuid>", "method": "process.start", "params": { … }, "deadline_ms": 30000 } }
{ "type": "op_ok", "re": "s42", "body": { "result": { … } } }
{ "type": "error", "re": "s42", "body": { "code": "OP_FAILED", "message": "…", "retryable": false, "detail": { "kind": "not_found" } } }
```

`workspace` is the sandbox's logical id. The host maps it to its root folder
(§5) and refuses an op for a workspace it has not been told to `open`.

Binary data travels base64-encoded in `data` fields. No single frame carries
more than `OP_MAX_DATA_BYTES` (1 MiB before encoding), so large files move in
ranged chunks and no stream frames are needed.

| `method` | `params` | `result` |
|---|---|---|
| `workspace.open` | `conversation_id` (uuid \| null), `root_hint` (host folder \| null), `slug` \| null, `date` (`yyyy-mm-dd`) \| null, `grants` `[path]` (≤ 8) | `root` (host absolute path), `home`, `platform` |
| `workspace.close` | — | `{}` |
| `process.start` | `operation_id`, `shell_command` \| `argv`, `cwd`, `environment` `[{name,value}]`, `tty` `{rows,cols}` \| null, `output_limit_bytes`, `initial_input` (b64) \| null | `process_id` |
| `process.read` | `process_id`, `after_sequence`, `wait_ms` (≤ 30 000) | `chunks` `[{sequence, stream: stdout\|stderr\|pty, data}]`, `next_sequence`, `truncated_before_sequence`, `state` (`running`\|`exited`\|`killed`), `exit_code` |
| `process.input` | `process_id`, `data` | `{}` |
| `process.resize` | `process_id`, `rows`, `cols` | `{}` |
| `process.terminate` | `process_id`, `grace_ms` (default 2000) | `{}` |
| `process.list` | — | `processes` `[{process_id, command, state, exit_code, started_at}]` |
| `file.stat` | `path` | a `FileStat`: `path`, `kind` (`file`\|`directory`\|`symlink`), `size_bytes`, `modified_at` (RFC 3339), `mode` (permission bits as an integer), `sha256` (`sha256:<hex>`, files ≤ 32 MiB, else null) |
| `file.list` | `path` | `entries` `[FileStat]`, sorted by path, `sha256` always null |
| `file.mkdir` | `path` | `{}` |
| `file.read` | `path`, `offset`, `length` (≤ 1 MiB, default 1 MiB) | `data`, `eof` |
| `file.write` | `path`, `upload_id` (`[A-Za-z0-9_-]{1,64}`), `offset`, `data`, `final`, `expected_sha256` (optional, on `final`; `sha256:<hex>` or bare hex) | `{}`, or the written `FileStat` on `final` |
| `file.move` | `source`, `destination` | `{}` |
| `file.delete` | `path`, `recursive` | `existed` |
| `secret.deliver` | `path`, `data` | `{}` (written 0600, parent 0700) |

`file.write` writes each chunk to a temporary sibling. `final` verifies the
digest, `fsync`s and renames it into place, so a reader never sees a partial
file; a mismatch removes the temporary and leaves the target untouched. An
upload not written to for 5 minutes is removed. Missing parent folders are
created. `stat`, `delete` and `move` act on a symbolic link itself; the other
file ops follow it.

`process.start` is idempotent on `operation_id`: a retry after a lost answer
returns the process it already started. When an `operation_id` is given it
*is* the `process_id`, because Lemma's sandbox protocol addresses every later
op by the id it chose. `shell_command` runs under
`/bin/bash -c` (no `-l`: the environment is already the login shell's);
`argv` runs directly. Each process leads its own process group (a `tty`
process its own session), so `terminate` sends SIGTERM to the group, then
SIGKILL to whatever of it is left after `grace_ms`, children that outlived the
leader included. A process ended by a signal, or by `terminate`, reads as
`killed`.

Process output is kept in a ring per process, bounded by
`output_limit_bytes` (default 1 MiB, at most 16 MiB), with 1-based sequences
that are exclusive in `after_sequence`, as in `sandbox_runtime`.
`next_sequence` is the sequence the next chunk will get, and
`truncated_before_sequence` the first one still held once anything was
dropped. `process.read` waits up to `wait_ms` for new output, and returns at
once for a process that has exited. A process reads as exited only once its
output is complete (or 2 s after exit, for a background child holding the pipe
open). It is tracked for 60 seconds after it has exited and been read to the
end, or for 10 minutes after exit.

An op carries `deadline_ms` (default 120 s); past it the host answers
`timeout` itself. At most 32 ops run at once per link.

**Failures** use `detail.kind`, which the provider maps onto
`sandbox_runtime` errors: `not_found`, `already_exists`, `not_a_directory`,
`is_a_directory`, `permission_denied` (including a Seatbelt denial),
`outside_workspace`, `digest_mismatch`, `too_large`, `process_not_found`,
`workspace_not_open`, `exec_server_unavailable`, `timeout`, `invalid_request`
(a malformed op: unknown method, missing or mistyped parameter), `io_error`
(any other operating-system failure). `retryable` is true for
`exec_server_unavailable` and `timeout` only. The methods and kinds are listed
in `wire_contract.json` under `host_execution`, which both sides test against.

**Not offered on the host.** The provider does not declare
`ProviderCapability.PORT_REACH`. Persistent Python sessions raise
`SandboxCapabilityUnsupported` with a sentence that tells the agent to run
`python3` through `exec_command`. There is no Python runtime we can rely on on
the user's Mac.

## 5. Paths

The workspace **root** is a real host folder, and paths in ops are host
absolute paths. The provider never rewrites a command string. The agent is told
its working directory from the sandbox, not from a hard-coded `/workspace`:

- If the conversation is bound to a folder (the folder chip, or an Agent Host
  run's cwd), the root is that folder. The user's native tools and Lemma's
  tools then see the same files.
- Otherwise it is `~/lemma/c/<yyyy-mm-dd>/<conversation-slug>`, the same folder
  an Agent Host run would use: `date` and `slug` from `workspace.open`
  (`date` defaults to today, `slug` to the conversation id; the backend should
  send both so a reopen on another day finds the same folder).

**The backend naming a folder is not the user choosing it.** The host uses a
`root_hint` or a grant only if it is a folder the user bound this
conversation to on this machine (the desktop shell records those from a native
folder dialog, in `conversation-folders.json`; see `conversation_folders.rs`),
or a folder under `~/lemma` -- and never the home folder or anything
containing it. Any other `root_hint` is ignored in favour of the default root,
which `workspace.open`'s `root` reports; any other grant is dropped.

A path in a file op must resolve, after following symlinks, inside the root,
`$TMPDIR`, or a folder the user granted; otherwise `outside_workspace`. This
is the exec-server's own check, and Seatbelt enforces it again underneath.
Commands are not path-checked, only sandboxed.

## 6. The Seatbelt profile

`desktop/agent-host/resources/host-sandbox.sb`, compiled into the binary and
passed as `sandbox-exec -p`, parameterised with `-D ROOT=… -D TMP=… -D HOME=…
-D GRANT_0=… … -D GRANT_7=…` (canonical paths; Seatbelt matches
`/private/var`, not `/var`). It is modelled on Claude Code's and Codex's:
**deny by default**, then broad reads, narrow writes, open network. Deny by
default rather than allow by default, because an allowed default also allows
the ways out of a sandbox that are not files at all -- `launchctl submit`,
`open -a Terminal`, Apple Events -- so Mach services are allowed by name
(logging, directory services, DNS, TLS trust, the Keychain, FSEvents).

- **Reads:** allowed everywhere except `~/.ssh`, `~/.aws`, `~/.gnupg`,
  `~/.config/gcloud`, `~/.azure`, `~/.kube`, `~/.docker/config.json`,
  `~/.netrc`, `~/.npmrc`, `~/.pypirc`, `~/Library/Keychains`,
  `~/Library/Application Support/{Google/Chrome,Firefox,Arc,BraveSoftware}`,
  `~/Library/Mail`, `~/Library/Messages`, `~/Library/Cookies`, and Lemma's own
  data directory. The credential denials come after every allow, so a root or
  grant that contains one still cannot reach it. One exception, measured: the
  login keychain file (`~/Library/Keychains/login.keychain-db`) is readable.
  `gh` and git's osxkeychain helper open it in-process to find their item and
  securityd then decides whether to release the secret; with it denied, `gh
  auth token` answers "no oauth token found". It is encrypted with the login
  password, and the rest of `~/Library/Keychains` stays denied.
- **Writes:** the root, granted folders, `$TMPDIR`, `/tmp`, `/private/var/folders`,
  and the package-manager caches (`~/.npm`, `~/.cache`, `~/Library/Caches`,
  `~/.cargo/registry`, `~/.rustup/tmp`, `~/go/pkg/mod`, `~/.gradle/caches`,
  `~/.m2/repository`, `~/.bun/install/cache`, `~/Library/pnpm`). Also
  `/dev/null`, `/dev/tty*` and `/dev/ptmx`.
- **Network:** open, outbound and loopback. `npm install` and `npm run dev`
  need both.
- **Processes:** fork and exec are allowed. Children inherit the profile and
  cannot drop it. Setuid programs (`ps`, `sudo`) cannot run under any
  sandbox profile.
- **Environment:** a snapshot of the user's login shell (`$SHELL -lic env`,
  taken once, cached, refreshed from Settings). `LEMMA_*`, `AGENT_HOST_*`,
  `*_TOKEN`, `*_SECRET`, `*_API_KEY` and `AWS_*` are removed. `PATH` is kept
  whole, so Homebrew, nvm and asdf tools resolve as they do in the user's
  terminal.

The profile is data and is tested as data: `desktop/agent-host/tests/seatbelt.rs`
runs on a macOS runner and proves the denials and the allowances with real
processes (§8).

## 7. The rest of the run

- **Agent Host runs** (Claude Code, Codex, …) already execute on the host with
  their own tools. When host execution is on, Lemma stops offering them its
  `exec_command` and file tools. Two tools that do the same thing in the same
  folder only confuse the model. Browser, pod, connector, `ask_user`,
  `display_resource` and the rest stay.
- **The prompt** tells the agent it is on the user's Mac, names the root, and
  says the browser is a separate machine that reaches the Mac's `localhost`
  through the relay.

## 8. Tests

| Lane | What it proves |
|---|---|
| Rust unit (`make desktop-test`) | exec-server op handling, output ring and sequences, chunked write and digest, path policy including symlink escape, env scrubbing (`src/host_exec/`) |
| Rust, macOS only (`tests/seatbelt.rs`) | under the real profile, with a test-made `HOME`: `cat ~/.ssh/x` denied, `touch ~/x` denied, write in the root and `~/.npm` allowed, grants, `git init` plus a commit in the root, `curl` to loopback; and the real exec-server binary under `sandbox-exec`, driven through the relay |
| Link tests (`src/link/tests.rs`) | `op` → relay → exec-server → `op_ok` across a real WebSocket, disabled host, no handler, unopened workspace, exec-server restart, root-hint admissibility, a waiting read not blocking other ops |
| Backend unit | provider maps every op and every failure kind; selection truth table (paired user, user with no host, another user's own host, steered, inbound, host offline, toggle off, cloud); tool filtering for Agent Host runs |
| Backend e2e | the real `lemma-agent-host` binary on the link runs `exec_command` for the paired user's run on the host, and a run of a user with no host lands in the VM |

## 9. The backend half

Where each part of the above lives in `lemma-backend`, and the choices the
contract left to it.

- **Selection** (§2) is `agent/services/host_execution_selection.py`, called
  once from `build_run_context`. "Triggering human" is read from how the run
  started: a person in Lemma's own app qualifies (`user_message`,
  `queued_messages`, `manual_retry`, `approval_resume`, `person`), and so does
  a continuation of such a run (`agent_wait`, `wait_resume`,
  `message_replies`) unless a schedule started the conversation. A sub-agent
  never does, and neither does a run in a conversation bound to a channel,
  even when the channel resolved the sender to the paired user: that identity is
  the platform's assertion, not the user's session. An unknown source does
  not qualify. A Mac that cannot open the workspace at selection time gives
  the run the VM; nothing has run yet, so nothing moves.
- **Where the choice is recorded.** A host sandbox's id is a UUIDv8 tagged
  `lmhost`, derived from the conversation (`workspace/domain/host_execution.py`),
  and its instance rows record provider `agent_host`. `HostRoutingProvider`
  sends a call to the host provider only for such an id, so a host sandbox can
  never reach the VM and nothing else can reach the host. The user's VM
  workspace keeps its own id; the browser stays there.
  `sandbox_host_bindings` records the host, the root hint, `date` and `slug`,
  and the `root` the host answered.
- **`workspace.open`** is sent with `conversation_id`, `root_hint` (the folder
  an Agent Host run in the conversation last reported, else null), `date` and
  `slug` from the conversation's own `c/<date>/<slug>` directory, and no
  grants.
- **Routing** (§3). The notice is `{type: "op", op_id, reply, workspace,
  method, params, deadline_ms}` on the host's notice channel. The link session
  first claims it (`SET NX` on the op id, so two links open across a reconnect
  cannot both forward it), then publishes `{type: "picked_up"}` and, once the
  host answers, `{type: "result", result}` or `{type: "result", error: {code,
  message, retryable, kind}}` on the reply channel.
- **Capabilities.** `host_execution` from `hello` and every `control` is kept
  on the host row (`agent_hosts.capabilities`).
- **Failures.** `detail.kind` maps as in
  [provider adapters §8.3](sandbox/provider-adapters.md#83-failures);
  `host_offline` -- nothing picked the op up -- reaches the agent as "This Mac
  is not connected, so the command did not run".
- **Agent Host runs** (§7) have Lemma's `WORKSPACE_CLI` toolset withheld
  (`RunToolAssembler.assemble(host_execution="native")`), and their runtime
  prompt swaps its Runtime and Browser sections for
  `prompts/agent_host_host_execution.md`.
- **The browser.** Such a run used to drive the VM browser with
  `agent-browser` through `lemma_exec_command`, and an in-process host run's
  `exec_command` now runs on the Mac, where `agent-browser` does not exist. Both
  are given the `browser` tool instead (`tools/browser/vm_browser.py`), offered
  only when the agent has the workspace CLI: one `agent-browser` invocation per
  call in the user's VM workspace, with `exec_command`'s session and output
  shaping. The arguments are split and re-quoted, so nothing but
  `agent-browser` runs through it. On a host run `view_image` reads a path under
  `/home/user/` from the VM (where screenshots land) and any other path from
  the Mac.
- **Recorded on the run.** The choice -- `{"target": "vm"}` or `{"target":
  "host", "sandbox_id", "root"}` -- is written under `execution` in the run's
  metadata the first time its context is built. A reclaimed run reads it back
  instead of selecting again, and an approved tool executed after a pause uses
  the paused run's record, so neither can land in the VM when the run was on
  the host. A recorded host whose Mac is offline fails the op with
  `host_offline`.
- **`grants`** are always empty: the backend has no notion of folders a user
  granted. The folder chip's binding lives in the desktop shell, which the
  host reads from `conversation_id` itself.
