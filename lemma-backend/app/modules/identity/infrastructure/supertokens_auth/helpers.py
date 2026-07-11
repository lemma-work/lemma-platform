from uuid import UUID
from supertokens_python.asyncio import get_user
from supertokens_python.recipe.session.asyncio import (
    create_new_session,
    create_new_session_without_request_response,
    refresh_session_without_request_response,
)
from app.core.log.log import get_logger
from app.core.authorization.delegation import (
    CLAIM_DELEGATION_VERSION,
    DELEGATION_VERSION,
)
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    validate_delegation_claims_payload,
)

logger = get_logger(__name__)


async def get_user_token(
    user_id: UUID,
    delegation_claims: dict | None = None,
) -> str:
    # we use the email password recipe here, but you can use the recipe you use
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
    logger.info("Delegated session created", user_id=str(user_id))
    return session.access_token


async def create_cli_session_tokens(
    user_id: UUID,
    *,
    access_token_payload: dict | None = None,
    session_data: dict | None = None,
) -> dict:
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
