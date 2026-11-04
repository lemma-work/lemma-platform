"""Ending a deleted account's sign-in: every session, then the login itself.

Runs once the deletion has committed -- never inside it, because these are
calls to the SuperTokens core over HTTP, and a rolled-back deletion must not
have signed anybody out. Called twice for each deletion: straight after the
commit, so the person is signed out by the time the request answers, and by
the ``identity.account_deleted`` work item written with the deletion, which
retries until it succeeds. Both calls are safe to repeat: revoking sessions a
user no longer has returns nothing, and removing a user SuperTokens no longer
holds is not an error.

Deleting the SuperTokens user is what frees the address: until it goes, the
same email signing up again would sign in to the deleted account instead, and
be refused.
"""

from __future__ import annotations

from uuid import UUID

from supertokens_python.asyncio import delete_user as delete_supertokens_user
from supertokens_python.recipe.session.asyncio import revoke_all_sessions_for_user

from app.modules.identity.infrastructure.user_cache import get_user_cache


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


__all__ = ["end_deleted_account_sign_in"]
