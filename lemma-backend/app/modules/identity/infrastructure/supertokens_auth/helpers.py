from uuid import UUID
from sqlalchemy import select
from supertokens_python.asyncio import get_user
from supertokens_python.recipe.session.asyncio import (
    create_new_session,
    create_new_session_without_request_response,
    refresh_session_without_request_response,
)
from app.core.config import settings
from app.core.log.log import get_logger
from app.core.authorization.delegation import (
    CLAIM_DELEGATION_VERSION,
    DELEGATION_VERSION,
)
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    validate_delegation_claims_payload,
)
from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.infrastructure.models.user_models import User

logger = get_logger(__name__)


async def _assert_local_user_can_authenticate(user_id: UUID) -> None:
    async with async_session_maker() as session:
        state = (
            await session.execute(
                select(User.is_active, User.is_verified, User.is_deleted).where(
                    User.id == user_id
                )
            )
        ).first()
    if (
        state is None
        or not state.is_active
        or state.is_deleted
        or (settings.auth_email_verification_required and not state.is_verified)
    ):
        raise ValueError("User is not eligible for an authenticated session")


async def get_user_token(
    user_id: UUID,
    delegation_claims: dict | None = None,
) -> str:
    # we use the email password recipe here, but you can use the recipe you use
    await _assert_local_user_can_authenticate(user_id)
    user = await get_user(str(user_id))

    if user is None:
        raise ValueError(f"User {user_id} not found")

    payload: dict = {"isImpersonation": True}
    if delegation_claims:
        payload.update(validate_delegation_claims_payload(delegation_claims))
        payload.setdefault(CLAIM_DELEGATION_VERSION, DELEGATION_VERSION)

    session = await create_new_session_without_request_response(
        "public",
        user.login_methods[0].recipe_user_id,
        payload,
    )
    return session.access_token


async def create_cli_session_tokens(
    user_id: UUID,
    *,
    access_token_payload: dict | None = None,
    session_data: dict | None = None,
) -> dict:
    await _assert_local_user_can_authenticate(user_id)
    user = await get_user(str(user_id))

    if user is None:
        raise ValueError(f"User {user_id} not found")

    session = await create_new_session_without_request_response(
        "public",
        user.login_methods[0].recipe_user_id,
        access_token_payload=access_token_payload or {"client": "lemma-cli"},
        session_data_in_database=session_data or {},
        disable_anti_csrf=True,
    )
    tokens = session.get_all_session_tokens_dangerously()

    return {
        "access_token": tokens["accessToken"],
        "refresh_token": tokens["refreshToken"],
        "access_token_expires_at": await session.get_expiry(),
        "session_handle": session.get_handle(),
        "user_id": str(user_id),
    }


async def create_desktop_browser_session(request, user_id: UUID) -> str:
    """Create a cookie session on the current webview exchange response."""
    await _assert_local_user_can_authenticate(user_id)
    user = await get_user(str(user_id))

    if user is None or not user.login_methods:
        raise ValueError(f"User {user_id} not found")

    session = await create_new_session(
        request,
        "public",
        user.login_methods[0].recipe_user_id,
        access_token_payload={"client": "lemma-desktop"},
        session_data_in_database={"client": "lemma-desktop"},
    )
    return session.get_handle()


async def create_browser_session(request, user_id: UUID, *, client: str) -> str:
    """Create a normal cookie session for a verified non-SuperTokens login flow."""
    await _assert_local_user_can_authenticate(user_id)
    user = await get_user(str(user_id))
    if user is None or not user.login_methods:
        raise ValueError(f"User {user_id} not found")

    # A top-level browser navigation does not carry the SuperTokens frontend
    # SDK's ``st-auth-mode: cookie`` header. Without forcing cookie transfer,
    # SuperTokens defaults to response-header tokens, which a redirecting
    # browser cannot persist and the Telegram login appears to succeed without
    # actually signing the user in.
    raw_headers = [
        (key, value)
        for key, value in request.scope.get("headers", [])
        if key.lower() != b"st-auth-mode"
    ]
    raw_headers.append((b"st-auth-mode", b"cookie"))
    request.scope["headers"] = raw_headers
    request.__dict__.pop("_headers", None)

    created = await create_new_session(
        request,
        "public",
        user.login_methods[0].recipe_user_id,
        access_token_payload={"client": client},
        session_data_in_database={"client": client},
    )
    return created.get_handle()


async def refresh_cli_session_tokens(refresh_token: str) -> dict:
    session = await refresh_session_without_request_response(
        refresh_token=refresh_token,
        disable_anti_csrf=True,
    )
    tokens = session.get_all_session_tokens_dangerously()

    return {
        "access_token": tokens["accessToken"],
        "refresh_token": tokens["refreshToken"],
        "access_token_expires_at": await session.get_expiry(),
        "session_handle": session.get_handle(),
        "user_id": session.get_user_id(),
    }
