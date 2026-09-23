"""What the in-sandbox runtime can refuse, and how a caller must read it.

Its own module because the client was at the size limit and this is the part
with no behaviour in it: a vocabulary, plus the two tables that say which HTTP
status means which word. Both `_ops` scopes branch on these types, so they are
the contract between the runtime and every fabric that speaks to it.
"""

from __future__ import annotations

from typing import Mapping


class WorkspaceRuntimeError(RuntimeError):
    pass


class WorkspaceRuntimeStartAmbiguous(WorkspaceRuntimeError):
    pass


class WorkspaceRuntimePythonAmbiguous(WorkspaceRuntimeError):
    pass


class WorkspaceRuntimeFileNotFound(WorkspaceRuntimeError):
    pass


class WorkspaceRuntimeFileConflict(WorkspaceRuntimeError):
    pass


class WorkspaceBrowserNotRunning(WorkspaceRuntimeError):
    """No browser to attach to.

    Its own type because it is not a failure: a workspace whose browser has been
    shed — for idleness or memory — is the ordinary resting state, and the
    caller wants to say "nothing to watch yet" rather than "something broke".
    """


class WorkspaceRuntimeFileRejected(WorkspaceRuntimeError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class WorkspaceRuntimeUnauthorized(WorkspaceRuntimeError):
    """The runtime refused this caller's credential.

    Its own type, and recognised for every endpoint rather than per-call,
    because a rejected credential is the one runtime answer that waiting cannot
    fix. Without it a 401 fell through to the bare `WorkspaceRuntimeError` that
    `_status_error` returns for anything unmapped, which both `_ops` scopes turn
    into `SandboxUnavailable` -- the retryable word. Every caller then retried a
    refusal until its deadline and reported a timeout, which says nothing about
    a credential.
    """

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class WorkspaceRuntimeProcessGone(WorkspaceRuntimeError):
    """The runtime has never heard of this process, or no longer has it.

    Its own type because it is the one process answer that waiting cannot fix.
    Unmapped, a 404 became the bare `WorkspaceRuntimeError` that `_status_error`
    returns by default, which both `_ops` scopes turn into `SandboxUnavailable`
    -- so a caller polling a process id that does not exist retried until its
    deadline and then reported a timeout.
    """


#: A 404 from any of the process endpoints. Deliberately separate from the
#: filesystem table: there, 404 means a path is absent and a caller may create
#: it; here it means the process is not coming back.
_PROCESS_STATUS_ERRORS: Mapping[int, type[WorkspaceRuntimeError]] = {
    404: WorkspaceRuntimeProcessGone,
}

_FILESYSTEM_STATUS_ERRORS: Mapping[int, type[WorkspaceRuntimeError]] = {
    404: WorkspaceRuntimeFileNotFound,
    409: WorkspaceRuntimeFileConflict,
    413: WorkspaceRuntimeFileRejected,
    422: WorkspaceRuntimeFileRejected,
    507: WorkspaceRuntimeFileRejected,
}
