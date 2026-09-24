# Host execution on Desktop

**Status:** macOS, local deployments. Windows and Linux keep VM execution.

**Related:** [Agent Host](agent-host.md#the-link) ·
[Desktop security](desktop-security.md) ·
[Sandbox provider adapters](sandbox/provider-adapters.md)

## 1. What it is for

On Desktop, every command a Lemma agent ran used to execute inside a container
in the guest VM. That is the right boundary for anyone the installation owner
invites, and the wrong one for the owner: their `gh` login, git credentials,
Homebrew tools and checked-out repositories are on the Mac, and the VM cannot
see any of them.

Host execution runs the owner's agent commands **on the Mac**, inside an OS
sandbox modelled on Claude Code's, so `gh pr create` or `npm run dev` just
works. The browser stays in the VM. The VM reaching a server the agent started
on the Mac is a separate piece, the loopback relay.

## 2. Who gets it

A run executes on the host only when **all** of these hold. Otherwise it gets
the owner-agnostic VM sandbox, exactly as before.

1. The deployment is a Desktop local install (`DEPLOYMENT_KIND=desktop`).
2. The workspace belongs to the **installation owner** (`installation_owner`).
3. The run's **triggering human** is the installation owner. A run started by
   a teammate, steered by a teammate, or started by an inbound channel message
   from anyone else never executes on the host, even inside the owner's pod.
4. The owner has a paired Agent Host that is **online**, with **host execution
   turned on** (Settings → This Mac → Coding agents).

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
                                              (under sandbox-exec, host-sandbox.sb)
                                                 processes · PTYs · files
```

- **The provider** (`app/modules/workspace/providers/agent_host.py`) implements
  `SandboxProvider` and `SandboxOpsProvider`. Each operation becomes one `op`
  request to the owner's host.
- **Routing.** Only one replica holds a host's socket (newest connection wins),
  and the provider may be running on another replica. The provider publishes
  the request on the host's notice channel with a one-off reply channel. The
  session holding the socket forwards it as an `op` frame and publishes the
  answer to the reply channel. If no session picks the request up within
  `OP_PICKUP_TIMEOUT` (2 s), the host is treated as offline.
- **The exec-server** is `lemma-agent-host exec-server`, the same binary,
  spawned once per host by the link worker under `sandbox-exec -f
  host-sandbox.sb` with the parameters below. Every process it starts and every
  file it touches inherits that confinement. It speaks JSON lines on
  stdin/stdout: one request, one response, correlated by `id`. The link worker
  restarts it if it exits.

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
| `workspace.open` | `root_hint` (conversation folder or null), `grants` | `root` (host absolute path), `home`, `platform` |
| `workspace.close` | — | `{}` |
| `process.start` | `operation_id`, `shell_command` \| `argv`, `cwd`, `environment` `[{name,value}]`, `tty` `{rows,cols}` \| null, `output_limit_bytes`, `initial_input` (b64) \| null | `process_id` |
| `process.read` | `process_id`, `after_sequence`, `wait_ms` | `chunks` `[{sequence, stream: stdout\|stderr\|pty, data}]`, `next_sequence`, `truncated_before_sequence`, `state` (`running`\|`exited`\|`killed`), `exit_code` |
| `process.input` | `process_id`, `data` | `{}` |
| `process.resize` | `process_id`, `rows`, `cols` | `{}` |
| `process.terminate` | `process_id`, `grace_ms` | `{}` |
| `process.list` | — | `processes` `[{process_id, command, state, exit_code, started_at}]` |
| `file.stat` | `path` | a `FileStat`: `path`, `kind` (`file`\|`directory`\|`symlink`), `size_bytes`, `modified_at`, `mode`, `sha256` |
| `file.list` | `path` | `entries` `[FileStat]` |
| `file.mkdir` | `path` | `{}` |
| `file.read` | `path`, `offset`, `length` (≤ 1 MiB) | `data`, `eof` |
| `file.write` | `path`, `upload_id`, `offset`, `data`, `final`, `expected_sha256` (on `final`) | `{}`, or the written `FileStat` on `final` |
| `file.move` | `source`, `destination` | `{}` |
| `file.delete` | `path`, `recursive` | `existed` |
| `secret.deliver` | `path`, `data` | `{}` (written 0600, parent 0700) |

`file.write` writes each chunk to a temporary sibling. `final` verifies the
digest, `fsync`s and renames it into place, so a reader never sees a partial
file. An upload that is not finalized within 5 minutes is removed.

Process output is kept in a ring per process, bounded by
`output_limit_bytes`, with 1-based sequences that are exclusive in `after_sequence`, as
in `sandbox_runtime`. `process.read` waits up to `wait_ms` for new output. A
process is tracked until it exits and has been read to the end, or for 10
minutes after exit.

**Failures** use `detail.kind`, which the provider maps onto
`sandbox_runtime` errors: `not_found`, `already_exists`, `not_a_directory`,
`is_a_directory`, `permission_denied` (including a Seatbelt denial),
`outside_workspace`, `digest_mismatch`, `too_large`, `process_not_found`,
`workspace_not_open`, `exec_server_unavailable`, `timeout`.

**Not offered on the host.** The provider does not declare
`ProviderCapability.PORT_REACH`. Persistent Python sessions raise
`SandboxCapabilityUnsupported` with a sentence that tells the agent to run
`python3` through `exec_command`. There is no Python runtime we can rely on on
the owner's Mac.

## 5. Paths

The workspace **root** is a real host folder, and paths in ops are host
absolute paths. The provider never rewrites a command string. The agent is told
its working directory from the sandbox, not from a hard-coded `/workspace`:

- If the conversation is bound to a folder (the folder chip, or an Agent Host
  run's cwd), the root is that folder. The owner's native tools and Lemma's
  tools then see the same files.
- Otherwise it is `~/lemma/c/<yyyy-mm-dd>/<conversation-slug>`, the same folder
  an Agent Host run would use.

A path in a file op must resolve, after following symlinks, inside the root,
`$TMPDIR`, or a folder the owner granted; otherwise `outside_workspace`. This
is the exec-server's own check, and Seatbelt enforces it again underneath.
Commands are not path-checked, only sandboxed.

## 6. The Seatbelt profile

`desktop/agent-host/resources/host-sandbox.sb`, parameterised with
`-D ROOT=… -D TMP=… -D HOME=… -D GRANT_0=…`. It is modelled on Claude Code's
defaults: broad reads, narrow writes, open network.

- **Reads:** allowed everywhere except `~/.ssh`, `~/.aws`, `~/.gnupg`,
  `~/.config/gcloud`, `~/.azure`, `~/.kube`, `~/.docker/config.json`,
  `~/.netrc`, `~/.npmrc`, `~/.pypirc`, `~/Library/Keychains`,
  `~/Library/Application Support/{Google/Chrome,Firefox,Arc,BraveSoftware}`,
  `~/Library/Mail`, `~/Library/Messages`, `~/Library/Cookies`, and Lemma's own
  data directory. `gh` reads its token from the Keychain through
  `security`'s Mach service, which stays reachable, so `gh` works without
  opening the keychain files.
- **Writes:** the root, granted folders, `$TMPDIR`, `/tmp`, `/private/var/folders`,
  and the package-manager caches (`~/.npm`, `~/.cache`, `~/Library/Caches`,
  `~/.cargo/registry`, `~/.rustup/tmp`, `~/go/pkg/mod`, `~/.gradle/caches`,
  `~/.m2/repository`, `~/.bun/install/cache`, `~/Library/pnpm`). Also
  `/dev/null`, `/dev/tty*` and `/dev/ptmx`.
- **Network:** open, outbound and loopback. `npm install` and `npm run dev`
  need both.
- **Processes:** fork and exec are allowed. Children inherit the profile and
  cannot drop it.
- **Environment:** a snapshot of the owner's login shell (`$SHELL -lic env`,
  taken once, cached, refreshed from Settings). `LEMMA_*`, `AGENT_HOST_*`,
  `*_TOKEN`, `*_SECRET`, `*_API_KEY` and `AWS_*` are removed. `PATH` is kept
  whole, so Homebrew, nvm and asdf tools resolve as they do in the owner's
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
- **The prompt** tells the agent it is on the owner's Mac, names the root, and
  says the browser is a separate machine that reaches the Mac's `localhost`
  through the relay.

## 8. Tests

| Lane | What it proves |
|---|---|
| Rust unit (`make desktop-test`) | exec-server op handling, output ring and sequences, chunked write and digest, path policy including symlink escape, env scrubbing |
| Rust, macOS only (`seatbelt.rs`) | under the real profile: `cat ~/.ssh/x` denied, `touch ~/x` denied, write in the root and `~/.npm` allowed, `git init` plus a commit in the root, `curl` to loopback |
| Link tests | `op` → exec-server → `op_ok` across a real WebSocket, host offline, exec-server restart |
| Backend unit | provider maps every op and every failure kind; selection truth table (owner, non-owner, steered, inbound, host offline, toggle off, cloud); tool filtering for Agent Host runs |
| Backend e2e | the real `lemma-agent-host` binary on the link runs `exec_command` for an owner's run on the host, and a non-owner's run lands in the VM |
