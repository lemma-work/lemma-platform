"""Ending a deleted account's sign-in: every session, then the login itself.

Two steps, at two moments, and never inside the deletion's transaction: a
rolled-back deletion must not have signed anybody out.

- Straight after the commit, the request drops the account's cached standing
  (:func:`drop_cached_standing`). That is what refuses an access token that has
  not expired yet, so from this moment nothing the person holds opens anything.
  It is the only step the request takes, because it is the only one whose
  failures can be named: a SuperTokens core that answers with an error raises a
  bare ``Exception``, and an answer of 500 for an account that is already gone
  is a lie the person cannot correct -- their retry is refused as deleted.
- The ``identity.account_deleted`` work item, written with the deletion, does
  the rest (:func:`end_deleted_account_sign_in`): it revokes every session,
  has Apple revoke the person's Sign in with Apple, and deletes the
  SuperTokens user, and is retried until it lands. All three are safe to
  repeat: revoking sessions a user no longer has returns nothing, the kept
  Apple token is dropped once Apple has answered, and removing a user
  SuperTokens no longer holds is not an error.
- If that item dies -- Apple down for longer than the work runtime's day of
  waiting, say -- its ``on_dead`` hook writes one more,
  ``identity.account_deleted_without_apple``, which does the same work but no
  longer waits on Apple. The person was told the account is gone; the address
  must not stay taken because a third party was down.

Deleting the SuperTokens user is what frees the address: until it goes, the
same email signing up again would sign in to the deleted account instead, and
be refused.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import httpx
from redis.exceptions import RedisError
from supertokens_python.asyncio import delete_user as delete_supertokens_user
from supertokens_python.recipe.session.asyncio import revoke_all_sessions_for_user

from app.core.infrastructure.work.types import Unavailable
from app.core.log.log import get_logger
from app.modules.identity.infrastructure.supertokens_auth.apple import (
    revoke_apple_sign_in,
)
from app.modules.identity.infrastructure.user_cache import get_user_cache

logger = get_logger(__name__)


class ForgetsUsers(Protocol):
    async def invalidate(self, user_id: UUID) -> None: ...


async def drop_cached_standing(
    user_id: UUID, *, cache: ForgetsUsers | None = None
) -> bool:
    """Refuse the deleted account's live tokens now; ``False`` if Redis was down.

    A Redis that cannot be reached is not a reason to tell the person their
    deletion failed: it happened. The standing's own TTL is the backstop, and
    the work item drops it again before it signs anyone out.
    """
    try:
        await (cache or get_user_cache()).invalidate(user_id)
    except (RedisError, OSError) as unreachable:
        logger.warning(
            "identity.account.cached_standing_kept.degraded",
            user_id=str(user_id),
            error_type=type(unreachable).__name__,
        )
        return False
    return True


async def end_deleted_account_sign_in(
    user_id: UUID,
    *,
    apple_http: httpx.AsyncClient | None = None,
    wait_for_apple: bool = True,
) -> None:
    """End the deleted account's sign-in: cached standing, sessions, Apple, user.

    ``wait_for_apple=False`` is the second try, written when the first item
    died (`on_account_deleted_dead`): Apple is asked once more, and if it is
    still down the deletion finishes without it, because the person has been
    told the account is gone and the address must not stay taken.
    """
    # The cached standing first: until it is dropped, an access token that has
    # not expired still reads the account as live.
    await get_user_cache().invalidate(user_id)
    # Revoked before the user is deleted, through the session recipe, because
    # that is the path that announces the ended sessions and closes the
    # person's live sockets (`override_session.py`); deleting the user takes
    # the sessions too, but says nothing.
    await revoke_all_sessions_for_user(str(user_id))
    # Before the user goes, because the token goes with them: it is kept in the
    # SuperTokens user's metadata. Apple being down raises `Unavailable`, so
    # the item waits for it with the user, and the token, still there; an
    # answer Apple gives is final, and the deletion carries on whatever it is.
    try:
        await revoke_apple_sign_in(str(user_id), http=apple_http)
    except Unavailable as down:
        if wait_for_apple:
            raise
        # A day of waiting is spent. The authorization stays with Apple, where
        # the person can still remove it under their Apple ID's settings.
        logger.warning(
            "identity.apple_sign_in.revoke_abandoned",
            user_id=str(user_id),
            reason=str(down),
        )
    await delete_supertokens_user(str(user_id), remove_all_linked_accounts=True)


__all__ = ["drop_cached_standing", "end_deleted_account_sign_in"]
