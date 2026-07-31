from __future__ import annotations

from typing import Any
from uuid import UUID

from supertokens_python.recipe.emailverification.interfaces import (
    APIInterface,
    APIOptions,
    RecipeInterface,
    VerifyEmailUsingTokenOkResult,
)
from supertokens_python.recipe.session.asyncio import revoke_all_sessions_for_user
from supertokens_python.recipe.session.interfaces import SessionContainer
from supertokens_python.types.response import GeneralErrorResponse
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.message_bus import get_message_bus
from app.core.log.log import get_logger
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.user_cache import get_user_cache
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.services.auth_abuse import (
    RateLimitExceeded,
    get_auth_abuse_store,
)
from app.modules.identity.services.user_service import UserService


logger = get_logger(__name__)


async def _set_local_verification(user_id: str, *, verified: bool) -> None:
    try:
        parsed_id = UUID(user_id)
    except ValueError:
        logger.warning("identity.email_verification.invalid_local_user_id")
        return

    async with async_session_maker() as db_session:
        uow = SqlAlchemyUnitOfWork(db_session)
        repository = UserRepository(uow, message_bus=get_message_bus())
        user = await repository.get(parsed_id)
        if user is None:
            logger.warning("identity.email_verification.local_user_missing")
            return
        if verified:
            service = UserService(
                user_repository=repository,
                organization_repository=OrganizationRepository(
                    uow, message_bus=get_message_bus()
                ),
                user_cache=get_user_cache(),
            )
            await service.mark_email_verified(parsed_id)
        else:
            user.is_verified = False
            user.email_verified_at = None
            await repository.update(user)
            await get_user_cache().invalidate(parsed_id)
        await uow.commit()


def override_email_verification_functions(
    original_implementation: RecipeInterface,
) -> RecipeInterface:
    original_verify = original_implementation.verify_email_using_token
    original_unverify = original_implementation.unverify_email

    async def verify_email_using_token(
        token: str,
        tenant_id: str,
        attempt_account_linking: bool,
        user_context: dict[str, Any],
    ):
        result = await original_verify(
            token,
            tenant_id,
            attempt_account_linking,
            user_context,
        )
        if isinstance(result, VerifyEmailUsingTokenOkResult):
            await _set_local_verification(
                result.user.recipe_user_id.get_as_string(), verified=True
            )
        return result

    async def unverify_email(recipe_user_id, email: str, user_context: dict[str, Any]):
        result = await original_unverify(recipe_user_id, email, user_context)
        user_id = recipe_user_id.get_as_string()
        await _set_local_verification(user_id, verified=False)
        await revoke_all_sessions_for_user(user_id)
        return result

    original_implementation.verify_email_using_token = verify_email_using_token
    original_implementation.unverify_email = unverify_email
    return original_implementation


def override_email_verification_apis(
    original_implementation: APIInterface,
) -> APIInterface:
    """Apply the per-address resend limits that cannot be derived from the body."""
    original_generate = original_implementation.generate_email_verify_token_post

    async def generate_email_verify_token_post(
        session: SessionContainer,
        api_options: APIOptions,
        user_context: dict[str, Any],
    ):
        try:
            user_id = UUID(session.get_user_id())
        except ValueError:
            return GeneralErrorResponse("Unable to send verification email")
        async with async_session_maker() as db_session:
            email = await db_session.scalar(
                select(User.email).where(User.id == user_id)
            )
        if email:
            store = get_auth_abuse_store()
            email_hash = store.digest(email)
            try:
                await store.enforce(
                    f"identity:rate:email-action:email:15m:{email_hash}",
                    limit=3,
                    window_seconds=900,
                )
                await store.enforce(
                    f"identity:rate:email-action:email:day:{email_hash}",
                    limit=6,
                    window_seconds=86_400,
                )
            except RateLimitExceeded as exc:
                api_options.response.set_status_code(429)
                api_options.response.set_header(
                    "Retry-After", str(exc.retry_after_seconds)
                )
                return GeneralErrorResponse("Too many verification email requests")
        return await original_generate(session, api_options, user_context)

    original_implementation.generate_email_verify_token_post = (
        generate_email_verify_token_post
    )
    return original_implementation
