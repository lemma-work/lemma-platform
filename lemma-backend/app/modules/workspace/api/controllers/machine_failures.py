"""What a machine's runtime failure becomes at the HTTP boundary: one table.

Every machine route that calls into a machine catches the same two families --
`sandbox_runtime.errors.SandboxError` from the fabric ops, and the runtime
client's `WorkspaceRuntimeError` where a route reaches the client directly --
and both have to land on the same status. The files controller used to decide
by sniffing class names (``"Rejected" in name``), which sent a refused
credential to the person as "your file is too large", because every refusal's
name carries "Rejected". The table is types, most specific first, read with
``isinstance``: a new subclass lands on its nearest ancestor's row, never on a
word in its name.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request, Response
from fastapi.routing import APIRoute

from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.workspace.providers.runtime_errors import (
    WorkspaceRuntimeError,
    WorkspaceRuntimeFileConflict,
    WorkspaceRuntimeFileNotFound,
    WorkspaceRuntimeFileRejected,
    WorkspaceRuntimeProcessGone,
    WorkspaceRuntimeUnauthorized,
)
from sandbox_runtime.errors import (
    SandboxCapabilityUnsupported,
    SandboxError,
    SandboxPathConflict,
    SandboxPathNotFound,
    SandboxProcessNotFound,
    SandboxRejected,
    SandboxUnauthorized,
    SandboxUnavailable,
)

logger = get_logger(__name__)

#: Everything a machine call may fail with that the table answers. Anything
#: else is a defect and must surface as a 500, not as "the machine is busy".
MACHINE_FAILURES: tuple[type[BaseException], ...] = (
    SandboxError,
    WorkspaceRuntimeError,
)


class MachineCallFailed(DomainError):
    """A machine operation the machine itself refused or could not do."""

    def __init__(self, message: str, *, code: str, status_code: int) -> None:
        super().__init__(message, code=code, status_code=status_code)


@dataclass(frozen=True, slots=True)
class _Row:
    types: tuple[type[BaseException], ...]
    status: int
    code: str
    #: ``{path}`` is the path the call was about, when there is one.
    message: str
    #: Lemma's fault, not the caller's: logged with its traceback (`_log`).
    logged: bool = False


_UNREACHABLE = _Row(
    (SandboxError, WorkspaceRuntimeError),
    503,
    "MACHINE_UNREACHABLE",
    "The machine is not reachable right now.",
    logged=True,
)

#: Most specific first; the first row whose types match wins.
_TABLE: tuple[_Row, ...] = (
    _Row(
        (SandboxUnauthorized, WorkspaceRuntimeUnauthorized),
        503,
        "MACHINE_RUNTIME_UNAUTHORIZED",
        "Lemma could not authenticate to the machine.",
        logged=True,
    ),
    _Row(
        (SandboxPathNotFound, WorkspaceRuntimeFileNotFound),
        404,
        "MACHINE_PATH_NOT_FOUND",
        "No such path: {path}",
    ),
    _Row(
        (SandboxProcessNotFound, WorkspaceRuntimeProcessGone),
        404,
        "MACHINE_PROCESS_NOT_FOUND",
        "The machine has no such process.",
    ),
    _Row(
        (SandboxPathConflict, WorkspaceRuntimeFileConflict),
        409,
        "MACHINE_PATH_CONFLICT",
        "Something is already at {path}, or it is not what the call expected.",
    ),
    _Row(
        (SandboxCapabilityUnsupported,),
        409,
        "MACHINE_CAPABILITY_MISSING",
        "This machine cannot do that.",
    ),
    _Row(
        # The fabric ops turn the runtime's 413/422/507 file refusals into a
        # bare `SandboxRejected`; the rest of that family is caught above.
        (WorkspaceRuntimeFileRejected, SandboxRejected),
        413,
        "MACHINE_FILE_REJECTED",
        "The machine refused that file: it is larger than this call will "
        "carry, or there is no room for it. Read or write a range.",
    ),
    _Row(
        (SandboxUnavailable,),
        503,
        "MACHINE_NOT_READY",
        "The machine is still starting. Try again in a moment.",
        logged=True,
    ),
    _UNREACHABLE,
)


def _row_for(exc: BaseException) -> _Row:
    for row in _TABLE:
        if isinstance(exc, row.types):
            return row
    return _UNREACHABLE


def _log(code: str, exc: BaseException) -> None:
    """One literal event per logged row: the catalog reads them from here."""
    if code == "MACHINE_RUNTIME_UNAUTHORIZED":
        logger.warning(
            "workspace.machine_failures.runtime_unauthorized.failed", exc_info=exc
        )
    elif code == "MACHINE_NOT_READY":
        logger.warning("workspace.machine_failures.not_ready.degraded", exc_info=exc)
    else:
        logger.warning("workspace.machine_failures.unreachable.degraded", exc_info=exc)


def machine_call_failed(exc: BaseException, *, path: str = "") -> MachineCallFailed:
    """The refusal to raise for ``exc``, one of :data:`MACHINE_FAILURES`."""
    row = _row_for(exc)
    if row.logged:
        _log(row.code, exc)
    return MachineCallFailed(
        row.message.format(path=path or "that path"),
        code=row.code,
        status_code=row.status,
    )


def is_missing_path(exc: BaseException) -> bool:
    """Whether ``exc`` says the path is not there -- an answer, for a listing."""
    return _row_for(exc).code == "MACHINE_PATH_NOT_FOUND"


class MachineFailureRoute(APIRoute):
    """A machine router's route class: whatever runtime failure escapes a
    handler is answered from the table, not as a 500.

    Handlers that know better -- a listing that reads a missing directory as
    an empty one, a startup probe that reports "unavailable" -- still catch
    first; this is for every call that had nothing to add.
    """

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def answered(request: Request) -> Response:
            try:
                return await handler(request)
            except MACHINE_FAILURES as exc:
                raise machine_call_failed(exc) from exc

        return answered


__all__ = [
    "MACHINE_FAILURES",
    "MachineCallFailed",
    "MachineFailureRoute",
    "is_missing_path",
    "machine_call_failed",
]
