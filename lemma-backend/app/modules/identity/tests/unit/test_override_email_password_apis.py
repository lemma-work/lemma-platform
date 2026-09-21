from types import SimpleNamespace

import pytest
from supertokens_python.recipe.emailpassword.interfaces import (
    SignInPostNotAllowedResponse,
    SignUpPostNotAllowedResponse,
)
from supertokens_python.recipe.emailpassword.types import FormField
from supertokens_python.types.response import GeneralErrorResponse

from app.modules.identity.infrastructure.supertokens_auth.override_email_password_apis import (
    override_emailpassword_apis,
)


def _passwordless_user(email: str):
    """Enough of a SuperTokens user for `has_passwordless_login_method` to run.

    Stood in front of the lookup rather than patched inside the override, so the
    predicate under test is the real one: a rename of `recipe_id`, or a change to
    how an address is compared, fails here instead of sailing past a stubbed
    answer.
    """
    return SimpleNamespace(
        login_methods=[
            SimpleNamespace(
                recipe_id="passwordless",
                has_same_email_as=lambda value, expected=email: value == expected,
            )
        ]
    )


def _finds(*users):
    async def _lookup(*, tenant_id, email, user_context):
        return list(users)

    return _lookup


async def _unexpected_original(*_args, **_kwargs):
    raise AssertionError("invalid identity input must not reach SuperTokens")


def _implementation(find_users=None, **originals):
    """The recipe's API surface, as far as this override actually reaches into it.

    Every method the override wraps has to be named here, including ones no
    test in this file calls: `override_emailpassword_apis` reads each original
    off the implementation at wrap time, so one this stand-in has never heard of
    is an `AttributeError` at import of the real app -- which is what happened
    when `generate_password_reset_token_post` was added and only the stub here
    still described the old surface.
    """
    return override_emailpassword_apis(
        SimpleNamespace(
            sign_in_post=originals.get("sign_in_post", _unexpected_original),
            sign_up_post=originals.get("sign_up_post", _unexpected_original),
            generate_password_reset_token_post=originals.get(
                "generate_password_reset_token_post", _unexpected_original
            ),
        ),
        **({} if find_users is None else {"find_users": find_users}),
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


@pytest.mark.asyncio
async def test_password_reset_tells_a_passwordless_account_which_door_it_has():
    """An account made through chat has no password, so there is none to reset.

    Core mints no token and sends nothing, while the page -- which cannot tell
    that from a successful send -- says to go and check an inbox that stays
    empty. Saying so is the only way out of that.
    """
    implementation = _implementation(
        find_users=_finds(_passwordless_user("chat@gmail.com"))
    )

    result = await implementation.generate_password_reset_token_post(
        [FormField("email", "chat@gmail.com")],
        "public",
        None,
        {},
    )

    assert isinstance(result, GeneralErrorResponse)
    assert "email-code login" in result.message


@pytest.mark.asyncio
async def test_password_reset_lets_an_ordinary_account_through():
    reached: dict[str, bool] = {}

    async def _original(*_args, **_kwargs):
        reached["original"] = True
        return GeneralErrorResponse("reached the recipe")

    implementation = _implementation(
        find_users=_finds(),
        generate_password_reset_token_post=_original,
    )

    await implementation.generate_password_reset_token_post(
        [FormField("email", "ordinary@gmail.com")],
        "public",
        None,
        {},
    )

    assert reached.get("original") is True


@pytest.mark.asyncio
async def test_password_reset_leaves_a_malformed_address_to_the_recipe():
    """No lookup is possible, and inventing a refusal here would be a new answer."""
    reached: dict[str, bool] = {}

    async def _original(*_args, **_kwargs):
        reached["original"] = True
        return GeneralErrorResponse("reached the recipe")

    implementation = _implementation(generate_password_reset_token_post=_original)

    await implementation.generate_password_reset_token_post(
        [FormField("email", "not-an-email")],
        "public",
        None,
        {},
    )

    assert reached.get("original") is True
