from typing import Any, Dict, Union
from uuid import UUID
from sqlalchemy import func, select

from supertokens_python.recipe.emailpassword.interfaces import (
    RecipeInterface,
    EmailAlreadyExistsError,
    SignUpOkResult,
)
from supertokens_python.recipe.session.interfaces import SessionContainer

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.events.message_bus import get_message_bus
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.services.user_service import UserService
from app.core.log.log import get_logger

from app.modules.identity.infrastructure.identity_lease import identity_lease
from app.modules.identity.infrastructure.supertokens_auth.auth_method_conflicts import (
    list_users_by_email,
)

logger = get_logger(__name__)


def override_emailpassword_functions(
    original_implementation: RecipeInterface,
) -> RecipeInterface:
    original_sign_up = original_implementation.sign_up

    async def sign_up(
        email: str,
        password: str,
        tenant_id: str,
        session: Union[SessionContainer, None],
        should_try_linking_with_session_user: Union[bool, None],
        user_context: Dict[str, Any],
    ):
        email = normalize_identity_email(email)
        async with identity_lease(f"account:{email}") as lease:
            async with async_session_maker() as db_session:
                local_user_id = await db_session.scalar(
                    select(User.id).where(func.lower(User.email) == email)
                )
            if local_user_id is not None:
                return EmailAlreadyExistsError()
            if await list_users_by_email(
                tenant_id=tenant_id, email=email, user_context=user_context
            ):
                return EmailAlreadyExistsError()
            await lease.require_ownership()
            result = await original_sign_up(
                email,
                password,
                tenant_id,
                session,
                should_try_linking_with_session_user,
                user_context,
            )

            await lease.require_ownership()
            if (
                isinstance(result, SignUpOkResult)
                and len(result.user.login_methods) == 1
            ):
                user_id = result.user.id
                emails = result.user.emails
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
                            id=UUID(user_id),
                            email=normalize_identity_email(emails[0]),
                            is_verified=False,
                            is_active=True,
                            is_superuser=False,
                            is_deleted=False,
                        ),
                        send_welcome=False,
                    )
                    await uow.commit()

            await lease.require_ownership()
            return result

    original_implementation.sign_up = sign_up

    return original_implementation
