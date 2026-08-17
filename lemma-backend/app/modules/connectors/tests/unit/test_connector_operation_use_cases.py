"""Unit tests for ConnectorOperationUseCases — the saga that releases the pooled
DB connection across the external connector/Composio operation call.

The key guarantee: the DB/auth resolve phase runs (and its short scope closes,
releasing the connection + committing any OAuth-token refresh) BEFORE the
external execute phase runs.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from unittest.mock import AsyncMock
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.connectors.application import connector_operation_use_cases as ucmod
from app.modules.connectors.application.connector_operation_use_cases import (
    ConnectorOperationUseCases,
)
from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionTimeoutError,
    OperationExecutionUnauthorizedError,
)
from app.modules.connectors.services.connector_operation_service import (
    ResolvedConnectorExecution,
)

pytestmark = pytest.mark.asyncio


async def _record(sink: list[str], scope: str) -> None:
    """Stand in for the Redis-backed `record_failure`, capturing the scope."""
    sink.append(scope)


@dataclass
class _FakeUowCtx:
    uow: object
    ctx: object


@pytest.fixture
def events():
    return []


@pytest.fixture(autouse=True)
def _fake_scopes(monkeypatch, events):
    @contextlib.asynccontextmanager
    async def fake_current_context_scope(uow_factory, *, request, user_id):
        events.append("p1_open")
        try:
            yield _FakeUowCtx(uow="uow1", ctx="ctx-actor")
        finally:
            events.append("p1_close")

    @contextlib.asynccontextmanager
    async def fake_uow_scope(uow_factory):
        events.append("p2_open")
        try:
            yield "uow2"
        finally:
            events.append("p2_close")

    monkeypatch.setattr(ucmod, "current_context_scope", fake_current_context_scope)
    monkeypatch.setattr(ucmod, "uow_scope", fake_uow_scope)


async def test_resolve_runs_in_phase1_and_execute_runs_after_release(events):
    # A stand-in for the real ResolvedConnectorExecution: the saga reads
    # connector_id off it when deciding what to do with a file result.
    resolved_sentinel = SimpleNamespace(connector_id="outlook", organization_id=uuid4())
    response_sentinel = SimpleNamespace(result={"ok": True})

    class _FakeService:
        def __init__(self, uow):
            self.uow = uow

        async def resolve_execution_for_auth_config(self, **kwargs):
            events.append(("resolve", self.uow, kwargs["actor"]))
            return resolved_sentinel

        async def execute_resolved(self, resolved):
            events.append(("execute", self.uow, resolved))
            return response_sentinel

    uc = ConnectorOperationUseCases(uow_factory=object(), service_builder=_FakeService)

    result = await uc.execute_operation_for_auth_config(
        organization_id=uuid4(),
        auth_config_name="outlook",
        operation_name="OUTLOOK_CREATE_DRAFT_REPLY",
        payload={"x": 1},
        user_id=uuid4(),
        request=object(),
        auth_token="tok",
        api_url="https://api",
        account_id=None,
    )

    assert result is response_sentinel

    names = [e if isinstance(e, str) else e[0] for e in events]
    # Phase 1 fully closes (connection released) BEFORE the external execute runs.
    assert names == [
        "p1_open",
        "resolve",
        "p1_close",
        "p2_open",
        "execute",
        "p2_close",
    ]

    resolve_evt = next(e for e in events if isinstance(e, tuple) and e[0] == "resolve")
    execute_evt = next(e for e in events if isinstance(e, tuple) and e[0] == "execute")
    # resolve authorizes with the in-scope ctx as actor; execute consumes the plan.
    assert resolve_evt[1] == "uow1" and resolve_evt[2] == "ctx-actor"
    assert execute_evt[1] == "uow2" and execute_evt[2] is resolved_sentinel


async def test_unauthorized_execution_flags_account_for_reauth(events):
    account_id = uuid4()
    user_id = uuid4()
    org_id = uuid4()
    resolved = ResolvedConnectorExecution(
        connector_id="airtable",
        operation_execution_name="AIRTABLE_LIST_BASES",
        provider="COMPOSIO",
        third_party_credentials={"connection_id": "ca_x"},
        payload={},
        auth_token=None,
        api_url=None,
        account_id=account_id,
        account_user_id=user_id,
        organization_id=org_id,
    )
    connector_service = AsyncMock()

    class _FakeService:
        def __init__(self, uow):
            self.uow = uow
            self.connector_service = connector_service

        async def resolve_execution_for_auth_config(self, **kwargs):
            return resolved

        async def execute_resolved(self, resolved):
            raise OperationExecutionUnauthorizedError("unauthorized")

    uc = ConnectorOperationUseCases(uow_factory=object(), service_builder=_FakeService)

    with pytest.raises(OperationExecutionUnauthorizedError):
        await uc.execute_operation_for_auth_config(
            organization_id=org_id,
            auth_config_name="airtable",
            operation_name="AIRTABLE_LIST_BASES",
            payload={},
            user_id=user_id,
            request=object(),
            account_id=account_id,
        )

    # Three phase-2 scopes: the original call, the one retry after refreshing
    # the credential, and the flagging write once that retry is rejected too.
    assert events.count("p2_open") == 3
    # The refresh was attempted before giving up -- that is what makes it safe
    # to stop refreshing before every call.
    connector_service.get_account_credentials.assert_awaited()
    connector_service.mark_account_reauth_required.assert_awaited_once_with(
        account_id, user_id, org_id
    )


@pytest.mark.parametrize(
    "failure",
    [
        OperationExecutionInfrastructureError("provider is down"),
        OperationExecutionTimeoutError("provider timed out"),
    ],
    ids=["infrastructure", "timeout"],
)
async def test_a_provider_failure_on_the_credential_retry_still_trips_the_breaker(
    monkeypatch, failure
):
    """The retry is inside the breaker's judgement, not beside it.

    The first call raising a 401 hands control to the credential-refresh path.
    If the *retry* then fails because the provider is unwell, that is the same
    provider fault the breaker exists for, and it has to count.

    It did not, once: the refresh ran inside an `except` handler, so an
    infrastructure error raised there propagated past the clause that records
    failures. The shape that made this matter is the systemic one -- a provider
    whose token endpoint is down rejects every account's credential at once, and
    every one of them then retries into the same outage. So the failure most
    worth breaking on was the single failure that could not trip the breaker.
    """
    recorded: list[str] = []
    monkeypatch.setattr(
        ucmod,
        "breaker_record_failure",
        lambda scope: _record(recorded, scope),
    )

    resolved = ResolvedConnectorExecution(
        connector_id="airtable",
        operation_execution_name="AIRTABLE_LIST_BASES",
        provider="COMPOSIO",
        third_party_credentials={"connection_id": "ca_x"},
        payload={},
        auth_token=None,
        api_url=None,
        account_id=uuid4(),
        account_user_id=uuid4(),
        organization_id=uuid4(),
    )
    connector_service = AsyncMock()
    attempts: list[int] = []

    class _FakeService:
        def __init__(self, uow):
            self.connector_service = connector_service

        async def resolve_execution_for_auth_config(self, **kwargs):
            return resolved

        async def execute_resolved(self, _resolved):
            attempts.append(1)
            # First the credential is rejected; then the retry hits the outage.
            if len(attempts) == 1:
                raise OperationExecutionUnauthorizedError("unauthorized")
            raise failure

    uc = ConnectorOperationUseCases(uow_factory=object(), service_builder=_FakeService)

    with pytest.raises(type(failure)):
        await uc.execute_operation_for_auth_config(
            organization_id=resolved.organization_id,
            auth_config_name="airtable",
            operation_name="AIRTABLE_LIST_BASES",
            payload={},
            user_id=resolved.account_user_id,
            request=object(),
            account_id=resolved.account_id,
        )

    assert len(attempts) == 2, "the credential refresh retry ran"
    # Org-prefixed: the breaker is scoped per organization, so one tenant's
    # provider outage cannot refuse another tenant's calls.
    assert recorded == [f"{resolved.organization_id}:airtable:AIRTABLE_LIST_BASES"]
    # The provider fault surfaces as itself. Swapping it for the 401 would tell
    # the user to reconnect an account that is fine.
    connector_service.mark_account_reauth_required.assert_not_awaited()


async def test_a_rejected_credential_alone_never_trips_the_breaker(monkeypatch):
    """The other half of the same boundary.

    A 401 that survives a refresh is one account's problem. Counting it would
    let a single tenant's revoked credential disable the operation for everyone
    else on the same connector -- the exact failure the breaker is supposed to
    prevent, caused by the breaker.
    """
    recorded: list[str] = []
    monkeypatch.setattr(
        ucmod,
        "breaker_record_failure",
        lambda scope: _record(recorded, scope),
    )

    resolved = ResolvedConnectorExecution(
        connector_id="airtable",
        operation_execution_name="AIRTABLE_LIST_BASES",
        provider="COMPOSIO",
        third_party_credentials={"connection_id": "ca_x"},
        payload={},
        auth_token=None,
        api_url=None,
        account_id=uuid4(),
        account_user_id=uuid4(),
        organization_id=uuid4(),
    )

    class _FakeService:
        def __init__(self, uow):
            self.connector_service = AsyncMock()

        async def resolve_execution_for_auth_config(self, **kwargs):
            return resolved

        async def execute_resolved(self, _resolved):
            raise OperationExecutionUnauthorizedError("unauthorized")

    uc = ConnectorOperationUseCases(uow_factory=object(), service_builder=_FakeService)

    with pytest.raises(OperationExecutionUnauthorizedError):
        await uc.execute_operation_for_auth_config(
            organization_id=resolved.organization_id,
            auth_config_name="airtable",
            operation_name="AIRTABLE_LIST_BASES",
            payload={},
            user_id=resolved.account_user_id,
            request=object(),
            account_id=resolved.account_id,
        )

    assert recorded == []
