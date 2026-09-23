from collections.abc import Awaitable, Callable
from typing import Any, Dict, List, Union

from supertokens_python.recipe.emailpassword.interfaces import (
    APIInterface,
    APIOptions,
    EmailAlreadyExistsError,
    GeneratePasswordResetTokenPostNotAllowedResponse,
    GeneratePasswordResetTokenPostOkResult,
    SignInPostNotAllowedResponse,
    SignInPostOkResult,
    SignUpPostNotAllowedResponse,
    SignUpPostOkResult,
    WrongCredentialsError,
)
from supertokens_python.types.response import GeneralErrorResponse
from supertokens_python.recipe.emailpassword.types import FormField
from supertokens_python.recipe.session.interfaces import SessionContainer

# Aliased: `User` in this module is the local ORM row, and the lookup below
# returns SuperTokens' own user, which is a different thing entirely.
from supertokens_python.types import User as AuthUser

from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.infrastructure.identity_lease import (
    IdentityLeaseLost,
    identity_lease,
)
from app.modules.identity.infrastructure.supertokens_auth.auth_method_conflicts import (
    get_conflicting_thirdparty_id,
    get_thirdparty_conflict_reason,
    has_emailpassword_login_method,
    has_passwordless_login_method,
    get_passwordless_conflict_reason,
    list_users_by_email,
)
from app.modules.identity.services.email_policy import (
    EmailPolicyError,
    validate_auth_email,
)
from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.infrastructure.models.user_models import User
from sqlalchemy import func, select


#: How this override finds out which login methods an address already has.
#: A named collaborator with a production default rather than a module global,
#: so a test can stand something in *front* of it. Reaching into this module to
#: patch the name would certify the half the test did not write, and would
#: survive a rename that ought to have failed.
UserLookup = Callable[..., Awaitable[List[AuthUser]]]


def override_emailpassword_apis(
    original_implementation: APIInterface,
    *,
    find_users: UserLookup = list_users_by_email,
) -> APIInterface:
    original_sign_in_post = original_implementation.sign_in_post
    original_sign_up_post = original_implementation.sign_up_post
    original_generate_password_reset_token_post = (
        original_implementation.generate_password_reset_token_post
    )

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
        try:
            email = _normalize_form_email(form_fields)
        except ValueError:
            # SuperTokens normally validates the field before this override, but
            # identity normalization is intentionally stricter for reserved and
            # malformed domains. Keep an invalid login indistinguishable from
            # wrong credentials instead of leaking it as an internal error.
            return SignInPostNotAllowedResponse(
                "Unable to sign in with these credentials"
            )
        async with async_session_maker() as db_session:
            local_user = await db_session.scalar(
                select(User).where(func.lower(User.email) == email)
            )
        if local_user is not None and not local_user.is_active:
            return SignInPostNotAllowedResponse(
                "Unable to sign in with these credentials"
            )
        users = await find_users(
            tenant_id=tenant_id,
            email=email,
            user_context=user_context,
        )

        if has_passwordless_login_method(users, email):
            return SignInPostNotAllowedResponse(get_passwordless_conflict_reason())

        if not has_emailpassword_login_method(users, email):
            conflicting_thirdparty_id = get_conflicting_thirdparty_id(
                users, email=email
            )
            if conflicting_thirdparty_id is not None:
                return SignInPostNotAllowedResponse(
                    get_thirdparty_conflict_reason(conflicting_thirdparty_id)
                )

        # Under the same lease account recovery takes, and for the whole of
        # verify-then-mint rather than either half. Recovery rotates the
        # password and then revokes sessions; without this a sign-in whose
        # credential was checked *before* the rotation can still have its
        # session minted *after* the revoke, and that session survives -- the
        # revoke only sweeps what already exists. Ordering the two operations
        # inside recovery cannot close that, because the gap is on this side.
        try:
            async with identity_lease(f"account:{_normalize_form_email(form_fields)}"):
                return await original_sign_in_post(
                    form_fields,
                    tenant_id,
                    session,
                    should_try_linking_with_session_user,
                    api_options,
                    user_context,
                )
        except IdentityLeaseLost:
            # Something else is mid-operation on this account -- recovery, most
            # likely, which is about to invalidate this very credential. Asking
            # for a retry is both true and the safe answer.
            return GeneralErrorResponse(
                "Sign-in is briefly unavailable for this account; try again."
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
        try:
            email = _normalize_form_email(form_fields)
        except ValueError:
            return SignUpPostNotAllowedResponse("Please use a valid email address")
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
        users = await find_users(
            tenant_id=tenant_id,
            email=email,
            user_context=user_context,
        )

        if has_passwordless_login_method(users, email):
            return SignUpPostNotAllowedResponse(get_passwordless_conflict_reason())

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

    async def generate_password_reset_token_post(
        form_fields: List[FormField],
        tenant_id: str,
        api_options: APIOptions,
        # `dict[str, object]`, where the recipe's own signature says
        # `Dict[str, Any]`. Nothing here reads inside it -- it is carried from
        # the caller to the original implementation untouched -- so `Any` would
        # be giving up a check this function never needed.
        user_context: dict[str, object],
    ) -> Union[
        GeneratePasswordResetTokenPostOkResult,
        GeneratePasswordResetTokenPostNotAllowedResponse,
        GeneralErrorResponse,
    ]:
        """Say so, rather than promising mail that cannot be sent.

        An account created through chat has one login method and it is
        passwordless. There is no password to reset, so Core mints no token and
        sends nothing -- while the page, which cannot tell that apart from a
        successful send, says to go and check an inbox that will stay empty.
        That was the likeliest way for somebody who signed up on WhatsApp to
        get permanently stuck: the door they were told to use does not exist.

        A `GeneralErrorResponse` rather than the shapelier
        `PASSWORD_RESET_NOT_ALLOWED`, because the page collapses that status
        into "sent" on purpose -- it is how a reset request avoids disclosing
        whether an account exists -- and the reason would be swallowed with it.
        This one is disclosure we have already chosen to make everywhere else,
        so it has to arrive as something the person actually reads.
        """
        try:
            email = _normalize_form_email(form_fields)
        except ValueError:
            return await original_generate_password_reset_token_post(
                form_fields, tenant_id, api_options, user_context
            )
        users = await find_users(
            tenant_id=tenant_id,
            email=email,
            user_context=user_context,
        )
        if has_passwordless_login_method(users, email):
            return GeneralErrorResponse(get_passwordless_conflict_reason())
        return await original_generate_password_reset_token_post(
            form_fields, tenant_id, api_options, user_context
        )

    original_implementation.sign_in_post = sign_in_post
    original_implementation.sign_up_post = sign_up_post
    original_implementation.generate_password_reset_token_post = (
        generate_password_reset_token_post
    )

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
