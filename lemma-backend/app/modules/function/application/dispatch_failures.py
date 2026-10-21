"""What a failed dispatch is called, and what the person reading the run sees.

Two things that only make sense together, so they live together: the exception
taxonomy the dispatcher raises, and the functions that turn a failure into the
one line stored on the run.

The taxonomy exists because "the call failed" and "the call may have run" are
different facts, and only the second one forbids a retry. The messages exist
because the run record is the whole explanation a user gets -- there is no
stack trace on the other side of it -- so each branch names both what happened
and what the caller can do about it.

Split out of `function_dispatcher.py` when that file reached the 600-line
ceiling `scripts/check_architecture.py` enforces. Classifying a failure at the
process boundary is its own job, per the backend rule in CONTRIBUTING.md, and
it is the part of the dispatcher that is a pure function of the exception.
"""

from __future__ import annotations

import httpx

from app.core.infrastructure.db.transient_errors import is_database_unavailable
from app.core.infrastructure.work import Released
from app.core.redaction import redact_text
from app.modules.function.contracts.runtime import RuntimeTerminalRequest
from sandbox_runtime.errors import (
    SandboxError,
    SandboxUnavailable,
)


class InvocationOutcomeUnconfirmed(RuntimeError):
    """The runtime may have begun work, so the invocation must not be replayed."""


class RuntimeNeverReached(InvocationOutcomeUnconfirmed):
    """No connection was ever established, so the runtime cannot have begun work.

    The one failure where replay is provably safe, and it is deliberately
    narrow. ``httpx.TransportError`` is not the right boundary: ``ReadError``,
    ``WriteError`` and ``RemoteProtocolError`` all mean bytes crossed the wire
    and the run may be underway with only the response lost. Only a refused or
    unroutable connection proves the request was never delivered.

    It still subclasses ``InvocationOutcomeUnconfirmed`` so that any caller
    which does not know about this distinction keeps the safe behaviour.
    """


class PodAtCapacity(Exception):
    """The pod already runs as many functions as it may; this run has not started.

    Not a domain error: nobody sees it. A queued run waits its turn and a
    synchronous caller waits up to its start deadline. The slot is the RUNNING
    row, counted at the start under the pod's lock, so it is held until the run
    is terminal however that end arrives -- inline, by callback, or by the
    deadline.
    """


class SandboxNotReady(Exception):
    """The pod's sandbox could not be reached or is still starting, and the run
    has not started. Nothing ran, so a queued run waits and tries again."""


#: The runtime's name for a run that waited for a free worker until its deadline.
RUNTIME_NEVER_STARTED_ERROR = "RuntimeOverloaded"


def runtime_failure_message(request: RuntimeTerminalRequest) -> str:
    """The message for a run the runtime itself reported as failed."""
    assert request.error is not None
    return runtime_error_message(request.error.name, request.error.message)


def runtime_error_message(name: str, message: str) -> str:
    # asyncio.wait_for raises the built-in TimeoutError without a message, so
    # the type is the whole signal.
    if name == "TimeoutError":
        return "Function execution timed out (deadline exceeded)"
    if name == RUNTIME_NEVER_STARTED_ERROR:
        # Distinct from a timeout on purpose: nothing ran, so the reader may
        # retry, and the remedy is capacity rather than a faster function.
        return (
            "Function never started: no function worker was free before the "
            "deadline. Nothing ran, so it is safe to retry."
        )
    return redact_text(f"{name}: {message}")[:16_384]


def execution_error(exc: BaseException) -> str:
    """The message for a dispatch that raised before the runtime settled it."""
    if isinstance(exc, InvocationOutcomeUnconfirmed):
        if isinstance(exc.__cause__, (httpx.TimeoutException, TimeoutError)):
            # Report the deadline (what the caller can change) while keeping
            # the "may have run" caveat (what they must not assume away).
            return (
                "Function execution timed out (deadline exceeded); execution "
                "may have started and was not retried"
            )
        return (
            "Function runtime response was not confirmed; execution may have "
            "started and was not retried"
        )
    if isinstance(exc, TimeoutError):
        return "Function execution timed out (deadline exceeded)"
    if isinstance(exc, SandboxError):
        # The type says whether waiting could have helped; the message says
        # what happened. Both go to a user reading a failed run.
        if isinstance(exc, SandboxUnavailable):
            return f"Function sandbox unavailable ({redact_text(str(exc))})"
        return f"Function sandbox refused the request ({redact_text(str(exc))})"
    if isinstance(exc, ValueError) and "token expires" in str(exc):
        return "Function execution exceeds delegated token lifetime"
    return "Function execution failed"


def not_a_failure(exc: Exception, *, queued: bool, started: bool) -> bool:
    """Signals for the caller, not outcomes of the run: a full pod, a sandbox
    not up yet, or a queued run stopped before its start."""
    if isinstance(exc, (PodAtCapacity, SandboxNotReady)):
        return True
    if not started and is_database_unavailable(exc):
        # Lemma's database could not be reached before the function was sent
        # anywhere: nothing ran, so the run is retried, not failed. Once it
        # started, the result is the sandbox's and failing is what records it.
        return True
    return queued and not started and isinstance(exc, Released)
