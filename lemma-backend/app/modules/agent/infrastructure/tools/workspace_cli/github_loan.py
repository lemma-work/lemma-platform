"""A borrowed GitHub token in a sandbox session, re-checked on every use.

The git bridge writes the acting person's own account into their session, or
-- when its owner allows use inside sandboxes -- somebody else's account a
SHARED `github` connector runs as. The second is a loan, and the session's
marker records it: the pod account, the pod and the person it was lent to. On
each later git command the whole pod-account rule is run again for that
person (``connectors/contracts/sandbox_borrowers``); the first command after
it stops holding takes the copy back out. A withdrawal from outside (the owner
turning the opt-in off, the share or the connector changing) marks the
session ``withdrawn``, which takes the copy out and resolves afresh.
"""

from __future__ import annotations

import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.connectors.contracts.sandbox_borrowers import (
    MARKER_WITHDRAWN,
    borrowed_sandbox_use_still_allowed,
    forget_sandbox_borrower,
)
from app.modules.workspace.contracts.session_config import (
    session_gh_config_dir,
    session_git_credentials_path,
)

#: The pod connector slug the sandbox's git and gh run through.
POD_CONNECTOR_SLUG = "github"
#: The file `gh` reads its token from, in the session's `GH_CONFIG_DIR`.
GH_HOSTS_NAME = "hosts.yml"
_BORROWED_MARKER_PREFIX = "borrowed:"


@dataclass(frozen=True, slots=True)
class BorrowedLoan:
    """Somebody else's pod account, lent to one person's sandbox session."""

    pod_account_id: UUID
    pod_id: UUID
    borrower: UUID

    def marker(self) -> str:
        return (
            f"{_BORROWED_MARKER_PREFIX}{self.pod_account_id}:{self.pod_id}"
            f":{self.borrower}"
        )


class MarkerVerdict(Enum):
    #: Nothing to do: the session's copy still holds.
    KEEP = "keep"
    #: The loan stopped holding; its copy is gone and none is given.
    WITHDRAWN = "withdrawn"
    #: Its copy, if any, is gone; resolve afresh.
    RESOLVE = "resolve"


class _Session(Protocol):
    def exec_command(
        self, *, cmd: str, timeout: int | None = None
    ) -> Awaitable[object]: ...


class _Markers(Protocol):
    def set(self, name: str, value: str, *, ex: int) -> Awaitable[object]: ...
    def srem(self, name: str, *values: str) -> Awaitable[object]: ...


LoanCheck = Callable[[BorrowedLoan], Awaitable[bool]]


def borrowed_loan(text: str) -> BorrowedLoan | None:
    """The loan a session's marker records; None for anything else, including
    a marker written before it named the pod and the borrower."""
    if not text.startswith(_BORROWED_MARKER_PREFIX):
        return None
    try:
        pod_account_id, pod_id, borrower = text.removeprefix(
            _BORROWED_MARKER_PREFIX
        ).split(":")
        return BorrowedLoan(
            pod_account_id=UUID(pod_account_id),
            pod_id=UUID(pod_id),
            borrower=UUID(borrower),
        )
    except ValueError:
        return None


async def loan_still_holds(loan: BorrowedLoan) -> bool:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        return await borrowed_sandbox_use_still_allowed(
            uow,
            pod_id=loan.pod_id,
            slug=POD_CONNECTOR_SLUG,
            pod_account_id=loan.pod_account_id,
            borrower=loan.borrower,
        )


async def take_back(workspace_session: _Session, session_id: str) -> None:
    """Remove the session's copies of the token; the gitconfig holds none."""
    paths = (
        session_git_credentials_path(session_id),
        f"{session_gh_config_dir(session_id)}/{GH_HOSTS_NAME}",
    )
    await workspace_session.exec_command(
        cmd="rm -f " + " ".join(shlex.quote(path) for path in paths), timeout=15
    )


async def settle_marker(
    text: str,
    *,
    workspace_session: _Session,
    session_id: str,
    marker_key: str,
    redis: _Markers,
    still_holds: LoanCheck | None,
    unavailable_ttl_seconds: int,
) -> MarkerVerdict:
    """What a session's existing marker means for this git command."""
    if text != MARKER_WITHDRAWN and not text.startswith(_BORROWED_MARKER_PREFIX):
        return MarkerVerdict.KEEP
    loan = borrowed_loan(text)
    if loan is not None and await (still_holds or loan_still_holds)(loan):
        return MarkerVerdict.KEEP
    await take_back(workspace_session, session_id)
    if loan is None:
        # Withdrawn from outside, or an older marker that cannot be re-checked:
        # the connector may now run as an account this session may hold.
        return MarkerVerdict.RESOLVE
    await forget_sandbox_borrower(
        redis, pod_account_id=loan.pod_account_id, session_id=session_id
    )
    await redis.set(marker_key, "unavailable", ex=unavailable_ttl_seconds)
    return MarkerVerdict.WITHDRAWN


__all__ = [
    "GH_HOSTS_NAME",
    "POD_CONNECTOR_SLUG",
    "BorrowedLoan",
    "LoanCheck",
    "MarkerVerdict",
    "borrowed_loan",
    "loan_still_holds",
    "settle_marker",
    "take_back",
]
