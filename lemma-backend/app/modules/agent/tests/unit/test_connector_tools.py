"""Unit tests for the connector toolset.

The contract that matters to an agent is the number of steps between "I need to
send an email" and the email being sent, and whether a wrong guess ends the run.
So these cover: search works without knowing which install provides a
capability, a failure comes back as data the model can act on rather than an
exception, and bad arguments come back with the schema attached.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.agent.tools.connectors import pydantic_adapter as adapter
from app.modules.agent.tools.connectors.models import (
    RunConnectorOperationRequest,
    SearchConnectorOperationsRequest,
)
from app.modules.agent.tools.context import BaseAgentContext


_DEFAULT = object()


def _run_ctx(*, org_id=_DEFAULT) -> SimpleNamespace:
    # Sentinel, not None: org_id=None is the case under test, so it must be
    # distinguishable from "caller did not specify".
    return SimpleNamespace(
        deps=BaseAgentContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            org_id=uuid4() if org_id is _DEFAULT else org_id,
        )
    )


def _patch_services(monkeypatch, services) -> None:
    @asynccontextmanager
    async def fake_connector_services(deps):  # noqa: ANN001 - test stub
        del deps
        yield services

    monkeypatch.setattr(adapter, "connector_services", fake_connector_services)


def test_the_toolset_is_four_tools_not_one_per_operation():
    """Compiling a tool per operation would blow the budget the moment a tenant
    installs an MCP server; discovery stays dynamic instead."""
    assert set(adapter.connectors_toolset.tools) == {
        "list_connectors",
        "search_connector_operations",
        "describe_connector_operation",
        "run_connector_operation",
    }


async def test_search_without_an_auth_config_spans_every_install(monkeypatch):
    """The whole point of making auth_config optional: an agent that knows the
    task but not which install provides it must not have to guess first."""
    across = AsyncMock(return_value={"items": [], "returned_count": 0})
    monkeypatch.setattr(adapter, "search_across_auth_configs", across)
    per_install = AsyncMock()
    _patch_services(
        monkeypatch,
        SimpleNamespace(
            operations=SimpleNamespace(discover_operations_for_auth_config=per_install)
        ),
    )

    ctx = _run_ctx()
    await adapter.search_connector_operations(
        ctx, SearchConnectorOperationsRequest(query="send an email")
    )

    across.assert_awaited_once()
    assert across.await_args.kwargs["query"] == "send an email"
    per_install.assert_not_awaited()


async def test_search_with_an_auth_config_still_targets_just_that_install(monkeypatch):
    """Naming an install stays a narrowing, not a hint — an agent that already
    knows where to look should not pay for a fan-out."""
    across = AsyncMock()
    monkeypatch.setattr(adapter, "search_across_auth_configs", across)
    per_install = AsyncMock(return_value={"items": []})
    _patch_services(
        monkeypatch,
        SimpleNamespace(
            operations=SimpleNamespace(discover_operations_for_auth_config=per_install)
        ),
    )

    await adapter.search_connector_operations(
        _run_ctx(),
        SearchConnectorOperationsRequest(auth_config="workspace-gmail", query="send"),
    )

    per_install.assert_awaited_once()
    assert per_install.await_args.kwargs["auth_config_name"] == "workspace-gmail"
    across.assert_not_awaited()


async def test_a_connector_failure_is_data_not_a_dead_run(monkeypatch):
    """A disconnected account is information the model can report or route
    around. Raising would end the run over something recoverable."""
    _patch_services(
        monkeypatch,
        SimpleNamespace(
            operations=SimpleNamespace(
                get_operation_details_for_auth_config=AsyncMock(
                    side_effect=DomainError("Account needs reconnecting")
                )
            )
        ),
    )

    result = await adapter.run_connector_operation(
        _run_ctx(),
        RunConnectorOperationRequest(
            auth_config="workspace-gmail",
            operation="gmail_send_email",
            arguments={"recipient_email": "a@b.com"},
        ),
    )

    assert "error" in result
    assert "reconnecting" in result["message"]


async def test_bad_arguments_come_back_with_the_schema_attached(monkeypatch):
    """The model can fix itself on the next call only if it is told the shape it
    missed — so the input schema rides along with the violations."""
    schema = {
        "type": "object",
        "properties": {"recipient_email": {"type": "string"}},
        "required": ["recipient_email"],
    }
    execute = AsyncMock()
    _patch_services(
        monkeypatch,
        SimpleNamespace(
            ctx=object(),
            operations=SimpleNamespace(
                get_operation_details_for_auth_config=AsyncMock(
                    return_value=SimpleNamespace(input_schema=schema)
                ),
                resolve_execution_for_auth_config=AsyncMock(),
                execute_resolved=execute,
            ),
        ),
    )

    result = await adapter.run_connector_operation(
        _run_ctx(),
        RunConnectorOperationRequest(
            auth_config="workspace-gmail",
            operation="gmail_send_email",
            arguments={"subject": "hi"},
        ),
    )

    assert result["error"] == "invalid_arguments"
    assert result["input_schema"] == schema
    assert any("recipient_email" in v["message"] for v in result["violations"])
    execute.assert_not_awaited()


async def test_a_conversation_without_an_org_says_so_once(monkeypatch):
    """Connectors are org-scoped; an unbound conversation should get one clear
    answer rather than a connection attempt that fails obscurely."""
    result = await adapter.list_connectors(_run_ctx(org_id=None))
    assert result["error"] == "no_organization"


@pytest.mark.parametrize("limit", [0, 101])
def test_search_limits_stay_bounded(limit):
    """An unbounded limit turns one tool call into an unbounded response."""
    with pytest.raises(ValueError):
        SearchConnectorOperationsRequest(limit=limit)
