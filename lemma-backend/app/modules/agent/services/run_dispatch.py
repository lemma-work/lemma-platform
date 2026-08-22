"""Whether starting a run also hands it to the worker.

Normally it does: `add_user_message_and_start_run` collects an
`AgentRunStartedEvent`, the outbox publishes it, and a streaq worker picks the
run up. The surface e2e suite needs the opposite — it runs the agent in-process
so its in-test fake platform servers see the delivery — and without a way to say
so the run would execute twice, once inline and once on the shared session
worker.

This is a `ContextVar` rather than an injected collaborator on purpose, and the
purpose is worth writing down because the injected version looks tidier. The
caller that needs to suppress is a *surface* event handler, which constructs its
own `ConversationService` several layers down; injecting would mean threading a
parameter from `agent_surfaces` through the handler and into `agent`, across a
module boundary, to carry a flag that exists for one test file. Dynamic scoping
is what this actually is.

It defaults to False and is only ever set by the context manager below, so a
process that never enters it cannot observe the flag.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_SUPPRESS_RUN_ENQUEUE: ContextVar[bool] = ContextVar(
    "suppress_agent_run_enqueue", default=False
)


def run_enqueue_suppressed() -> bool:
    """True when the caller has taken responsibility for executing the run."""
    return _SUPPRESS_RUN_ENQUEUE.get()


@contextmanager
def suppress_agent_run_enqueue() -> Iterator[None]:
    """Run agent-run starts inline: skip the worker-dispatch event publish."""
    token = _SUPPRESS_RUN_ENQUEUE.set(True)
    try:
        yield
    finally:
        _SUPPRESS_RUN_ENQUEUE.reset(token)
