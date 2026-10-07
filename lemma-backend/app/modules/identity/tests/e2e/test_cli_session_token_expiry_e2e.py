"""A CLI session's ``access_token_expires_at`` is its access token's own expiry.

A SuperTokens *session* outlives its access token by orders of magnitude: the
session lasts as long as its refresh token, the access token minutes to an
hour. The CLI session endpoints reported the session's expiry under the access
token's name, so a client that trusted the field would keep spending a token
months after the gateway had stopped accepting it.

Against the real core, because the defect was a wrong belief about what one of
its calls returns. A double of that call encodes the belief, right or wrong.
"""

from __future__ import annotations

from uuid import UUID

import jwt
import pytest

from app.modules.identity.infrastructure.supertokens_auth.helpers import (
    create_cli_session_tokens,
    refresh_cli_session_tokens,
)

pytestmark = pytest.mark.e2e


def _access_token_exp_ms(token: str) -> int:
    claims = jwt.decode(token, options={"verify_signature": False})
    return claims["exp"] * 1000


@pytest.mark.asyncio
async def test_minted_cli_session_reports_its_access_tokens_exp_in_milliseconds(
    fixed_test_user,
):
    minted = await create_cli_session_tokens(UUID(fixed_test_user["id"]))

    assert minted["access_token_expires_at"] == _access_token_exp_ms(
        minted["access_token"]
    )


@pytest.mark.asyncio
async def test_refreshed_cli_session_reports_the_new_access_tokens_exp_in_milliseconds(
    fixed_test_user,
):
    minted = await create_cli_session_tokens(UUID(fixed_test_user["id"]))

    refreshed = await refresh_cli_session_tokens(minted["refresh_token"])

    assert refreshed["access_token_expires_at"] == _access_token_exp_ms(
        refreshed["access_token"]
    )
