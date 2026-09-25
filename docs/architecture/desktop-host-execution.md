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
on the Mac is a separate piece, the
[loopback relay](desktop-security.md#the-loopback-relay): the VM browser of
the user this Mac's Agent Host is paired to asks for `localhost:3000`, and when nothing in the sandbox serves it
the request reaches port 3000 on the Mac's own loopback.

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

A user with more than one usable host is routed to the one the conversation's
most recent host run used, and otherwise to the most recently seen. Somebody
else on the same installation is routed to their own paired host if
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

This check happens once, when a run's sandbox is chosen, and the choice --
VM, or which host and which folder -- is recorded on the run. A run never
moves between the two mid-flight, nor between two Macs. If the
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
  group it started and exits. It also reports each group it starts to the
  relay (a `group` field the relay strips from `process.start`'s answer), and
  when an exec-server's output ends -- it exited, crashed, or was killed after
  its stop grace -- the relay, outside the sandbox, kills whatever of those
  groups is left. The relay does the same for every workspace when the link
  worker that owns it goes away. A crash of the Agent Host itself closes each
  exec-server's stdin, which stops them in turn.
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
dropped. One read carries at most `OP_MAX_DATA_BYTES` of output (at least one
chunk); when it stops short, `next_sequence` is the first chunk it left out
and the process still reads as `running`, so a reader carries on.
`process.read` waits up to `wait_ms` for new output, and returns at once for
a process that has exited. A process reads as exited only once its reader has
all of its output (or 2 s after exit, for a background child holding the pipe
open). It is tracked for 60 seconds after it has exited and been read to the
end, or for 10 minutes after exit -- and never forgotten while anything in its
process group is still running, so `workspace.close` still reaches a server a
command left in the background. A `process.start` is checked, spawned and
registered in one step, so two tries of one `operation_id` start one process.

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

**The Mac remembers the folder.** It owns the disk, so the host -- not Lemma --
keeps which root each conversation's workspace opened in
(`conversation-roots.json` beside the Agent Host's `config.json`, written by
the host; `host_exec/roots.rs`). An open with a `conversation_id` chooses, in
order:

1. `root_hint`, when it is the folder the user bound this conversation to (the
   user choosing, now);
2. the root this conversation opened in before, while it is still admissible
   (below) -- a default folder that was deleted is made again;
3. `root_hint`, when it is a folder under `~/lemma`;
4. the default folder above;

and records what it chose. So a re-open -- after the host restarted and forgot
every open workspace, or by a later run -- lands in the folder the
conversation already works in whatever day, slug or `~/lemma` hint Lemma
sends. The `root` in the answer stays authoritative; Lemma stores nothing about
folders but the root a run recorded (§9).

**The backend naming a folder is not the user choosing it.** The host uses a
`root_hint` or a grant only if it is a folder the user bound this
conversation to on this machine (the desktop shell records those from a native
folder dialog, in `conversation-folders.json`; see `conversation_folders.rs`),
or a folder under `~/lemma` that this paired workspace already owns in the
directory registry (`~/lemma/.lemma/directories.sqlite3`, the one Agent Host
runs claim their folders in; `conversation_directory.rs`) -- never a hidden
folder such as the registry's own, never the home folder or anything
containing it. The root a workspace opens in under `~/lemma` -- the default
folder included -- is claimed for this paired workspace as it opens, and one
another paired workspace owns is refused (`permission_denied`) rather than
shared. Any other `root_hint` is ignored in favour of the default root,
which `workspace.open`'s `root` reports; any other grant is dropped.

A path in a file op must resolve, after following symlinks, inside the root,
`$TMPDIR`, or a folder the user granted; otherwise `outside_workspace`. This
is the exec-server's own check, and Seatbelt enforces it again underneath.
Commands are not path-checked, only sandboxed.

## 6. The Seatbelt profile

`desktop/agent-host/resources/host-sandbox.sb`, compiled into the binary and
passed as `sandbox-exec -p`, parameterised with `-D ROOT=… -D HOME=… -D TMP=…
-D USER_TMP=… -D CACHE=… -D GRANT_0=… … -D GRANT_7=…`, plus `GIT_0…GIT_8` and
`RESOLVED_0…RESOLVED_15` (below), all canonical paths (Seatbelt matches
`/private/var`, not `/var`). It is modelled on Claude Code's and Codex's:
**deny by default**, then broad reads, narrow writes, open network. Deny by
default rather than allow by default, because an allowed default also allows
the ways out of a sandbox that are not files at all -- `launchctl submit`,
`open -a Terminal`, Apple Events -- so Mach services are allowed by name
(logging, directory services, DNS, TLS trust, the Keychain, FSEvents).

The rule behind every write denial: **nothing a command writes may be run
later, unconfined, without the user asking for it.** The user's own terminal,
editor, git and the coding agents the Agent Host starts all run outside this
sandbox.

- **Reads:** allowed everywhere except credentials and private data, which are
  neither readable nor writable: `~/.ssh`, `~/.aws`, `~/.gnupg`,
  `~/.config/gcloud`, `~/.azure`, `~/.kube`, `~/.docker/config.json`,
  `~/.netrc`, `~/.npmrc`, `~/.pypirc`, `~/.git-credentials`,
  `~/.config/git/credentials`, `~/.pgpass`, `~/.my.cnf`, `~/.vault-token`,
  `~/.cargo/credentials{,.toml}`, `~/.gem/credentials`, `~/.terraform.d`,
  `~/.config/op`, `~/.password-store`, `~/.codex/auth.json`,
  `~/.claude/.credentials.json`; shell and REPL history (`~/.zsh_history`,
  `~/.zsh_sessions`, `~/.bash_history`, `~/.bash_sessions`, fish, Python,
  Node, psql, MySQL, SQLite, irb); `~/Library/Keychains`; browser and chat
  profiles under `~/Library/Application Support` (Chrome, Chromium, Edge,
  Vivaldi, Opera, Firefox, Arc, Brave, Slack, Discord), `~/Library/Safari`,
  `~/Library/Mail`, `~/Library/Messages`, `~/Library/Cookies`; and Lemma's own
  data -- `~/Library/Application Support/Lemma` (the Agent Host's pairing
  secrets and journal, the app's database and vault), `~/.lemma` (the CLI),
  and every `work.lemma.*` entry under `~/Library/{Application Support,WebKit,
  HTTPStorages,Caches,Preferences}` plus `~/Library/Caches/lemma-desktop`
  (the app's web storage, its signed-in session included, for release,
  candidate and QA bundle ids alike). The denials come after every allow, so a
  root or grant that contains one still cannot reach it. A credential path
  that is a **symbolic link** is denied where it really points too
  (`RESOLVED_n`, resolved by the host when the exec-server starts), because
  the kernel matches the resolved path.
- **What is deliberately readable:** the login keychain file
  (`~/Library/Keychains/login.keychain-db`) and `~/.config/gh`. `gh` and git's
  osxkeychain helper open the keychain file in-process to find their item and
  securityd then decides whether to release the secret; with it denied, `gh
  auth token` answers "no oauth token found". Without `hosts.yml`, `gh` refuses
  to start at all. So a command can obtain what those tools can use -- `gh auth
  token` prints the GitHub token, and `/usr/bin/security` reads any item whose
  access list admits it. That is the cost of `gh pr create` working as the
  user; the file is encrypted with the login password, and the rest of
  `~/Library/Keychains` stays denied.
- **Writes:** the root, granted folders, `TMP`, `USER_TMP` and `CACHE`, and
  nothing else. Also `/dev/null`, `/dev/tty*` and `/dev/ptmx`.
  - `TMP` is this exec-server's own `$TMPDIR`, `CACHE/tmp/<workspace>`.
  - `USER_TMP` is the user's per-user temporary folder
    (`/private/var/folders/…/T`). It cannot be moved: macOS's `mktemp` and the
    system frameworks take it from `confstr`, not from `$TMPDIR`. The rest of
    `/private/var/folders` and all of `/private/tmp` are read-only.
  - `CACHE` is `~/Library/Caches/lemma-host-exec`. The package managers' own
    caches are **not** writable: `~/.npm` (npx runs code out of `_npx`),
    `~/Library/pnpm` (on `PATH`), `~/.cache`, `~/Library/Caches`, `~/.cargo`,
    `~/go`, `~/.gradle`, `~/.m2` and `~/.bun` all hold code the user's own
    terminal runs next. Instead the exec-server's environment sends every
    package manager to `CACHE` (`seatbelt::cache_environment`):
    `XDG_CACHE_HOME`, `npm_config_cache`, `npm_config_store_dir`,
    `npm_config_devdir`, `YARN_CACHE_FOLDER`, `BUN_INSTALL_CACHE_DIR`,
    `COREPACK_HOME`, `DENO_DIR`, `PIP_CACHE_DIR`, `UV_CACHE_DIR`,
    `POETRY_CACHE_DIR`, `CARGO_HOME`, `GOMODCACHE`, `GOCACHE`,
    `GRADLE_USER_HOME` and `CLANG_MODULE_CACHE_PATH`. It is outside every root
    so a bound project does not fill up with caches. Maven's
    `~/.m2/repository` has no environment variable and is not redirected, so
    Maven downloads fail on the host.
- **Kept as they are, in the root and every grant:** `.git/hooks`, `.claude`,
  `.codex`, `.gemini`, `.opencode`, `opencode.json`, `.mcp.json`, `.envrc`,
  `.vscode` and `.idea` -- git's hooks, the coding agents' settings (their
  hooks and MCP servers; the Agent Host starts those agents unconfined),
  direnv, and editor tasks all run without being asked. And a repository
  that **already existed** when the exec-server started (`GIT_n`) keeps its
  `.git` entry and `.git/config` (where `core.fsmonitor` or `core.hooksPath`
  names a program the user's shell prompt runs on every `cd`). Commits,
  branches and pushes still work; `git push -u` pushes but cannot record the
  upstream. A repository a command creates is its own: `git init` may write
  its config, and makes no hooks, because `GIT_TEMPLATE_DIR` is an empty
  folder under `CACHE`. Project files themselves -- `package.json` scripts, a
  `Makefile` -- are the work, and are the user's to review before running.
- **Network:** open, outbound and loopback, for TCP and UDP. `npm install` and
  `npm run dev` need both. **Unix-domain sockets are refused** -- each one is a
  service acting for the user outside the sandbox: the Docker daemon,
  ssh-agent, a password manager's agent, tmux. The one exception is
  `/private/var/run/mDNSResponder`, through which every `getaddrinfo` goes.
  Listening is not limited to loopback: a dev server bound to `0.0.0.0` is
  reachable from the local network, as it would be from the user's terminal.
- **Processes:** fork and exec are allowed. Children inherit the profile and
  cannot drop it. Setuid programs (`ps`, `sudo`) cannot run under any
  sandbox profile.
- **Environment:** a snapshot of the user's login shell (`$SHELL -lic env`,
  taken once, cached, refreshed from Settings). `LEMMA_*`, `AGENT_HOST_*`,
  `AWS_*`, `OP_SESSION_*`, anything ending `_TOKEN`, `_SECRET`, `_KEY`,
  `_PASSWORD`, `_PASSWD`, `_PAT` or `_CREDENTIALS`, and `SSH_AUTH_SOCK`,
  `GPG_AGENT_INFO` and `PGPASSWORD` are removed. `PATH` is kept whole, so
  Homebrew, nvm and asdf tools resolve as they do in the user's terminal.
  `HOME` is the user's, `TMPDIR` is `TMP`, and the cache variables above point
  into `CACHE`.

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
| Rust, macOS only (`tests/seatbelt.rs`) | under the real profile, with a test-made `HOME`: `cat ~/.ssh/x` denied, including through a symbolic link, `touch ~/x` denied; the app's WebKit and HTTPStorages data, `~/.git-credentials`, `~/.codex/auth.json`, shell history and `~/.lemma` unreadable, `~/.config/gh` readable; writes in the root, `CACHE` (through the package managers' environment variables) and both temporary folders allowed; `~/.npm/_npx`, `~/Library/pnpm`, `~/Library/Caches`, `~/.cache`, `~/.cargo/registry` and `/private/tmp` denied; in an existing repository `.git/hooks`, `.git/config` and `.git` itself kept, `.claude`, `.mcp.json`, `.envrc` and `.vscode` kept, while commit and branch work; `git init` of a fresh root works; a Unix-domain socket connect refused while names still resolve; grants; `curl` to loopback; and the real exec-server binary under `sandbox-exec`, driven through the relay |
| Link tests (`src/link/tests.rs`) | `op` → relay → exec-server → `op_ok` across a real WebSocket, disabled host, no handler, unopened workspace, exec-server restart, a crashed exec-server's commands killed by the relay, root-hint admissibility, a folder under `~/lemma` given only to the workspace that owns it, a conversation re-opening in the folder it remembers (and a folder the owner binds later winning), a waiting read not blocking other ops |
| Backend unit | provider maps every op and every failure kind; selection truth table (paired user, user with no host, another user's own host, steered, inbound, host offline, toggle off, cloud); tool filtering for Agent Host runs |
| Backend e2e | the real `lemma-agent-host` binary on the link runs `exec_command` for the paired user's run on the host, and a run of a user with no host lands in the VM; after the binary restarts, the next command re-opens the workspace in the same folder with nothing about the folder stored by Lemma. Over the link: a `control` without `host_execution` keeps the stored report; a host sandbox follows the conversation's latest host run |

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
  workspace keeps its own id; the browser stays there. **No table records
  which host or folder**: both are derived per operation (next items).
- **Which host an op goes to** (`agent/infrastructure/agent_host/host_execution.py`,
  `host_for_host_sandbox`). The sandbox row's slug (`host-<conversation hex>`)
  names the conversation and its owner the user. The op goes to the host in
  the `execution` record of the conversation's most recent run that chose the
  host -- whatever that host's state now, so a run never moves: offline is
  `host_offline`, never another Mac and never the VM. A conversation no run has
  chosen the host in yet falls to the user's usable host, and with none of
  those the op is `host_offline` without being sent. Selection picks among
  the user's online hosts with host execution on and available
  (`host_execution_host_id`): the conversation's last host if it is one of
  them, else the most recently seen.
- **`workspace.open`** is sent by selection, to the host it chose, with
  `conversation_id`, `root_hint` (the folder an Agent Host run in the
  conversation last reported, else null), `date` and `slug` from the
  conversation's own `c/<date>/<slug>` directory, and no grants. A re-open --
  the host answered `workspace_not_open` -- sends the same inputs read again
  from the conversation, with the root the run recorded as the hint; the Mac's
  own memory (§5) is what makes it land in the same folder. `create` opens
  nothing.
- **Routing** (§3). The notice is `{type: "op", op_id, reply, workspace,
  method, params, deadline_ms}` on the host's notice channel. The link session
  first claims it (`SET NX` on the op id, so two links open across a reconnect
  cannot both forward it), then publishes `{type: "picked_up"}` and, once the
  host answers, `{type: "result", result}` or `{type: "result", error: {code,
  message, retryable, kind}}` on the reply channel.
- **Capabilities.** `host_execution` from `hello` and every `control` is kept
  under the `host_execution` key of the host row's `capacity` (the wire is
  unchanged: it is still its own field on both frames). A `control` without it
  keeps the stored report, and a report never replaces the run slots
  (`repository._stored_capacity`).
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
  "host", "host_id", "sandbox_id", "root"}` -- is written under `execution` in the run's
  metadata the first time its context is built. A reclaimed run reads it back
  instead of selecting again, and an approved tool executed after a pause uses
  the paused run's record, so neither can land in the VM when the run was on
  the host. A recorded host whose Mac is offline fails the op with
  `host_offline`.
- **`grants`** are always empty: the backend has no notion of folders a user
  granted. The folder chip's binding lives in the desktop shell, which the
  host reads from `conversation_id` itself.
