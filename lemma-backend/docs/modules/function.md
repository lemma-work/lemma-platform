# Function module

**Status:** Canonical the sandbox runtime-backed execution implemented; rollout verification in progress

The function domain owns definitions, immutable revisions, permissions, public
runs, schemas, durable attempts, scheduling, tickets, callbacks, cancellation,
and completion events. It uses the sandbox runtime only through the provider-neutral sandbox
and process API.

The canonical target design is:

- [Function execution](../../../docs/architecture/sandbox/function-execution.md)
- [Function execution benchmark](../../../docs/operators/sandbox-function-benchmark.md)
- [Sandbox design overview](../../../docs/architecture/sandbox/README.md)
- [Sandbox protocol](../../../docs/architecture/sandbox/sandbox-protocol.md)
- [Testing strategy](../../../docs/architecture/sandbox/testing-strategy.md)
- [Verification and rollout](../../../docs/architecture/sandbox/verification-and-rollout.md)

Do not duplicate function runtime, retry, queue, provider, credential, or artifact
rules here. They belong in the canonical design set. There is no separate legacy
execution service or fallback path.

## Main data model

| Table | Meaning |
| --- | --- |
| `functions` | Pod-scoped definition: name, description, input/output/config schemas, code path, and the revision hash currently promoted |
| `function_revisions` | One built, executable revision. The artifact and source bytes were always content-addressed; this row is what makes them findable, and it snapshots the schemas so promoting an old revision restores the contract its code actually implements |
| `function_runs` | One durable attempt: input/output, status, logs, error, deadline, and job handle. The foreign key to `functions` is `SET NULL`, because a run is the record of what the function did and must outlive the definition; the delete path refuses while any run is still in flight |

A new revision's schemas are read by the pod's function runtime
(`application/function_schema_dispatcher.py`). A dead or moved runtime (a
transport error, 404/410, 5xx) is asked once more on a fresh endpoint; an
inspection the runtime refused because fetching the code failed in transit (a
422 naming an httpx transport error such as `ReadError`) is asked once more.
A function whose code does not load is refused at once.

## Runs on the work runtime

| Kind | Class | Written by | Does |
| --- | --- | --- | --- |
| `function.run` (JOB) | automation | The transaction that creates an asynchronous run | Starts it under the pod's gate and invokes the sandbox |
| `function.deadline` (FAST) | events | The start (and the creation of a synchronous run) | Fails a run still open past its deadline plus the callback grace |
| `function.cancel` (JOB) | automation | Cancelling a started run from a workflow | Asks the sandbox to stop it |

The per-pod gate is the RUNNING rows: the start counts the pod's running runs
under a lock on the pod and refuses past
`FUNCTION_RUNTIME_MAX_CONCURRENT_RUNS_PER_POD`. A queued run turned away is
deferred without charge; a synchronous caller waits up to its start deadline. A
sandbox that cannot be reached before the start is waited out, uncharged, for
at most the run's start window. Once a run is RUNNING it is never invoked
again: a reclaimed item finds it started and leaves it to its callback or its
deadline. Every terminal transition emits `FunctionRunFinished`
(`contracts/run_facts.py`) in its own transaction.

The table list is inventory, not a runtime contract. Attempt, retry, queue, and
artifact rules live in the canonical design set above.

## Kept from the assistant

`functions.assistant_invoke` (default true; migration 0074) says whether the
Lemma's assistant is offered the function. A plugin sets it false on a function
that its own flow calls after a check the flow owns -- an app's "claim" behind
a daily limit -- so the assistant cannot step around the check by running it by
name. Such a function is left out of the assistant's resource map
(`contracts/agent_tools.list_pod_functions`) and of the functions an AI tool is
offered over MCP (`contracts/person_runs.runnable_functions`), and
`resolve_execute` refuses the assistant a run of it with 403
`FUNCTION_NOT_FOR_ASSISTANT` (`services/assistant_access`). "The assistant" is
`core/authorization/app_sessions.is_the_assistant`: the assistant's context,
except an app framed in an AI tool, which is signed in with the assistant's
standing under an `app_session_id` session and is the app's own flow. A person,
an app in Lemma, a workflow, a schedule, another function, and a named agent
holding a grant on it all still run it. The assistant still reads the function
(to build with it), with the flag on it. `contacts_invoke` is a separate opt-in
and is not narrowed by this. Set on create and update
(`assistant_invoke`), and carried by a bundle only when false.

The assistant acts with the person's own access, so one who may edit the
function can have the assistant turn the flag back on; it guards against the
assistant choosing a shortcut, not against the person.

## What a run is told about its person

`FunctionContext.user_name` is the member's name as Lemma shows it (first and
last name), read from identity when the run starts
(`infrastructure/person_names`), so a rename reaches the next run.
`None` for a run with no member behind it or a member with no name. It reaches
the runtime as `identity.user_name`, sent only when set, like `contact_id`, and
the code as `ctx.user_name` and `LEMMA_USER_NAME`. A function runtime older than
this change forbids the field, so a run with a name needs the runtime image of
the same release.
