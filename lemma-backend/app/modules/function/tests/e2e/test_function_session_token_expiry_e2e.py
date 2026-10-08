"""The function session cache never serves a token past its access token's life.

The cache decides reuse by comparing a token's expiry against the run's
deadline, so it is only as good as the expiry the minter reports. That came from
the SuperTokens *session* -- months -- while the access token itself lasts an
hour by default, and the cache handed out tokens the gateway was refusing.
Driven through the production minter against the real core, because the defect
lived in what the issuer reports, not in the cache.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid7

import jwt
import pytest

from app.modules.function.api.dependencies import mint_function_session_token
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionTokenCache,
    FunctionSessionTokenKey,
)

pytestmark = pytest.mark.e2e


def _access_token_exp(token: str) -> datetime:
    claims = jwt.decode(token, options={"verify_signature": False})
    return datetime.fromtimestamp(claims["exp"], tz=timezone.utc)


@pytest.mark.asyncio
async def test_cached_function_token_is_reminted_for_a_window_its_access_token_does_not_cover(
    fixed_test_user,
):
    cache = FunctionSessionTokenCache()
    key = FunctionSessionTokenKey(
        user_id=UUID(fixed_test_user["id"]),
        pod_id=uuid7(),
        function_id=uuid7(),
        revision_hash=f"sha256:{'a' * 64}",
        workload_name="read_records",
        scope=(),
        delegated_tokens_enabled=True,
    )

    cached = await cache.get(key, minter=mint_function_session_token)
    assert cached.expires_at == _access_token_exp(cached.value)

    # A run whose window ends exactly when the cached access token dies.
    served = await cache.get(
        key,
        minter=mint_function_session_token,
        min_validity_until=_access_token_exp(cached.value),
    )

    assert served.value != cached.value
