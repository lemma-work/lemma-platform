"""The unit of work reachable from a repository or a bare session.

Services are constructed from a session, not from the unit of work wrapping it,
so a service that wants to defer a cache invalidation past the commit -- or end
the transaction before slow work -- has only the session to ask.
`SqlAlchemyUnitOfWork` leaves a back-reference in ``session.info`` for exactly
that. This module owns the key; nothing else should spell it.

Lookups are deliberately defensive, because the sources vary: a repository
double often has no ``session``, and a session double often has no ``info``.
A missing unit of work is not an error. It means there is nothing to defer to,
which is also the case where there is no pooled connection to give back.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, cast

SESSION_UOW_KEY = "lemma_uow"


class DeferrableUnitOfWork(Protocol):
    """What a service reaching back through a session actually needs.

    Narrower than `IUnitOfWork` on purpose: the return type says what may be
    called on the result, and `after_commit` is not part of the domain port.
    """

    async def commit(self) -> None: ...

    def after_commit(self, callback: Callable[[], Awaitable[object]]) -> None: ...


def active_uow(source: object) -> DeferrableUnitOfWork | None:
    """The unit of work wrapping ``source``, a repository or a session.

    Returns ``None`` when the chain is incomplete at any point -- no session, no
    ``info`` mapping, or no unit of work registered. Callers are expected to
    treat that as "do the work inline"; see `UserService._cache_after_commit`.
    """
    # `AsyncSession` has no `session` attribute, so this disambiguates a
    # repository from a session without either needing to declare which it is.
    session = getattr(source, "session", source)
    info = getattr(session, "info", None)
    if not isinstance(info, dict):
        return None
    # Returned without a runtime type check, deliberately. Only
    # `SqlAlchemyUnitOfWork.__init__` writes this slot, and a structural check
    # here would silently downgrade a test double that implements one half of
    # the protocol into "no unit of work" -- a green test for a fallback path
    # nobody asked for.
    return cast(DeferrableUnitOfWork | None, info.get(SESSION_UOW_KEY))


async def commit_now(source: object) -> None:
    """End the transaction on ``source``'s unit of work, handing the connection back.

    For the shape `connection_released` cannot serve: the caller has *written*,
    and is about to wait on something slow. `safe_to_release` correctly refuses
    a dirty session, so a release there would be a silent no-op -- the commit is
    the only thing that actually returns the connection to the pool.

    Doing nothing when there is no unit of work is not a skipped commit; it is
    the case where there is no pooled connection to give back. Written as one
    statement so the static gate can see that the release is unconditional --
    see `_is_commit_statement` in `scripts/check_session_scope.py`, which
    refuses to credit a commit nested in an `if`, and rightly.
    """
    uow = active_uow(source)
    if uow is not None:
        await uow.commit()
