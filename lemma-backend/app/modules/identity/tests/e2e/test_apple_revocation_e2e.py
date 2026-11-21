"""A Sign in with Apple is revoked with Apple when the account is deleted.

Against the real SuperTokens core, its user metadata and the secret cipher.
Apple's two halves are stood in for: the provider's sign-in, in front of the
override (nothing here can complete a real Apple login), and Apple's revoke
endpoint, as an HTTP transport handed to the deletion.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import cast
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from supertokens_python.asyncio import get_user
from supertokens_python.recipe.thirdparty.asyncio import (
    manually_create_or_update_user,
)
from supertokens_python.recipe.thirdparty.interfaces import (
    ManuallyCreateOrUpdateUserOkResult,
    RecipeInterface,
    SignInUpOkResult,
)
from supertokens_python.recipe.thirdparty.types import RawUserInfoFromProvider
from supertokens_python.recipe.usermetadata.asyncio import get_user_metadata

from app.core.infrastructure.work.types import Unavailable
from app.modules.identity.config import identity_settings
from app.modules.identity.infrastructure.supertokens_auth.account_removal import (
    end_deleted_account_sign_in,
)
from app.modules.identity.infrastructure.supertokens_auth.apple import (
    APPLE_REVOKE_ENDPOINT,
    REVOCATION_METADATA_KEY,
    remember_apple_refresh_token,
)
from app.modules.identity.infrastructure.supertokens_auth.override_thirdparty import (
    override_thirdparty_functions,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.fixture
def apple_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )
    monkeypatch.setattr(identity_settings, "apple_client_id", "com.example.signin")
    monkeypatch.setattr(identity_settings, "apple_team_id", "TEAM123456")
    monkeypatch.setattr(identity_settings, "apple_key_id", "KEY1234567")
    monkeypatch.setattr(identity_settings, "apple_private_key", SecretStr(pem))


async def _apple_user(sub: str | None = None) -> ManuallyCreateOrUpdateUserOkResult:
    created = await manually_create_or_update_user(
        "public",
        "apple",
        sub or f"001234.{uuid4().hex}",
        f"{uuid4().hex}@example.com",
        True,
    )
    assert isinstance(created, ManuallyCreateOrUpdateUserOkResult)
    return created


def _apple_answering(
    *answers: httpx.Response,
) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    asked: list[httpx.Request] = []
    pending = list(answers)

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return pending.pop(0)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle)), asked


def _form(request: httpx.Request) -> dict[str, str]:
    return {
        key: values[0] for key, values in parse_qs(request.content.decode()).items()
    }


async def test_an_apple_account_deleted_is_revoked_with_apple_once(
    test_app: FastAPI, apple_configured: None
) -> None:
    del test_app
    sub = f"001234.{uuid4().hex}"
    created = await _apple_user(sub)
    user_id = created.user.id
    tokens: dict[str, object] = {
        "access_token": "a.access",
        "refresh_token": "r.refresh-token",
        "id_token": "x",
    }
    raw = RawUserInfoFromProvider({"aud": "com.example.signin", "sub": "s"}, None)

    async def provider_sign_in_up(*_args: object) -> SignInUpOkResult:
        return SignInUpOkResult(
            user=created.user,
            recipe_user_id=created.recipe_user_id,
            created_new_recipe_user=False,
            oauth_tokens=tokens,
            raw_user_info_from_provider=raw,
        )

    recipe = override_thirdparty_functions(
        cast(RecipeInterface, SimpleNamespace(sign_in_up=provider_sign_in_up))
    )
    signed_in = await recipe.sign_in_up(
        "apple",
        sub,
        created.user.emails[0],
        True,
        tokens,
        raw,
        None,
        None,
        "public",
        {},
    )
    assert isinstance(signed_in, SignInUpOkResult)

    kept = (await get_user_metadata(user_id)).metadata[REVOCATION_METADATA_KEY]
    assert kept["client_id"] == "com.example.signin"
    # Sealed, not stored as it came: the SuperTokens dashboard shows metadata.
    assert "r.refresh-token" not in str(kept)

    apple, asked = _apple_answering(httpx.Response(200))
    async with apple:
        await end_deleted_account_sign_in(UUID(user_id), apple_http=apple)
        # A retried work item finds nothing left to ask Apple for.
        await end_deleted_account_sign_in(UUID(user_id), apple_http=apple)

    [revoke] = asked
    assert str(revoke.url) == APPLE_REVOKE_ENDPOINT
    assert _form(revoke)["token"] == "r.refresh-token"
    assert _form(revoke)["client_id"] == "com.example.signin"
    assert await get_user(user_id) is None


async def test_apple_being_down_keeps_the_account_and_its_token_for_the_retry(
    test_app: FastAPI, apple_configured: None
) -> None:
    del test_app
    created = await _apple_user()
    user_id = created.user.id
    assert await remember_apple_refresh_token(
        user_id, {"refresh_token": "r.refresh-token"}, {"aud": "com.example.signin"}
    )

    apple, asked = _apple_answering(httpx.Response(503), httpx.Response(200))
    async with apple:
        with pytest.raises(Unavailable):
            await end_deleted_account_sign_in(UUID(user_id), apple_http=apple)
        assert await get_user(user_id) is not None
        assert REVOCATION_METADATA_KEY in (await get_user_metadata(user_id)).metadata

        await end_deleted_account_sign_in(UUID(user_id), apple_http=apple)

    assert [_form(request)["token"] for request in asked] == ["r.refresh-token"] * 2
    assert await get_user(user_id) is None


@pytest.mark.parametrize(
    "answer",
    [
        lambda: httpx.Response(400, json={"error": "invalid_grant"}),
        lambda: httpx.Response(400, json={"error": "invalid_client"}),
    ],
    ids=["already_spent", "refused"],
)
async def test_an_answer_apple_will_not_change_lets_the_deletion_finish(
    test_app: FastAPI,
    apple_configured: None,
    answer: Callable[[], httpx.Response],
) -> None:
    del test_app
    created = await _apple_user()
    user_id = created.user.id
    assert await remember_apple_refresh_token(
        user_id, {"refresh_token": "r.refresh-token"}, {"aud": "com.example.signin"}
    )

    apple, asked = _apple_answering(answer())
    async with apple:
        await end_deleted_account_sign_in(UUID(user_id), apple_http=apple)

    assert len(asked) == 1
    assert await get_user(user_id) is None
