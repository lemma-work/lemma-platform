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


def runtime_failure_message(request: RuntimeTerminalRequest) -> str:
    """The message for a run the runtime itself reported as failed."""
    assert request.error is not None
    if request.error.name == "TimeoutError":
        return "Function execution timed out (deadline exceeded)"
    return redact_text(f"{request.error.name}: {request.error.message}")[:16_384]


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
