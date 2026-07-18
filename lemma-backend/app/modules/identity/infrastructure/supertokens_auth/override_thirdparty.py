from typing import Any, Dict, Optional, Union

from supertokens_python.recipe.session.interfaces import SessionContainer
from supertokens_python.recipe.thirdparty.interfaces import (
    RecipeInterface,
    SignInUpNotAllowed,
    SignInUpOkResult,
)
from supertokens_python.recipe.thirdparty.types import RawUserInfoFromProvider

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.infrastructure.supertokens_auth.auth_method_conflicts import (
    get_emailpassword_conflict_reason,
    has_emailpassword_login_method,
    has_thirdparty_login_method,
    list_users_by_email,
)
from app.modules.identity.services.user_service import UserService
from app.core.log.log import get_logger

logger = get_logger(__name__)


def override_thirdparty_functions(
    original_implementation: RecipeInterface,
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
        users = await list_users_by_email(
            tenant_id=tenant_id,
            email=email,
            user_context=user_context,
        )
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

        if isinstance(result, SignInUpOkResult):
            if (
                session is None
                and result.created_new_recipe_user
                and len(result.user.login_methods) == 1
            ):
                async with async_session_maker() as db_session:
                    uow = SqlAlchemyUnitOfWork(db_session)
                    message_bus = get_message_bus()
                    user_service = UserService(
                        user_repository=UserRepository(uow, message_bus=message_bus),
                        organization_repository=OrganizationRepository(
                            uow, message_bus=message_bus
                        ),
                    )
                    await user_service.create_user(
                        UserEntity(
                            id=result.user.id,
                            email=normalize_identity_email(result.user.emails[0]),
                            is_verified=True,
                            is_active=True,
                            is_superuser=False,
                            is_deleted=False,
                        )
                    )
                    await uow.commit()

        return result

    original_implementation.sign_in_up = sign_in_up

    return original_implementation
