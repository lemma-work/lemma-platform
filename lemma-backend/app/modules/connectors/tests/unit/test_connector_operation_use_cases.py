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
from uuid import uuid4

import pytest

from app.modules.connectors.application import connector_operation_use_cases as ucmod
from app.modules.connectors.application.connector_operation_use_cases import (
    ConnectorOperationUseCases,
)
from app.modules.connectors.domain.errors import OperationExecutionUnauthorizedError
from app.modules.connectors.services.connector_operation_service import (
    ResolvedConnectorExecution,
)

pytestmark = pytest.mark.asyncio


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
    resolved_sentinel = object()
    response_sentinel = object()

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

    # A fresh phase-2 scope opens for the flagging write after the failed call.
    assert events.count("p2_open") == 2
    connector_service.mark_account_reauth_required.assert_awaited_once_with(
        account_id, user_id, org_id
    )
