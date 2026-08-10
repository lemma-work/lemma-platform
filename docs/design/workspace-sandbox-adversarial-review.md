# Workspace Sandbox: Adversarial Review

**Date:** 2026-08-03
**Scope:** `sandbox/`, `sandbox-client/`, `lemma-backend/app/modules/workspace/`,
`lemma-backend/app/modules/agent/tools/workspace_cli/`, `docs/design/sandbox/`
**Method:** static review of the code at `a6ef73e7`. Nothing was executed against a live
provider. Each finding is marked **[code]** when it is directly readable from the source,
or **[inferred]** when it depends on provider runtime behaviour I did not exercise.

---

## 0. The one-paragraph verdict

The control plane is genuinely well built — the allocation-token journal, generation
fencing, and unit-of-work boundary are careful work. The failure is not in that core. It
is that **everything the user actually experiences as "the workspace" lives outside the
part that was designed carefully**: the durable filesystem contract differs per provider,
the provider's own lifecycle timer runs unmanaged alongside the sandbox runtime's, the entire data
plane is one process with fixed global caps, and the agent-facing session object is
reconstructed from scratch on every tool call so nothing about a "session" is actually
sessionful. The reported symptoms — "the sandbox keeps resetting", "the reset wiped the
installed packages", "the working directory seems to have changed" — are all explained by
findings below, and none of them are a single bug.

---

## 1. Architectural issues

### A1. Two incompatible durability contracts behind one API **[code]**

| Provider | `workspace_storage_kind` | What survives a release | What survives an allocation replacement |
| --- | --- | --- | --- |
| E2B (prod) | `SANDBOX_NATIVE` (`adapters/e2b.py:268`) | whole rootfs (fs snapshot) | **nothing** |
| Docker (dev/CI) | `VOLUME` mounted at `/workspace` (`adapters/docker.py:133,185`) | container writable layer + volume | only `/workspace` |
| lemma_local | `VOLUME` (`adapters/lemma_local.py:94`) | delegated to the local CLI | unspecified |

Dev and prod disagree about the two questions that matter most to an agent: *does
`pip install` survive?* and *does anything outside `/workspace` survive?* On Docker,
release is `stop_container` and the writable layer is preserved; on E2B it is a
filesystem snapshot. They happen to agree for release and disagree completely for
replacement. `docs/design/sandbox/README.md:98` states the goal "Preserve user files
across workspace release on every supported provider" — true for *release* only, and the
document never elevates the replacement case to the same prominence even though
replacement is the common path in production.

This is the root cause of "works locally, behaves differently in prod".

### A2. A profile-digest change silently destroys every user's workspace on E2B **[code]**

Chain:

1. `repository.resume_released_allocation` returns `None` when
   `allocation.profile_digest != profile.digest` (`persistence/repository.py:1437`).
2. `ensure` therefore opens a *new* allocation intent (`lifecycle.py:118`).
3. Storage is still bound to the old allocation, so `superseded_native_allocation` is
   set (`lifecycle.py:131-154`).
4. `_retire_superseded_native_workspace` calls `release_allocation` **and then
   `destroy_allocation`** on the old sandbox (`lifecycle.py:720-742`).
5. On E2B `destroy_allocation` is `AsyncSandbox.kill()` (`adapters/e2b.py:508`) — and on
   E2B the sandbox *is* the storage.

Net effect: **shipping a new workspace template/profile digest wipes every user's entire
workspace filesystem.** `workspace_profile_digest` is a plain config value
(`config.py:88`), so this fires on an ordinary release, not an exotic migration.

Worse, the behaviour is *asserted as correct*:
`tests/adapters/test_e2b_real.py:611-621` drives exactly this transition and asserts
`replacement_provider_id != fresh_provider_id` — it never asserts that a file written
before the replacement survives it.

The same path also fires when the current allocation is in any state other than
`RELEASED` (e.g. `FAILED` after a bad boot), so a transient provider failure can also
cost the user their files.

### A3. The data plane is one process with hard global caps **[code]**

`docs/design/sandbox/lifecycle-state-model.md:118` states the manager is deliberately
single-replica and single-process. The caps it lists are implemented as **process-global,
not per-tenant**:

| Cache | Cap | Where |
| --- | --- | --- |
| process routing records + inflight | 64 | `processes.py:63,101` |
| Python execution results + inflight | 32 | `python_sessions.py:64,254` |
| Python sessions | 512 | `python_sessions.py:63,148` |

Records are retained until their request deadline and pruned only lazily
(`processes.py:407`). The backend's default exec deadline is 60 s
(`workspace_cli.py:37`). So the whole platform sustains roughly:

- **~64 process starts per 60 s** before `CAPACITY_EXHAUSTED`
- **~32 `execute_python` calls per 60 s** before `CAPACITY_EXHAUSTED`

Past that, `start`/`execute` raise 429 and — by explicit design — the manager "never
evicts a live idempotency record". These are not per-user limits. Two moderately active
users can starve everyone. There is no per-tenant fairness anywhere in the registry.

### A4. Two filesystems, bridged only by the agent remembering to copy **[code]**

The sandbox (`/workspace/...`) is scratch; the pod filesystem (`/me/...`) is durable. The
only bridge is the agent choosing to run `lemma files upload`
(`agent/domain/prompts.py:195-199`). Every durability property the user cares about
therefore depends on a prompt instruction rather than the platform. The transcript
behaviour — the agent panicking, then deciding to "write the durable files to the pod
right away so another reset can't lose them" — is the agent correctly reverse-engineering
this and routing around the platform.

### A5. One sandbox per user, shared by everything **[code]**

`sandbox_id(user_id) = user_id` (`services/sandbox_manager.py:24-25`). Every
conversation, every pod, every concurrent agent run, and every scheduled job for one user
share a single sandbox — one CPU and 2 GiB on the Docker profile
(`config.py:61-68`), one process-buffer namespace, one Python-context namespace, one
directory tree. There is no per-conversation isolation and no admission fairness between
a user's own concurrent runs.

### A6. The declared source of truth diverges from the implementation

`docs/design/sandbox/README.md:382` declares that directory the sole source of truth.
At least three load-bearing statements in it are not true of the code:

| Doc claim | Reality |
| --- | --- |
| "the sandbox runtime deliberately disables automatic resume: pause/resume is an explicit, generation-fenced lifecycle event" (README.md:84) | `AsyncSandbox.connect()` auto-resumes a paused sandbox (e2b SDK `sandbox_async/main.py:262`), and `_connect` is on every runtime path. Resume is implicit and unfenced. See B2. |
| "Run the same behavioral conformance suite against real Docker and E2B environments for the initial release" (README.md:105) | CI runs `RUN_DOCKER_TESTS=1` only (`.github/workflows/ci.yml:139-146`). Real E2B tests are skipped unless someone sets `RUN_E2B_TESTS=1` locally. See D1. |
| "Status: Implemented and verified for Docker and E2B" (README.md:3) | Verified for Docker in CI; E2B is unverified in any automated gate. |

---

## 2. Lifecycle issues

### B1. The E2B workspace timeout is never refreshed — sandboxes pause after 1 h of *active* use **[code]**

`create()` passes `timeout=workspace_timeout_seconds` (default **3600 s**) with
`lifecycle={"on_timeout": "pause", "auto_resume": False}` (`adapters/e2b.py:296-305`).
E2B's `timeout` is a wall-clock lifetime, not an idle timer.

`set_timeout` is called from exactly one place — `_extend_function_lifetime`
(`adapters/e2b.py:1410`) — and both of its callers are guarded by
`workload_kind == WorkloadKind.FUNCTION` (`e2b.py:604, 1244`). **Workspaces never refresh
their timeout.**

`_connect` short-circuits on the in-memory cache and returns without touching the
provider (`e2b.py:1280-1282`), so a continuously-used workspace never even re-enters the
`connect(timeout=…)` path that would have bumped it.

Result: a workspace in continuous use is paused by E2B at T+3600 s from creation,
regardless of activity. The sandbox runtime's own idle release (300 s, `config.py:146`) never fires
for it, so nothing in the control plane is watching.

The config validator reasons about this incorrectly:

```python
# config.py:204-213 — only checks e2b_timeout > idle + 2*interval
```

with the comment "It must comfortably exceed the sandbox runtime idle cleanup so the sandbox runtime remains
the normal pause/resume authority" (`config.py:140-144`). That reasoning holds only if
the E2B timeout were an idle timer. It is an absolute lifetime, so the invariant the
validator is trying to enforce is not the one it enforces.

### B2. A provider-initiated pause is invisible to the control plane **[code]/[inferred]**

When B1 (or any E2B-side pause) fires:

- durable state still reads `ACTIVE`; `resource_generation` and `allocation_epoch` are
  unchanged;
- `protect_activity` keeps succeeding (`repository.py:153`), so idle cleanup stays
  suppressed;
- `find_allocations` (`e2b.py:534-575`) matches on metadata only and never inspects
  running-vs-paused, so reconciliation cannot detect it;
- the next runtime op calls `_connect`, which either uses a dead cached handle or
  performs a fresh `connect()` that **silently auto-resumes** the sandbox.

The design's central fence — "Every runtime operation validates allocation ID and epoch
before calling the provider" (`lifecycle-state-model.md:72`) — cannot see this transition
at all, because the epoch never moved. After the implicit resume, the manager's
`_ProcessBuffer` watchers, `handle`s, and `_python_contexts` still claim resources that
the pause/resume cycle invalidated. **[inferred]** on exactly what E2B preserves through
each pause variant; **[code]** on the fact that the sandbox runtime records nothing.

Related inconsistency: explicit release uses `pause(keep_memory=False)` — a cold boot
(`e2b.py:484-487`) — while `on_timeout: "pause"` uses the string form, which defaults
`keep_memory=True` (SDK `main.py:203`). Two different snapshot semantics for what the
state machine treats as the same `Active -> Suspended` edge.

### B3. Background processes die 5 minutes after the last poll **[code]**

`protect_activity(key, until=deadline_at)` is called with the *operation's* deadline
(`processes.py:380`, `python_sessions.py:550`, `filesystem.py:327`). For a backgrounded
process the operation deadline is the tool call's timeout — 60 s by default.

So: the agent starts a dev server or a long build, `exec_command` yields at 30 s and
returns a `process_id`, the agent moves on. `last_used_at` stops advancing. At 300 s the
maintenance worker claims the sandbox and releases it (`maintenance.py:52-57`), and
`_quiesce` **explicitly kills every running command** (`e2b.py:1354-1366`) before pausing.

The design advertises "reconnectable commands and PTYs" and "background processes"
(README.md:99, 233) as portable capabilities, but there is no lease that keeps a sandbox
alive for a process that outlives its starting tool call. Nothing extends
`protected_until` for the lifetime of a running process.

### B4. Every manager restart silently destroys all live processes and kernels **[code]**

Accepted by design (`lifecycle-state-model.md:68`), but the product consequence is not
handled anywhere: a routine deploy of the sandbox manager, mid-conversation, drops all
`_ProcessBuffer`s, process records, and Python session handles. The agent's next call
returns `PROCESS_NOT_RUNNING` or a fresh, empty kernel. There is no signal that
distinguishes "your kernel was restarted by a deploy" from "your code failed", so the
agent narrates it as a mysterious reset. This is the most likely source of the
"Python kernel hiccup" in the transcript.

### B5. The first Python session after a restart kills every *other* conversation's kernel **[code]**

`create_python_session` lists all code contexts on the sandbox and removes every one not
present in the manager's in-memory `owned` set (`e2b.py:1071-1080`):

```python
owned = self._python_contexts.setdefault(allocation.provider_id, set())
existing = await sandbox.list_code_contexts()
owned.intersection_update(existing_ids)
for context in existing:
    if context.id not in owned:
        await sandbox.remove_code_context(context.id)
```

After a manager restart `owned` is empty. Because one sandbox is shared by all of a
user's conversations (A5), the first conversation to create a kernel destroys the kernels
of all the others. The comment frames this as restart hygiene; the blast radius is
cross-conversation because the ownership set is keyed by `provider_id`, not by session.

### B6. Multiple independent total-wipe paths, none of them announced

Collected: profile digest change (A2), allocation replacement after a failed boot (A2),
retention expiry at 7 days (`config.py:172`, `README.md:307`), and permanent delete. On
E2B all four remove the filesystem. None of them produce a user-visible or
agent-visible event — the next `ensure` just returns a ready, empty sandbox. The agent
cannot distinguish "fresh workspace" from "your files were deleted", which is exactly the
confusion in the transcript.

---

## 3. Implementation bugs

### C1. Every re-poll of a process replays its entire output buffer **[code]**

`SandboxWorkspaceSession._output_sequence` is instance state
(`sandbox_session.py:108`), and `_collect_process` seeds `after_sequence` from it
(`sandbox_session.py:533`).

But the session object is **constructed fresh on every tool call** —
`_get_workspace_session` → `WorkspaceToolRuntime.get_session` →
`WorkspaceSandboxService.get_session` → `return SandboxWorkspaceSession(...)`
(`workspace_sandbox_service.py:406`). There is no session cache.

So `write_stdin` / any subsequent poll of a still-running process always sends
`after_sequence=0`, and the manager returns every retained chunk
(`e2b.py:767: chunks = tuple(item for item in buffer.chunks if item.sequence > after_sequence)`)
— up to the 2 MiB buffer ceiling, every time. The agent sees the whole output duplicated
on each poll and burns tokens on it.

### C2. `cd` never persists; `set_cwd`/`get_cwd` are dead code **[code]**

- `self._cwd` is initialised from `initial_cwd` in the constructor
  (`sandbox_session.py:103`) and, per C1, the object is new on every tool call. So the
  cwd resets to the conversation default every call.
- `set_cwd` and `get_cwd` (`sandbox_session.py:450-459`) have no callers outside the
  `ISandbox` interface declaration — verified across `lemma-backend/app`.
- `get_cwd()` cannot work even if it were called: it runs `pwd` via `exec_command`, which
  starts a **new** process with `cwd=self._cwd` (`sandbox_session.py:192`). It can only
  ever return the value it already held, then assigns that value back to itself.

Consequence: `cd /somewhere` in one `exec_command` has zero effect on the next one. The
agent's model of a persistent shell is wrong, and the prompt's "run `pwd` once and then
use relative paths" advice reinforces a shell that does not exist.

### C3. Shell and Python session identity are derived inconsistently **[code]**

`workspace_cli.py:57-61` builds `default_python_session_id` with the cwd folded in but
`default_shell_session_id` without it. Meanwhile
`SandboxWorkspaceSession.python_session_id` is `uuid5(..., f"sandbox:{logical_id}:python:{session_id}")`
(`sandbox_session.py:94-97`) — **no cwd**. The invariant in
`lifecycle-state-model.md:130-134` ("Backend session identity also includes that cwd, so
… both runtimes move together") is satisfied only because the tool layer happens to encode
cwd into the string it passes down. The adapter itself does not enforce it, and the shell
half does not participate at all.

### C4. Two contradictory definitions of "success" **[code]**

`_collect_process` returns `success = state in {RUNNING, SUCCEEDED}`
(`sandbox_session.py:568`) — a process that merely yielded early is "successful".
`execute_terminal_command` then computes `success = result.get("exit_code") == 0`
(`sandbox_session.py:161`) — for the same yielded process, `exit_code` is `None`, so
success is `False`. Two callers of the same underlying call get opposite answers.

### C5. No backend-side handling of `CAPACITY_EXHAUSTED` **[code]**

`exec_command` and `execute_code` catch `SandboxApiError` and convert it to a result dict
with "Retry the same operation if it is still needed."
(`sandbox_session.py:206-211, 128-139`). Given A3's caps, the agent is the retry loop for
a platform-level capacity limit. `_ensure_python_session` does retry on `WAIT`
(`sandbox_session.py:493-514`), so session creation is resilient while execution is not —
an inconsistency, not a design.

### C6. `/tmp` has three contradictory contracts **[code]**

- `_canonical_runtime_path` explicitly permits `/tmp` as a valid workspace path root
  (`sandbox_session.py:29,44-48`).
- The system prompt tells the agent "Do NOT work in `/tmp` … it gets wiped"
  (`prompts.py:190-191`).
- The sandbox runtime itself stores process control state in `/tmp/.sandbox/processes`
  (`e2b.py:92`), and `_quiesce` deletes that tree on release (`e2b.py:1379-1385`).

On E2B a filesystem pause actually *does* preserve `/tmp`, so the prompt's warning is
wrong for the production provider and right for the dev one.

### C7. The local no-Docker test harness was deleted and nothing replaced it **[code]**

`lemma-backend/app/modules/workspace/testing/` now contains only an empty `__init__.py`;
`fake_sandbox.py` was removed in `75eea642` (#214). Only stale `.pyc` files remain
(`testing/__pycache__/fake_sandbox.cpython-314.pyc`,
`tests/unit/__pycache__/test_fake_sandbox…`). The documented ability to exercise
workspace tools with no Docker is gone, which pushes all workspace iteration onto a real
provider.

---

## 4. Verification gaps

### D1. The production provider has no automated conformance gate **[code]**

`.github/workflows/ci.yml:139-146` runs `tests/adapters/test_docker_real.py` with
`RUN_DOCKER_TESTS=1`. Nothing sets `RUN_E2B_TESTS`, so all 700 lines of
`test_e2b_real.py` are skipped in CI. Every finding in §1–§2 that is E2B-specific
(A1, A2, B1, B2, B5) is in code that CI never executes.

### D2. The persistence test proves the weakest possible property **[code]**

`test_e2b_real.py:428-458` writes one file under `/workspace`, releases, resumes, and
reads it back. It does not check: an installed package, a file outside `/workspace`, a
running process, a Python session's variables, or survival across a profile change (the
test that *does* change profiles, at :611, asserts a new sandbox and checks no files).

### D3. Untested behaviours that map directly to the reported symptoms

None of the following has a test anywhere in the repo:

- a workspace alive longer than `workspace_timeout_seconds` (B1)
- a provider-initiated pause observed by the control plane (B2)
- a background process surviving an idle window (B3)
- manager restart with live processes/kernels (B4, B5)
- concurrent load against the 64/32 caps (A3)
- output sequencing across two separate tool calls (C1)

---

## 5. Agent-facing contract issues

### E1. The per-conversation random cwd is indistinguishable from a wiped sandbox **[code]**

`new_workspace_cwd` produces `/workspace/c/{date}/{random 8-char slug}` per root
conversation (`agent/services/workspace_location.py:53-56`). The prompt then says the
whole sandbox is the agent's, and forbids it from creating a stable root:

> "Never create a parallel project root directly under `/workspace`"
> — `agent/domain/prompts.py:184-186`

So the agent has **no stable home across conversations**, and a follow-up conversation
starts in an empty directory that looks identical to a reset sandbox. This is almost
certainly the literal cause of "The research directory is gone — likely the sandbox was
reset between sessions" in the transcript: the files were probably still there, under the
previous conversation's slug, unreachable and unmentioned.

### E2. The prompt asserts a pip/kernel guarantee that nothing verifies **[code]/[inferred]**

> "run `pip install <package>` (via `exec_command`), then import it in `execute_python` —
> they share the same interpreter."
> — `agent/domain/prompts.py:194-197`

The only contract test for this (`tests/adapters/workspace_python_contract.py`) installs a
wheel and imports it from a **fresh** `python -c`. It never checks that an already-running
`create_code_context` kernel — which is what `execute_python` uses — picks up a package
installed after the kernel started. **[inferred]:** this usually works via CPython's path
finder revalidation, but it is an unverified promise made to the agent in the prompt, and
it is exactly the class of thing that fails intermittently.

### E3. Failure text invites retries the platform cannot satisfy **[code]**

Every workspace tool failure is rendered as "Treat this as a recoverable tool failure and
retry if the operation is still needed" (`workspace_cli.py:88-91`). Under A3's caps or
B1's pause, retrying is precisely wrong — it amplifies load against a global limit. The
agent has no way to tell a transient transport error from a structural one.

---

## 6. Ranked fix list

Ordered by (user-visible damage) × (confidence) ÷ (effort).

**Stop the bleeding**

1. **B1** — call `set_timeout` for workspaces on every runtime lease, or stop caching
   handles past a refresh interval. One-line class of fix; prevents mid-session pauses.
2. **A2** — never destroy sandbox-native storage to satisfy a profile change. Either
   pin the digest per existing allocation until the user's next natural release, or
   snapshot `/workspace` out before retiring. Add the missing assertion to
   `test_e2b_real.py:611`.
3. **C1** — cache `SandboxWorkspaceSession` per `(user, session_id)`, or move
   `_output_sequence` into the process-binding store next to `bind_process_to_session`.
4. **E1** — give each user one stable home (e.g. `/workspace/home`) and make the
   per-conversation dir a subdirectory of it, or tell the agent explicitly where prior
   conversations' work lives.

**Structural**

5. **A3** — make the caps per-tenant, and either shard the manager or make the registry
   externally backed. Today's numbers are a platform-wide ceiling of ~1 exec/s.
6. **B3** — introduce a real process lease: extend `protected_until` while any process
   started by that sandbox is still running.
7. **B2** — teach reconciliation to observe running-vs-paused in `find_allocations`, and
   bump `allocation_epoch` on any resume, implicit or explicit.
8. **B5** — key `_python_contexts` ownership by session, or persist enough to survive a
   restart, so one conversation cannot reap another's kernel.
9. **A1** — pick one durability contract and make Docker and E2B both implement it, or
   surface the difference explicitly in the profile capability set so callers can branch.

**Hygiene**

10. **D1** — run the E2B conformance suite on a schedule against a real account, even if
    not per-PR.
11. **C2** — delete `set_cwd`/`get_cwd`, or implement a real persistent cwd.
12. **C4, C5, C6, E3** — reconcile the contradictions; they are cheap and each one
    currently misleads either the agent or a maintainer.
13. **A6** — update `docs/design/sandbox/` to describe what the code does. It is
    declared the source of truth and is currently wrong in ways that would mislead the
    next person to touch this.
