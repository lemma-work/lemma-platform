"""Sessions that end take their live sockets with them.

A live socket (``app/core/live``) is authenticated once, at its handshake, so
signing out or having one's sessions revoked would otherwise leave it open and
streaming. Every way SuperTokens revokes -- ``/auth/signout``, a password
reset, an account deactivated -- goes through these three recipe functions;
each announces the sessions it ended once they are gone, and every API process
closes the sockets it holds for them.
"""

from __future__ import annotations

from typing import Any, Dict, List

from supertokens_python.recipe.session.interfaces import RecipeInterface

from app.core.live.sessions import announce_ended


def override_session_functions(
    original_implementation: RecipeInterface,
) -> RecipeInterface:
    original_revoke_session = original_implementation.revoke_session
    original_revoke_all = original_implementation.revoke_all_sessions_for_user
    original_revoke_multiple = original_implementation.revoke_multiple_sessions

    async def revoke_session(session_handle: str, user_context: Dict[str, Any]) -> bool:
        revoked = await original_revoke_session(session_handle, user_context)
        await announce_ended(handles=[session_handle])
        return revoked

    async def revoke_all_sessions_for_user(
        user_id: str,
        revoke_sessions_for_linked_accounts: bool,
        tenant_id: str,
        revoke_across_all_tenants: bool,
        user_context: Dict[str, Any],
    ) -> List[str]:
        revoked = await original_revoke_all(
            user_id,
            revoke_sessions_for_linked_accounts,
            tenant_id,
            revoke_across_all_tenants,
            user_context,
        )
        await announce_ended(handles=revoked, user_id=user_id)
        return revoked

    async def revoke_multiple_sessions(
        session_handles: List[str], user_context: Dict[str, Any]
    ) -> List[str]:
        revoked = await original_revoke_multiple(session_handles, user_context)
        await announce_ended(handles=revoked)
        return revoked

    original_implementation.revoke_session = revoke_session
    original_implementation.revoke_all_sessions_for_user = revoke_all_sessions_for_user
    original_implementation.revoke_multiple_sessions = revoke_multiple_sessions
    return original_implementation


__all__ = ["override_session_functions"]
