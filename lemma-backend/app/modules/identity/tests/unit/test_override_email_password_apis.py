from types import SimpleNamespace

import pytest
from supertokens_python.recipe.emailpassword.interfaces import (
    SignInPostNotAllowedResponse,
    SignUpPostNotAllowedResponse,
)
from supertokens_python.recipe.emailpassword.types import FormField

from app.modules.identity.infrastructure.supertokens_auth.override_email_password_apis import (
    override_emailpassword_apis,
)


async def _unexpected_original(*_args, **_kwargs):
    raise AssertionError("invalid identity input must not reach SuperTokens")


def _implementation():
    return override_emailpassword_apis(
        SimpleNamespace(
            sign_in_post=_unexpected_original,
            sign_up_post=_unexpected_original,
        )
    )


@pytest.mark.asyncio
async def test_signup_rejects_invalid_email_as_user_error():
    implementation = _implementation()

    result = await implementation.sign_up_post(
        [FormField("email", "owner@example.test")],
        "public",
        None,
        None,
        None,
        {},
    )

    assert isinstance(result, SignUpPostNotAllowedResponse)
    assert result.reason == "Please use a valid email address"


@pytest.mark.asyncio
async def test_signin_keeps_invalid_email_indistinguishable_from_bad_credentials():
    implementation = _implementation()

    result = await implementation.sign_in_post(
        [FormField("email", "not-an-email")],
        "public",
        None,
        None,
        None,
        {},
    )

    assert isinstance(result, SignInPostNotAllowedResponse)
    assert result.reason == "Unable to sign in with these credentials"
