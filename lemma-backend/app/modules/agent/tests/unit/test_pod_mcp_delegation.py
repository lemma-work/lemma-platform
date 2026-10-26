"""Which tokens the pod MCP mounts honour, and as whom.

`/agent-runtime/pods` and `/mcp/{pod}` are ASGI mounts the global `verify_auth`
dependency never runs on, so the service is the only gate a Lemma session
passes there. It used to keep a weaker copy of the dependency's checks: a
malformed claim read as a plain user session, a function's token kept the
person's full authority in any pod, a revoked workload's token still worked,
and switching delegated tokens off promoted them to user sessions. These pin
that it now refuses each of those exactly as the HTTP dependency does.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID, uuid4

import pytest

from app.core.auth_state_cache import AccountStanding
from app.core.authorization.delegation import (
    CLAIM_ACTOR_ID,
    CLAIM_ACTOR_NAME,
    CLAIM_ACTOR_TYPE,
    CLAIM_DELEGATION_VERSION,
    CLAIM_INVOKED_BY_USER_ID,
    CLAIM_POD_ID,
    CLAIM_SCOPE,
    CLAIM_SESSION_ID,
    DELEGATION_VERSION,
)
from app.core.config import settings
from app.core.security import PrincipalLookups
from app.modules.agent.services.pod_mcp_service import PodMCPService, VerifiedSession
from app.modules.agent.infrastructure.tools.authority import workload_principal_type

pytestmark = pytest.mark.unit

_GOOD_STANDING = AccountStanding(is_active=True, is_verified=True, is_deleted=False)


def _service(
    *,
    user_id: UUID,
    payload: Mapping[str, object],
    standing: AccountStanding | None = _GOOD_STANDING,
    revoked: frozenset[UUID] = frozenset(),
) -> PodMCPService:
    async def verify(_token: str) -> VerifiedSession:
        return VerifiedSession(user_id=user_id, payload=payload)

    async def account_standing(_user_id: UUID) -> AccountStanding | None:
        return standing

    async def delegation_revoked(actor_id: UUID) -> bool:
        return actor_id in revoked

    return PodMCPService(
        verify_session=verify,
        principal_lookups=PrincipalLookups(
            account_standing=account_standing,
            delegation_revoked=delegation_revoked,
        ),
    )


def _delegated(
    *,
    user_id: UUID,
    pod_id: UUID,
    actor_id: UUID,
    actor_type: str = "AGENT",
    actor_name: str | None = "researcher",
    scope: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        CLAIM_ACTOR_TYPE: actor_type,
        CLAIM_ACTOR_ID: str(actor_id),
        CLAIM_POD_ID: str(pod_id),
        CLAIM_SESSION_ID: "session",
        CLAIM_INVOKED_BY_USER_ID: str(user_id),
        CLAIM_DELEGATION_VERSION: DELEGATION_VERSION,
        "isImpersonation": True,
    }
    if actor_name is not None:
        payload[CLAIM_ACTOR_NAME] = actor_name
    if scope is not None:
        payload[CLAIM_SCOPE] = scope
    return payload


@pytest.fixture
def delegation_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "authz_delegated_tokens_enabled", True)
    monkeypatch.setattr(settings, "auth_email_verification_required", True)


@pytest.mark.asyncio
@pytest.mark.usefixtures("delegation_on")
async def test_pod_mcp_holds_a_function_token_to_the_function_not_the_person() -> None:
    user_id, pod_id, function_id = uuid4(), uuid4(), uuid4()
    service = _service(
        user_id=user_id,
        payload=_delegated(
            user_id=user_id,
            pod_id=pod_id,
            actor_id=function_id,
            actor_type="FUNCTION",
            actor_name=None,
            scope=["function.execute"],
        ),
    )

    ctx = await service.context_for(pod_id=pod_id, token="t")

    assert ctx is not None
    assert (ctx.workload_type, ctx.workload_id) == ("function", function_id)
    assert ctx.is_pod_default_agent is False
    assert ctx.delegation_scope == frozenset({"function.execute"})
    assert workload_principal_type(ctx) == "FUNCTION"


@pytest.mark.asyncio
@pytest.mark.usefixtures("delegation_on")
async def test_pod_mcp_holds_a_named_agent_token_to_its_own_grants() -> None:
    user_id, pod_id, agent_id = uuid4(), uuid4(), uuid4()
    service = _service(
        user_id=user_id,
        payload=_delegated(user_id=user_id, pod_id=pod_id, actor_id=agent_id),
    )

    ctx = await service.context_for(pod_id=pod_id, token="t")

    assert ctx is not None
    assert (ctx.workload_type, ctx.workload_id) == ("agent", agent_id)
    assert ctx.is_pod_default_agent is False
    assert workload_principal_type(ctx) == "AGENT"


@pytest.mark.asyncio
@pytest.mark.usefixtures("delegation_on")
async def test_pod_mcp_refuses_a_function_token_minted_for_another_pod() -> None:
    user_id, other_pod = uuid4(), uuid4()
    service = _service(
        user_id=user_id,
        payload=_delegated(
            user_id=user_id,
            pod_id=other_pod,
            actor_id=uuid4(),
            actor_type="FUNCTION",
            actor_name=None,
        ),
    )

    assert await service.authorize(pod_id=uuid4(), token="t") is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("delegation_on")
async def test_pod_mcp_refuses_malformed_delegation_claims() -> None:
    user_id = uuid4()
    # An actor id with nothing else: not "no claims", a broken delegated token.
    service = _service(user_id=user_id, payload={CLAIM_ACTOR_ID: str(uuid4())})

    assert await service.authorize(pod_id=uuid4(), token="t") is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("delegation_on")
async def test_pod_mcp_refuses_a_revoked_workload_s_token() -> None:
    user_id, pod_id, agent_id = uuid4(), uuid4(), uuid4()
    service = _service(
        user_id=user_id,
        payload=_delegated(user_id=user_id, pod_id=pod_id, actor_id=agent_id),
        revoked=frozenset({agent_id}),
    )

    assert await service.authorize(pod_id=pod_id, token="t") is False


@pytest.mark.asyncio
async def test_pod_mcp_refuses_delegated_tokens_when_delegation_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "authz_delegated_tokens_enabled", False)
    user_id, pod_id = uuid4(), uuid4()
    service = _service(
        user_id=user_id,
        payload=_delegated(user_id=user_id, pod_id=pod_id, actor_id=uuid4()),
    )

    assert await service.authorize(pod_id=pod_id, token="t") is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("delegation_on")
async def test_pod_mcp_refuses_a_session_with_an_unverified_email() -> None:
    service = _service(
        user_id=uuid4(),
        payload={},
        standing=AccountStanding(is_active=True, is_verified=False, is_deleted=False),
    )

    assert await service.authorize(pod_id=uuid4(), token="t") is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("delegation_on")
async def test_pod_mcp_refuses_a_deactivated_account() -> None:
    service = _service(
        user_id=uuid4(),
        payload={},
        standing=AccountStanding(is_active=False, is_verified=True, is_deleted=False),
    )

    assert await service.authorize(pod_id=uuid4(), token="t") is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("delegation_on")
async def test_pod_mcp_refuses_claims_delegated_by_someone_else() -> None:
    pod_id = uuid4()
    service = _service(
        user_id=uuid4(),
        payload=_delegated(user_id=uuid4(), pod_id=pod_id, actor_id=uuid4()),
    )

    assert await service.authorize(pod_id=pod_id, token="t") is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("delegation_on")
async def test_pod_mcp_serves_a_plain_session_as_the_pod_s_assistant() -> None:
    user_id, pod_id = uuid4(), uuid4()
    service = _service(user_id=user_id, payload={})

    ctx = await service.context_for(pod_id=pod_id, token="t")

    assert ctx is not None
    assert (ctx.user_id, ctx.workload_id, ctx.is_pod_default_agent) == (
        user_id,
        None,
        True,
    )
