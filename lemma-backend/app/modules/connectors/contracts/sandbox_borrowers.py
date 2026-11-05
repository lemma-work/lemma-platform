"""Somebody else's pod account, lent into a sandbox session, and taking it back.

The sandbox's git bridge (``agent/tools/workspace_cli/github_credential_bridge``)
writes a GitHub token into one session's directory in a person's sandbox.
When the token is somebody else's -- a SHARED connector's account whose owner
allowed use inside sandboxes -- the loan holds only while the whole pod-account
rule (``services/pod_account_rule``) still serves that account to that person
*and* the owner still allows it. Two things keep it to that:

- **Every use asks again.** ``borrowed_sandbox_use_still_allowed`` re-runs the
  rule for the borrowing person on each git command of a session holding a
  borrowed copy: the connector still runs as that pod account, the account is
  still shared with them (a RESTRICTED share they were dropped from serves them
  nothing), and the opt-in is still on. Reading only the opt-in let a token
  outlive a re-pointed connector or a narrowed share.
- **Changes reach every session that borrowed it.** The bridge records each
  borrowing session under the pod account (``note_sandbox_borrower``). Turning
  the opt-in off, withdrawing or narrowing the share, re-pointing or deleting
  the connector and the account leaving the pod call
  ``withdraw_sandbox_borrowers`` once committed, which marks every such
  session's credential marker withdrawn -- so its next git command takes the
  copy out and resolves afresh, not only the session that happened to run one.

A session that runs no further git command keeps its copy until the bridge's
stale-session sweep removes its directory (``SANDBOX_STALE_SESSION_MINUTES``).

The marker key is defined here rather than in the agent module because both
sides write it: the bridge per session, this module on withdrawal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Iterable
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

from app.core.log.log import get_logger

if TYPE_CHECKING:
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

logger = get_logger(__name__)

#: The per-session credential marker the git bridge keeps.
SANDBOX_CREDENTIAL_MARKER_PREFIX = "workspace:github-credentials:v1"
#: A marker set by a withdrawal: take the session's copy out, then ask again.
MARKER_WITHDRAWN = "withdrawn"
#: Longest a session's copy is trusted before it is rewritten; the borrower set
#: lives this long after its last addition, since no marker outlives it.
SANDBOX_CREDENTIAL_TTL_SECONDS = 45 * 60
#: A session directory nobody refreshed for this long is of a session that
#: ended (a live one is rewritten well within it), and the bridge sweeps it.
SANDBOX_STALE_SESSION_MINUTES = 2 * SANDBOX_CREDENTIAL_TTL_SECONDS // 60

_BORROWERS_KEY_PREFIX = "connectors:sandbox-borrowers:v1"

#: Said to the owner who turns ``allow_sandbox_use`` off.
SANDBOX_USE_WITHDRAWN_NOTICE = (
    "No sandbox is given this account's token from now on. Every session "
    "that holds a copy loses it on its next git command. A session that has "
    "ended keeps its copy until its directory is swept as stale, "
    f"{SANDBOX_STALE_SESSION_MINUTES} minutes after it was last refreshed; "
    "until then it can still be read there."
)


class BorrowerNoter(Protocol):
    def sadd(self, name: str, *values: str) -> Awaitable[object]: ...
    def expire(self, name: str, time: int) -> Awaitable[object]: ...


class BorrowerForgetter(Protocol):
    def srem(self, name: str, *values: str) -> Awaitable[object]: ...


class BorrowerStore(Protocol):
    """The Redis commands a withdrawal uses, so a test can hand in its own."""

    def smembers(self, name: str) -> Awaitable[Iterable[str | bytes]]: ...
    def delete(self, *names: str) -> Awaitable[object]: ...
    def set(self, name: str, value: str, *, ex: int) -> Awaitable[object]: ...


def sandbox_credential_marker_key(session_id: str) -> str:
    return f"{SANDBOX_CREDENTIAL_MARKER_PREFIX}:{session_id}"


def _borrowers_key(pod_account_id: UUID) -> str:
    return f"{_BORROWERS_KEY_PREFIX}:{pod_account_id}"


def _store() -> BorrowerStore:
    from app.core.config import settings
    from app.core.infrastructure.redis.client import get_redis

    return cast(BorrowerStore, get_redis(url=settings.redis_url))


async def note_sandbox_borrower(
    redis: BorrowerNoter, *, pod_account_id: UUID, session_id: str
) -> None:
    """Record that ``session_id`` holds a copy of this pod account's token."""
    key = _borrowers_key(pod_account_id)
    await redis.sadd(key, session_id)
    await redis.expire(key, SANDBOX_CREDENTIAL_TTL_SECONDS)


async def forget_sandbox_borrower(
    redis: BorrowerForgetter, *, pod_account_id: UUID, session_id: str
) -> None:
    await redis.srem(_borrowers_key(pod_account_id), session_id)


async def withdraw_sandbox_borrowers(
    pod_account_ids: Iterable[UUID], *, redis: BorrowerStore | None = None
) -> None:
    """Mark every session that borrowed these pod accounts withdrawn.

    Run after the change commits: before it, a session's next git command
    would re-check against the old state and pass. A Redis failure is logged
    and dropped -- each use still re-runs the rule, so the copy goes on that
    session's next git command either way.
    """
    from redis.exceptions import RedisError

    ids = sorted(set(pod_account_ids))
    if not ids:
        return
    store = redis if redis is not None else _store()
    try:
        for pod_account_id in ids:
            key = _borrowers_key(pod_account_id)
            members = await store.smembers(key)
            for raw in members:
                session_id = raw.decode() if isinstance(raw, bytes) else str(raw)
                await store.set(
                    sandbox_credential_marker_key(session_id),
                    MARKER_WITHDRAWN,
                    ex=SANDBOX_CREDENTIAL_TTL_SECONDS,
                )
            await store.delete(key)
    except RedisError, OSError:
        logger.warning(
            "connectors.sandbox_borrowers.withdraw_failed.degraded", exc_info=True
        )


def withdraw_sandbox_borrowers_after_commit(
    uow: "SqlAlchemyUnitOfWork", pod_account_ids: Iterable[UUID | None]
) -> None:
    ids = [pod_account_id for pod_account_id in pod_account_ids if pod_account_id]
    if ids:
        uow.after_commit(lambda: withdraw_sandbox_borrowers(ids))


async def borrowed_sandbox_use_still_allowed(
    uow: "SqlAlchemyUnitOfWork",
    *,
    pod_id: UUID,
    slug: str,
    pod_account_id: UUID,
    borrower: UUID,
) -> bool:
    """Whether ``borrower`` may still hold ``pod_account_id``'s token in a
    sandbox: the pod-account rule, run for them now, still lands on that
    account, it is still somebody else's, and its owner still allows it."""
    from app.modules.connectors.contracts.pod_connectors import (
        ConnectorDomainError,
    )
    from app.modules.connectors.services.pod_connector_composition import (
        build_pod_connector_resolver,
    )

    resolver = build_pod_connector_resolver(uow)
    if not await resolver.picker.is_pod_member(pod_id, borrower):
        return False
    try:
        connector = await resolver.connector(pod_id, slug)
        resolved = await resolver.resolve_standing(
            connector, on_behalf_of=borrower, record_use=False
        )
    except ConnectorDomainError:
        return False
    pod_account = resolved.pod_account
    return (
        pod_account.id == pod_account_id
        and pod_account.owner_user_id != borrower
        and pod_account.allow_sandbox_use
    )


__all__ = [
    "MARKER_WITHDRAWN",
    "SANDBOX_CREDENTIAL_MARKER_PREFIX",
    "SANDBOX_CREDENTIAL_TTL_SECONDS",
    "SANDBOX_STALE_SESSION_MINUTES",
    "SANDBOX_USE_WITHDRAWN_NOTICE",
    "BorrowerForgetter",
    "BorrowerNoter",
    "BorrowerStore",
    "borrowed_sandbox_use_still_allowed",
    "forget_sandbox_borrower",
    "note_sandbox_borrower",
    "sandbox_credential_marker_key",
    "withdraw_sandbox_borrowers",
    "withdraw_sandbox_borrowers_after_commit",
]
