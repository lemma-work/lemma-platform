from typing import Any, Dict

from supertokens_python.asyncio import list_users_by_account_info
from supertokens_python.recipe.thirdparty.types import ThirdPartyInfo
from supertokens_python.types import User
from supertokens_python.types.base import AccountInfoInput

from app.modules.identity.domain.email import normalize_identity_email


EMAIL_PASSWORD_SIGN_IN_METHOD = "email and password"
GOOGLE_SIGN_IN_METHOD = "Google"


async def list_users_by_email(
    *,
    tenant_id: str,
    email: str,
    user_context: Dict[str, Any],
) -> list[User]:
    email = normalize_identity_email(email)
    return await list_users_by_account_info(
        tenant_id=tenant_id,
        account_info=AccountInfoInput(email=email),
        do_union_of_account_info=False,
        user_context=user_context,
    )


def has_emailpassword_login_method(users: list[User], email: str) -> bool:
    email = normalize_identity_email(email)
    return any(
        login_method.recipe_id == "emailpassword" and login_method.has_same_email_as(email)
        for user in users
        for login_method in user.login_methods
    )


def has_thirdparty_login_method(
    users: list[User],
    *,
    email: str,
    third_party_id: str | None = None,
    third_party_user_id: str | None = None,
) -> bool:
    email = normalize_identity_email(email)
    return any(
        _matches_thirdparty_login_method(
            login_method,
            email=email,
            third_party_id=third_party_id,
            third_party_user_id=third_party_user_id,
        )
        for user in users
        for login_method in user.login_methods
    )


def get_conflicting_thirdparty_id(users: list[User], *, email: str) -> str | None:
    email = normalize_identity_email(email)
    for user in users:
        for login_method in user.login_methods:
            if login_method.recipe_id != "thirdparty" or not login_method.has_same_email_as(email):
                continue
            if login_method.third_party is not None:
                return login_method.third_party.id
    return None


def get_emailpassword_conflict_reason() -> str:
    return f"This email is already registered with {EMAIL_PASSWORD_SIGN_IN_METHOD}. Please sign in using your password."


def get_thirdparty_conflict_reason(third_party_id: str) -> str:
    provider_name = GOOGLE_SIGN_IN_METHOD if third_party_id == "google" else third_party_id
    return f"This email is already registered with {provider_name}. Please sign in using {provider_name}."


def _matches_thirdparty_login_method(
    login_method,
    *,
    email: str,
    third_party_id: str | None,
    third_party_user_id: str | None,
) -> bool:
    email = normalize_identity_email(email)
    if login_method.recipe_id != "thirdparty" or not login_method.has_same_email_as(email):
        return False

    if third_party_id is None:
        return True

    if third_party_user_id is None:
        return (
            login_method.third_party is not None
            and login_method.third_party.id == third_party_id
        )

    return login_method.has_same_third_party_info_as(
        ThirdPartyInfo(
            third_party_user_id=third_party_user_id,
            third_party_id=third_party_id,
        )
    )
