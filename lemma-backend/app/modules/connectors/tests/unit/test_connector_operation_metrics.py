"""Every connector operation is counted by connector and outcome, and timed."""

from __future__ import annotations

import pytest

from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.connector_operation import ResolvedOperation
from app.modules.connectors.domain.kinds import KindPlugin
from app.modules.connectors.infrastructure.kinds.registry import KindRegistry
from app.modules.connectors.services.execution import KindDispatcher
from app.modules.test_support.metric_capture import total


class _Answers:
    async def execute(self, request):
        return {"ok": True}


class _Refuses:
    async def execute(self, request):
        raise RuntimeError("upstream said no")


def _dispatcher(executor) -> KindDispatcher:
    return KindDispatcher(
        KindRegistry(
            {ConnectorKind.HTTP: KindPlugin(kind=ConnectorKind.HTTP, executor=executor)}
        )
    )


def _request(dispatcher: KindDispatcher, connector_id: str):
    return dispatcher.build_request(
        connector_id=connector_id,
        kind=ConnectorKind.HTTP,
        operation=ResolvedOperation(name="list_things"),
        payload={},
        credentials={},
        config={},
    )


@pytest.mark.asyncio
async def test_a_successful_operation_is_counted_and_timed() -> None:
    dispatcher = _dispatcher(_Answers())
    ops = total("lemma.connector.operations", connector="metrics-ok", outcome="success")
    timed = total("lemma.connector.operation_duration_seconds", connector="metrics-ok")

    assert await dispatcher.execute(_request(dispatcher, "metrics-ok")) == {"ok": True}

    assert (
        total("lemma.connector.operations", connector="metrics-ok", outcome="success")
        == ops + 1
    )
    assert (
        total("lemma.connector.operation_duration_seconds", connector="metrics-ok")
        == timed + 1
    )


@pytest.mark.asyncio
async def test_a_failed_operation_is_counted_as_a_failure() -> None:
    dispatcher = _dispatcher(_Refuses())
    ops = total(
        "lemma.connector.operations", connector="metrics-bad", outcome="failure"
    )

    with pytest.raises(RuntimeError):
        await dispatcher.execute(_request(dispatcher, "metrics-bad"))

    assert (
        total("lemma.connector.operations", connector="metrics-bad", outcome="failure")
        == ops + 1
    )
    assert (
        total("lemma.connector.operations", connector="metrics-bad", outcome="success")
        == 0
    )
