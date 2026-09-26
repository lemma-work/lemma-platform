from types import SimpleNamespace

import pytest
from supertokens_python.recipe.emailpassword.interfaces import (
    EmailAlreadyExistsError,
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
        delivery_state=originals.get("delivery_state", lambda: "sending"),
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


@pytest.mark.asyncio
async def test_signup_hands_the_gate_the_invitation_the_request_presented(
    monkeypatch,
):
    """The invitation link's id travels in a header, and reaches the gate.

    A password sign-up does not prove its address when email verification is
    off, so the gate needs the invitation itself -- and it can only judge what
    the override actually passes it.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "auth_email_deliverability_checks_enabled", False)
    monkeypatch.setattr(settings, "auth_disposable_email_domains_enabled", False)
    admitted: list[tuple[str, str | None]] = []

    async def admit(email: str, invitation_id: str | None) -> object:
        admitted.append((email, invitation_id))
        return object()

    async def original_sign_up(*_args, **_kwargs):
        return "signed-up"

    implementation = override_emailpassword_apis(
        SimpleNamespace(
            sign_in_post=_unexpected_original,
            sign_up_post=original_sign_up,
            generate_password_reset_token_post=_unexpected_original,
        ),
        find_users=_finds(),
        admit_signup=admit,
    )
    request = SimpleNamespace(
        get_header=lambda name: (
            "6f1d8c1e-2c2a-4b0e-9d3b-0c9a0f5e7a11"
            if name == "x-lemma-invitation"
            else None
        )
    )

    result = await implementation.sign_up_post(
        [FormField("email", "guest@lemma.work"), FormField("password", "pw")],
        "public",
        None,
        None,
        SimpleNamespace(request=request),
        {},
    )

    assert result == "signed-up"
    assert admitted == [("guest@lemma.work", "6f1d8c1e-2c2a-4b0e-9d3b-0c9a0f5e7a11")]


def _password_user(email: str):
    return SimpleNamespace(
        login_methods=[
            SimpleNamespace(
                recipe_id="emailpassword",
                has_same_email_as=lambda value, expected=email: value == expected,
            )
        ]
    )


@pytest.mark.asyncio
async def test_signup_for_an_existing_password_account_says_sign_in_before_the_gate(
    monkeypatch,
):
    """A member of an invite-only Lemma is told to sign in, not to get invited.

    The gate is not consulted: the address already has an account, so no new
    account is being asked for, and the answer is the recipe's own
    `EMAIL_ALREADY_EXISTS_ERROR` that an open deployment already gives.
    """
    from app.core.config import settings
    from app.modules.identity.domain.errors import SignupNotAllowedError

    monkeypatch.setattr(settings, "auth_email_deliverability_checks_enabled", False)
    monkeypatch.setattr(settings, "auth_disposable_email_domains_enabled", False)

    async def refuse(email: str, invitation_id: str | None) -> object:
        raise SignupNotAllowedError(SignupNotAllowedError.INVITE_ONLY)

    implementation = override_emailpassword_apis(
        SimpleNamespace(
            sign_in_post=_unexpected_original,
            sign_up_post=_unexpected_original,
            generate_password_reset_token_post=_unexpected_original,
        ),
        find_users=_finds(_password_user("member@lemma.work")),
        admit_signup=refuse,
    )

    result = await implementation.sign_up_post(
        [FormField("email", "member@lemma.work"), FormField("password", "pw")],
        "public",
        None,
        None,
        SimpleNamespace(request=SimpleNamespace(get_header=lambda _name: None)),
        {},
    )

    assert isinstance(result, EmailAlreadyExistsError)


@pytest.mark.asyncio
async def test_signup_for_a_new_address_still_meets_the_gate(monkeypatch):
    from app.core.config import settings
    from app.modules.identity.domain.errors import SignupNotAllowedError

    monkeypatch.setattr(settings, "auth_email_deliverability_checks_enabled", False)
    monkeypatch.setattr(settings, "auth_disposable_email_domains_enabled", False)

    async def refuse(email: str, invitation_id: str | None) -> object:
        raise SignupNotAllowedError(SignupNotAllowedError.INVITE_ONLY)

    implementation = override_emailpassword_apis(
        SimpleNamespace(
            sign_in_post=_unexpected_original,
            sign_up_post=_unexpected_original,
            generate_password_reset_token_post=_unexpected_original,
        ),
        find_users=_finds(_password_user("someone-else@lemma.work")),
        admit_signup=refuse,
    )

    result = await implementation.sign_up_post(
        [FormField("email", "stranger@lemma.work"), FormField("password", "pw")],
        "public",
        None,
        None,
        SimpleNamespace(request=SimpleNamespace(get_header=lambda _name: None)),
        {},
    )

    assert isinstance(result, SignUpPostNotAllowedResponse)
    assert result.reason == SignupNotAllowedError.INVITE_ONLY_MESSAGE


@pytest.mark.asyncio
async def test_password_reset_without_email_says_so_for_every_address():
    """No promise of a link that has nowhere to go -- and no account oracle.

    Answered before the address is looked up, so every address, with an
    account or without, gets the same sentence.
    """
    from app.modules.identity.infrastructure.supertokens_auth.override_email_password_apis import (
        PASSWORD_RESET_NOT_CONFIGURED_MESSAGE,
    )

    async def _no_lookup(**_kwargs):
        raise AssertionError("the address must not be looked up")

    implementation = _implementation(
        find_users=_no_lookup, delivery_state=lambda: "not_configured"
    )

    result = await implementation.generate_password_reset_token_post(
        [FormField("email", "anyone@lemma.work")], "public", SimpleNamespace(), {}
    )

    assert isinstance(result, GeneralErrorResponse)
    assert result.message == PASSWORD_RESET_NOT_CONFIGURED_MESSAGE
