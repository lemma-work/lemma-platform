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

import httpx
import pytest

from app.modules.connectors.domain.connector import AuthProvider, ConnectorKind

from app.modules.connectors.application import connector_operation_use_cases as ucmod
from app.modules.connectors.application.connector_operation_use_cases import (
    ConnectorOperationUseCases,
)
from app.modules.connectors.domain.errors import (
    ConnectorReauthRequiredError,
    OAuthWorkflowError,
    OperationExecutionAccessDeniedError,
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
    async def fake_pod_context_scope(uow_factory, *, request, user_id, pod_id):
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

    monkeypatch.setattr(ucmod, "pod_context_scope", fake_pod_context_scope)
    monkeypatch.setattr(ucmod, "uow_scope", fake_uow_scope)


async def test_resolve_runs_in_phase1_and_execute_runs_after_release(events):
    # A stand-in for the real ResolvedConnectorExecution: the saga reads
    # connector_id off it when deciding what to do with a file result.
    resolved_sentinel = SimpleNamespace(
        connector_id="outlook",
        organization_id=uuid4(),
        input_schema=None,
        payload={},
        requested_output_path=None,
        account_reauth_pending=False,
    )
    response_sentinel = SimpleNamespace(result={"ok": True})

    class _FakeService:
        def __init__(self, uow):
            self.uow = uow

        async def resolve_pod_connector_execution(self, ctx, **kwargs):
            events.append(("resolve", self.uow, ctx))
            return resolved_sentinel

        async def execute_resolved(self, resolved):
            events.append(("execute", self.uow, resolved))
            return response_sentinel

    uc = ConnectorOperationUseCases(uow_factory=object(), service_builder=_FakeService)

    result = await uc.execute_pod_connector_operation(
        pod_id=uuid4(),
        slug="outlook",
        operation_name="OUTLOOK_CREATE_DRAFT_REPLY",
        payload={"x": 1},
        user_id=uuid4(),
        request=object(),
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


def _credentials(**values: str) -> SimpleNamespace:
    """What `get_account_credentials` returns: a model the saga dumps."""
    return SimpleNamespace(model_dump=lambda **_: dict(values))


async def test_a_credential_nothing_can_renew_rejected_twice_is_flagged(events):
    account_id = uuid4()
    user_id = uuid4()
    org_id = uuid4()
    resolved = ResolvedConnectorExecution(
        connector_id="airtable",
        operation_execution_name="AIRTABLE_LIST_BASES",
        kind=ConnectorKind.HTTP,
        provider=AuthProvider.LEMMA.value,
        third_party_credentials={"api_key": "k"},
        payload={},
        account_id=account_id,
        account_user_id=user_id,
        organization_id=org_id,
    )
    connector_service = AsyncMock()
    # A plain secret: the forced refresh has nothing to renew it with and hands
    # back what was stored.
    connector_service.get_account_credentials.return_value = _credentials(api_key="k")

    class _FakeService:
        def __init__(self, uow):
            self.uow = uow
            self.connector_service = connector_service

        async def resolve_pod_connector_execution(self, ctx, **kwargs):
            return resolved

        async def execute_resolved(self, resolved):
            raise OperationExecutionUnauthorizedError("unauthorized")

    uc = ConnectorOperationUseCases(uow_factory=object(), service_builder=_FakeService)

    with pytest.raises(OperationExecutionUnauthorizedError):
        await uc.execute_pod_connector_operation(
            pod_id=uuid4(),
            slug="airtable",
            operation_name="AIRTABLE_LIST_BASES",
            payload={},
            user_id=user_id,
            request=object(),
        )

    # Four bare scopes: the original call, the credential refresh (done as the
    # account's owner, so it needs no caller context), the one retry, and the
    # flagging write once that retry is rejected too.
    assert events.count("p2_open") == 4
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
        kind=ConnectorKind.COMPOSIO,
        provider=AuthProvider.COMPOSIO.value,
        third_party_credentials={"connection_id": "ca_x"},
        payload={},
        account_id=uuid4(),
        account_user_id=uuid4(),
        organization_id=uuid4(),
    )
    connector_service = AsyncMock()
    attempts: list[int] = []

    class _FakeService:
        def __init__(self, uow):
            self.connector_service = connector_service

        async def resolve_pod_connector_execution(self, ctx, **kwargs):
            return resolved

        async def execute_resolved(self, _resolved):
            attempts.append(1)
            # First the credential is rejected; then the retry hits the outage.
            if len(attempts) == 1:
                raise OperationExecutionUnauthorizedError("unauthorized")
            raise failure

    uc = ConnectorOperationUseCases(uow_factory=object(), service_builder=_FakeService)

    with pytest.raises(type(failure)):
        await uc.execute_pod_connector_operation(
            pod_id=uuid4(),
            slug="airtable",
            operation_name="AIRTABLE_LIST_BASES",
            payload={},
            user_id=resolved.account_user_id,
            request=object(),
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
        kind=ConnectorKind.COMPOSIO,
        provider=AuthProvider.COMPOSIO.value,
        third_party_credentials={"connection_id": "ca_x"},
        payload={},
        account_id=uuid4(),
        account_user_id=uuid4(),
        organization_id=uuid4(),
    )

    class _FakeService:
        def __init__(self, uow):
            self.connector_service = AsyncMock()
            self.connector_service.get_account_credentials.return_value = _credentials(
                connection_id="ca_x"
            )

        async def resolve_pod_connector_execution(self, ctx, **kwargs):
            return resolved

        async def execute_resolved(self, _resolved):
            raise OperationExecutionUnauthorizedError("unauthorized")

    uc = ConnectorOperationUseCases(uow_factory=object(), service_builder=_FakeService)

    with pytest.raises(OperationExecutionUnauthorizedError):
        await uc.execute_pod_connector_operation(
            pod_id=uuid4(),
            slug="airtable",
            operation_name="AIRTABLE_LIST_BASES",
            payload={},
            user_id=resolved.account_user_id,
            request=object(),
        )

    assert recorded == []


# -- which failures may mark an account ---------------------------------------
#
# The mark refuses every later call through the account, so only a definitive
# rejection of the credential may set it. A production account was disabled
# whole by a 403 on one operation, and another by a token endpoint that failed
# once; neither ever recovered.


def _bigquery(*, reauth_pending: bool = False) -> ResolvedConnectorExecution:
    return ResolvedConnectorExecution(
        connector_id="googlebigquery",
        operation_execution_name="GOOGLEBIGQUERY_QUERY",
        kind=ConnectorKind.COMPOSIO,
        provider=AuthProvider.COMPOSIO.value,
        third_party_credentials={"connection_id": "ca_x"},
        payload={},
        account_id=uuid4(),
        account_user_id=uuid4(),
        organization_id=uuid4(),
        account_reauth_pending=reauth_pending,
    )


async def _run(resolved, *, fails_with, connector_service, attempts=None):
    attempts = attempts if attempts is not None else []

    class _FakeService:
        def __init__(self, uow):
            self.connector_service = connector_service

        async def resolve_pod_connector_execution(self, ctx, **kwargs):
            return resolved

        async def execute_resolved(self, _resolved):
            attempts.append(_resolved)
            failure = fails_with(len(attempts))
            if failure is not None:
                raise failure
            return SimpleNamespace(result={"ok": True})

    uc = ConnectorOperationUseCases(uow_factory=object(), service_builder=_FakeService)
    return await uc.execute_pod_connector_operation(
        pod_id=uuid4(),
        slug="bigquery",
        operation_name="GOOGLEBIGQUERY_QUERY",
        payload={},
        user_id=resolved.account_user_id,
        request=object(),
    )


async def test_a_403_is_the_operations_failure_and_never_marks_the_account():
    resolved = _bigquery()
    connector_service = AsyncMock()
    denied = OperationExecutionAccessDeniedError(
        "", details={"upstream_status": 403, "upstream_message": "no dataset access"}
    )

    with pytest.raises(OperationExecutionAccessDeniedError) as raised:
        await _run(
            resolved, fails_with=lambda _: denied, connector_service=connector_service
        )

    # The provider's own permission answer, not a "reconnect".
    assert raised.value.code == "OPERATION_EXECUTION_ACCESS_DENIED"
    assert raised.value.status_code == 403
    connector_service.mark_account_reauth_required.assert_not_awaited()
    # A permission refusal is no reason to try a refresh either.
    connector_service.get_account_credentials.assert_not_awaited()


async def test_a_withdrawn_grant_on_the_refresh_marks_the_account():
    resolved = _bigquery()
    connector_service = AsyncMock()
    connector_service.get_account_credentials.side_effect = (
        ConnectorReauthRequiredError(reason="composio_expired")
    )

    with pytest.raises(OperationExecutionUnauthorizedError):
        await _run(
            resolved,
            fails_with=lambda _: OperationExecutionUnauthorizedError("unauthorized"),
            connector_service=connector_service,
        )

    connector_service.mark_account_reauth_required.assert_awaited_once_with(
        resolved.account_id, resolved.account_user_id, resolved.organization_id
    )


@pytest.mark.parametrize(
    "refresh_failure",
    [
        OAuthWorkflowError("token endpoint answered 503"),
        httpx.ConnectTimeout("token endpoint timed out"),
        TimeoutError(),
    ],
    ids=["provider-5xx", "network", "timeout"],
)
async def test_a_refresh_that_merely_failed_never_marks_the_account(refresh_failure):
    resolved = _bigquery()
    connector_service = AsyncMock()
    connector_service.get_account_credentials.side_effect = refresh_failure

    with pytest.raises(OperationExecutionUnauthorizedError):
        await _run(
            resolved,
            fails_with=lambda _: OperationExecutionUnauthorizedError("unauthorized"),
            connector_service=connector_service,
        )

    connector_service.mark_account_reauth_required.assert_not_awaited()


async def test_a_401_after_the_issuer_renewed_the_credential_does_not_mark_it():
    """A refresh the issuer granted proves the grant is alive.

    A 401 that survives it is about this call -- an endpoint that answers 401
    where it means 403, a resource the token's audience does not cover -- and
    marking the account would refuse every operation that does work.
    """
    resolved = _bigquery()
    connector_service = AsyncMock()
    connector_service.get_account_credentials.return_value = _credentials(
        access_token="fresh", connection_id="ca_x"
    )
    attempts: list[ResolvedConnectorExecution] = []

    with pytest.raises(OperationExecutionUnauthorizedError):
        await _run(
            resolved,
            fails_with=lambda _: OperationExecutionUnauthorizedError("unauthorized"),
            connector_service=connector_service,
            attempts=attempts,
        )

    assert len(attempts) == 2
    assert attempts[1].third_party_credentials["access_token"] == "fresh"
    connector_service.mark_account_reauth_required.assert_not_awaited()


# -- a marked account the provider accepts is unmarked -------------------------


def _marked_account_service() -> tuple[AsyncMock, SimpleNamespace]:
    from app.modules.connectors.domain.account import AccountStatus

    account = SimpleNamespace(status=AccountStatus.REAUTH_REQUIRED)
    connector_service = AsyncMock()
    connector_service.account_repository.get.return_value = account
    return connector_service, account


async def test_a_successful_call_clears_a_mark_resolution_let_through():
    from app.modules.connectors.domain.account import AccountStatus

    resolved = _bigquery(reauth_pending=True)
    connector_service, account = _marked_account_service()

    result = await _run(
        resolved, fails_with=lambda _: None, connector_service=connector_service
    )

    assert result.result == {"ok": True}
    connector_service.account_repository.get.assert_awaited_once_with(
        resolved.account_id
    )
    assert account.status is AccountStatus.CONNECTED
    connector_service.account_repository.update.assert_awaited_once_with(account)
    connector_service.uow.commit.assert_awaited()


async def test_a_403_clears_a_mark_too_because_the_provider_knew_the_caller():
    from app.modules.connectors.domain.account import AccountStatus

    resolved = _bigquery(reauth_pending=True)
    connector_service, account = _marked_account_service()

    with pytest.raises(OperationExecutionAccessDeniedError):
        await _run(
            resolved,
            fails_with=lambda _: OperationExecutionAccessDeniedError("denied"),
            connector_service=connector_service,
        )

    assert account.status is AccountStatus.CONNECTED
    connector_service.mark_account_reauth_required.assert_not_awaited()


async def test_a_healthy_account_pays_no_extra_read_on_success():
    resolved = _bigquery(reauth_pending=False)
    connector_service = AsyncMock()

    await _run(resolved, fails_with=lambda _: None, connector_service=connector_service)

    connector_service.account_repository.get.assert_not_awaited()
