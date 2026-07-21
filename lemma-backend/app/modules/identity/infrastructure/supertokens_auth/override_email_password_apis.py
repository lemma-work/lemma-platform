from typing import Any, Dict, List, Union

from supertokens_python.recipe.emailpassword.interfaces import (
    APIInterface,
    APIOptions,
    EmailAlreadyExistsError,
    SignInPostNotAllowedResponse,
    SignInPostOkResult,
    SignUpPostNotAllowedResponse,
    SignUpPostOkResult,
    WrongCredentialsError,
)
from supertokens_python.types.response import GeneralErrorResponse
from supertokens_python.recipe.emailpassword.types import FormField
from supertokens_python.recipe.session.interfaces import SessionContainer

from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.infrastructure.supertokens_auth.auth_method_conflicts import (
    get_conflicting_thirdparty_id,
    get_thirdparty_conflict_reason,
    has_emailpassword_login_method,
    list_users_by_email,
)
from app.modules.identity.services.email_policy import (
    EmailPolicyError,
    validate_auth_email,
)
from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.infrastructure.models.user_models import User
from sqlalchemy import func, select


def override_emailpassword_apis(original_implementation: APIInterface) -> APIInterface:
    original_sign_in_post = original_implementation.sign_in_post
    original_sign_up_post = original_implementation.sign_up_post

    async def sign_in_post(
        form_fields: List[FormField],
        tenant_id: str,
        session: Union[SessionContainer, None],
        should_try_linking_with_session_user: Union[bool, None],
        api_options: APIOptions,
        user_context: Dict[str, Any],
    ) -> Union[
        SignInPostOkResult,
        WrongCredentialsError,
        SignInPostNotAllowedResponse,
        GeneralErrorResponse,
    ]:
        email = _normalize_form_email(form_fields)
        async with async_session_maker() as db_session:
            local_user = await db_session.scalar(
                select(User).where(func.lower(User.email) == email)
            )
        if local_user is not None and not local_user.is_active:
            return SignInPostNotAllowedResponse(
                "Unable to sign in with these credentials"
            )
        users = await list_users_by_email(
            tenant_id=tenant_id,
            email=email,
            user_context=user_context,
        )

        if not has_emailpassword_login_method(users, email):
            conflicting_thirdparty_id = get_conflicting_thirdparty_id(
                users, email=email
            )
            if conflicting_thirdparty_id is not None:
                return SignInPostNotAllowedResponse(
                    get_thirdparty_conflict_reason(conflicting_thirdparty_id)
                )

        return await original_sign_in_post(
            form_fields,
            tenant_id,
            session,
            should_try_linking_with_session_user,
            api_options,
            user_context,
        )

    async def sign_up_post(
        form_fields: List[FormField],
        tenant_id: str,
        session: Union[SessionContainer, None],
        should_try_linking_with_session_user: Union[bool, None],
        api_options: APIOptions,
        user_context: Dict[str, Any],
    ) -> Union[
        SignUpPostOkResult,
        EmailAlreadyExistsError,
        SignUpPostNotAllowedResponse,
        GeneralErrorResponse,
    ]:
        email = _normalize_form_email(form_fields)
        try:
            email = await validate_auth_email(email)
        except EmailPolicyError:
            return SignUpPostNotAllowedResponse(
                "Please use a valid, non-disposable email address"
            )
        for field in form_fields:
            if field.id == "email":
                field.value = email
                break
        users = await list_users_by_email(
            tenant_id=tenant_id,
            email=email,
            user_context=user_context,
        )

        if not has_emailpassword_login_method(users, email):
            conflicting_thirdparty_id = get_conflicting_thirdparty_id(
                users, email=email
            )
            if conflicting_thirdparty_id is not None:
                return SignUpPostNotAllowedResponse(
                    get_thirdparty_conflict_reason(conflicting_thirdparty_id)
                )

        return await original_sign_up_post(
            form_fields,
            tenant_id,
            session,
            should_try_linking_with_session_user,
            api_options,
            user_context,
        )

    original_implementation.sign_in_post = sign_in_post
    original_implementation.sign_up_post = sign_up_post

    return original_implementation


def _get_email(form_fields: List[FormField]) -> str:
    return next(field.value for field in form_fields if field.id == "email")


def _normalize_form_email(form_fields: List[FormField]) -> str:
    email = normalize_identity_email(_get_email(form_fields))
    for field in form_fields:
        if field.id == "email":
            field.value = email
            break
    return email
