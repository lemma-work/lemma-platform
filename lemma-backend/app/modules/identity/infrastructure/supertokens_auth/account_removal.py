"""Ending a deleted account's sign-in: every session, then the login itself.

Runs once the deletion has committed -- never inside it, because these are
calls to the SuperTokens core over HTTP, and a rolled-back deletion must not
have signed anybody out. Called twice for each deletion: straight after the
commit, so the person is signed out by the time the request answers, and by
the ``identity.account_deleted`` work item written with the deletion, which
retries until it succeeds -- and is what finishes it when the first attempt
finds SuperTokens or Redis down. Both calls are safe to repeat: revoking
sessions a user no longer has returns nothing, and removing a user SuperTokens
no longer holds is not an error.

Deleting the SuperTokens user is what frees the address: until it goes, the
same email signing up again would sign in to the deleted account instead, and
be refused.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from redis.exceptions import RedisError
from supertokens_python.asyncio import delete_user as delete_supertokens_user
from supertokens_python.exceptions import SuperTokensError
from supertokens_python.recipe.session.asyncio import revoke_all_sessions_for_user

from app.core.log.log import get_logger
from app.modules.identity.infrastructure.user_cache import get_user_cache

logger = get_logger(__name__)

#: What reaching SuperTokens or Redis fails with when either is down. A core
#: that answers with an error is not among them: its querier raises a bare
#: ``Exception``, which is a fault worth its 500, not an outage to wait out.
_UNREACHABLE = (SuperTokensError, RedisError, OSError)


class EndsItself(Protocol):
    """The request's own session: revoking it clears its cookies or tokens."""

    async def revoke_session(self) -> None: ...


async def sign_out_after_deletion(
    session: EndsItself,
    user_id: UUID,
    *,
    remove: Callable[[UUID], Awaitable[None]] | None = None,
) -> bool:
    """End the deleted account's sign-in now; ``False`` if it had to be left.

    Called once the deletion has committed, so a failure here is not a reason
    to tell the person their account was not deleted -- it was, and nothing
    they can send again would reach this. What could not be done now is done
    by the ``identity.account_deleted`` work written with the deletion.

    Everywhere first, then this request's own session: the first drops the
    cached standing, which is what refuses a still-valid access token, and the
    second only clears what this response carries.
    """
    try:
        await (remove or end_deleted_account_sign_in)(user_id)
        await session.revoke_session()
    except _UNREACHABLE as unreachable:
        logger.warning(
            "identity.account.sign_in_removal_deferred.degraded",
            user_id=str(user_id),
            error_type=type(unreachable).__name__,
        )
        return False
    return True


async def end_deleted_account_sign_in(user_id: UUID) -> None:
    # The cached standing first: until it is dropped, an access token that has
    # not expired still reads the account as live.
    await get_user_cache().invalidate(user_id)
    # Revoked before the user is deleted, through the session recipe, because
    # that is the path that announces the ended sessions and closes the
    # person's live sockets (`override_session.py`); deleting the user takes
    # the sessions too, but says nothing.
    await revoke_all_sessions_for_user(str(user_id))
    await delete_supertokens_user(str(user_id), remove_all_linked_accounts=True)


__all__ = ["end_deleted_account_sign_in", "sign_out_after_deletion"]
