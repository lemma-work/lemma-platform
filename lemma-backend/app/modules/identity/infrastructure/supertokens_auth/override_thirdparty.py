from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union
from uuid import UUID

from supertokens_python.recipe.session.interfaces import SessionContainer
from supertokens_python.recipe.thirdparty.interfaces import (
    RecipeInterface,
    SignInUpNotAllowed,
    SignInUpOkResult,
)
from supertokens_python.recipe.thirdparty.types import RawUserInfoFromProvider
from sqlalchemy import func, select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.errors import SignupNotAllowedError
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.services.signup_gate import get_signup_gate
from app.modules.identity.infrastructure.supertokens_auth.provider_profile import (
    names_from_provider,
)
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.infrastructure.supertokens_auth.auth_method_conflicts import (
    get_emailpassword_conflict_reason,
    has_emailpassword_login_method,
    has_passwordless_login_method,
    get_passwordless_conflict_reason,
    has_thirdparty_login_method,
    list_users_by_email,
)
from app.modules.identity.services.user_service import UserService
from app.core.log.log import get_logger

from app.modules.identity.infrastructure.identity_lease import identity_lease

logger = get_logger(__name__)


#: See `AdmitSignup` in `override_email_password_apis`; the same gate, because
#: an OAuth provider is just another way to arrive at the sign-up page.
AdmitSignup = Callable[[str], Awaitable[object]]


async def _admit_signup(email: str) -> object:
    return await get_signup_gate().admit(email)


async def _signup_refusal(
    admit_signup: AdmitSignup, email: str, *, linking: bool, known: bool
) -> SignInUpNotAllowed | None:
    if linking or known:
        return None
    try:
        await admit_signup(email)
    except SignupNotAllowedError as refused:
        return SignInUpNotAllowed(refused.message)
    return None


def override_thirdparty_functions(
    original_implementation: RecipeInterface,
    *,
    admit_signup: AdmitSignup = _admit_signup,
) -> RecipeInterface:
    original_sign_in_up = original_implementation.sign_in_up

    async def sign_in_up(
        third_party_id: str,
        third_party_user_id: str,
        email: str,
        is_verified: bool,
        oauth_tokens: Dict[str, Any],
        raw_user_info_from_provider: RawUserInfoFromProvider,
        session: Optional[SessionContainer],
        should_try_linking_with_session_user: Union[bool, None],
        tenant_id: str,
        user_context: Dict[str, Any],
    ):
        email = normalize_identity_email(email)
        async with identity_lease(f"account:{email}") as lease:
            async with async_session_maker() as db_session:
                local_user = await db_session.scalar(
                    select(User).where(func.lower(User.email) == email)
                )
            if local_user is not None and (
                not local_user.is_active or local_user.is_deleted
            ):
                return SignInUpNotAllowed("Unable to sign in with this account")
            users = await list_users_by_email(
                tenant_id=tenant_id,
                email=email,
                user_context=user_context,
            )
            if has_passwordless_login_method(users, email):
                return SignInUpNotAllowed(get_passwordless_conflict_reason())
            has_matching_thirdparty_user = has_thirdparty_login_method(
                users,
                email=email,
                third_party_id=third_party_id,
                third_party_user_id=third_party_user_id,
            )

            if not has_matching_thirdparty_user and has_emailpassword_login_method(
                users, email
            ):
                return SignInUpNotAllowed(get_emailpassword_conflict_reason())

            # A sign-*up* is the case where neither Lemma nor SuperTokens knows
            # this person yet. Anyone already here signs in regardless of the
            # mode -- closing signup must not lock existing members out -- and
            # a call carrying a session is linking an account, not creating one.
            refusal = await _signup_refusal(
                admit_signup,
                email,
                linking=session is not None,
                known=local_user is not None or has_matching_thirdparty_user,
            )
            if refusal is not None:
                return refusal

            result = await original_sign_in_up(
                third_party_id,
                third_party_user_id,
                email,
                is_verified,
                oauth_tokens,
                raw_user_info_from_provider,
                session,
                should_try_linking_with_session_user,
                tenant_id,
                user_context,
            )

            await lease.require_ownership()
            if isinstance(result, SignInUpOkResult):
                if (
                    session is None
                    and local_user is None
                    and len(result.user.login_methods) == 1
                ):
                    async with async_session_maker() as db_session:
                        uow = SqlAlchemyUnitOfWork(db_session)
                        message_bus = get_message_bus()
                        user_service = UserService(
                            user_repository=UserRepository(
                                uow, message_bus=message_bus
                            ),
                            organization_repository=OrganizationRepository(
                                uow, message_bus=message_bus
                            ),
                        )
                        # The provider just told us who this is. Storing it here is
                        # what lets the first screen after signup be the product
                        # rather than a form asking for a name we were handed.
                        first_name, last_name = names_from_provider(
                            raw_user_info_from_provider.from_id_token_payload,
                            raw_user_info_from_provider.from_user_info_api,
                        )
                        await user_service.create_user(
                            UserEntity(
                                id=UUID(result.user.id),
                                email=normalize_identity_email(result.user.emails[0]),
                                first_name=first_name,
                                last_name=last_name,
                                is_verified=is_verified,
                                email_verified_at=(
                                    datetime.now(timezone.utc) if is_verified else None
                                ),
                                is_active=True,
                                is_superuser=False,
                                is_deleted=False,
                            ),
                            send_welcome=is_verified,
                        )
                        await uow.commit()

            await lease.require_ownership()
            return result

    original_implementation.sign_in_up = sign_in_up

    return original_implementation
