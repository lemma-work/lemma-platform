"""The expiry a minted workload token reports is the access token's own.

A SuperTokens *session* outlives its access token by orders of magnitude: the
session lasts as long as its refresh token, the access token minutes to an
hour. The expiry-bearing mint reported the session's, so the function session
cache -- which checks that expiry against a run's deadline -- handed out tokens
the gateway was already refusing.

Against the real core, because the defect was a wrong belief about what one of
its calls returns. A double of that call encodes the belief, right or wrong.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid7

import jwt
import pytest

from app.modules.identity.contracts.delegated_tokens import (
    mint_delegated_token_with_expiry,
)

pytestmark = pytest.mark.e2e


def _access_token_exp(token: str) -> datetime:
    claims = jwt.decode(token, options={"verify_signature": False})
    return datetime.fromtimestamp(claims["exp"], tz=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("delegated_tokens_enabled", [True, False])
async def test_minted_token_expiry_is_the_access_tokens_exp_not_the_sessions(
    fixed_test_user, delegated_tokens_enabled: bool
):
    issued = await mint_delegated_token_with_expiry(
        user_id=UUID(fixed_test_user["id"]),
        workload_type="function",
        workload_id=uuid7(),
        pod_id=uuid7(),
        session_id=f"function-session:{uuid7().hex}",
        workload_name="read_records",
        scope=None,
        delegated_tokens_enabled=delegated_tokens_enabled,
    )

    assert issued.expires_at == _access_token_exp(issued.value)
