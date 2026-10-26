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
from app.modules.agent.infrastructure.tools.connectors import (
    pydantic_adapter as adapter,
)
from app.modules.agent.infrastructure.tools.connectors.models import (
    RunConnectorOperationRequest,
    SearchConnectorOperationsRequest,
)
from app.modules.agent.domain.context import ToolContext


_DEFAULT = object()


def _run_ctx(*, org_id=_DEFAULT) -> SimpleNamespace:
    # Sentinel, not None: org_id=None is the case under test, so it must be
    # distinguishable from "caller did not specify".
    return SimpleNamespace(
        deps=ToolContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            org_id=uuid4() if org_id is _DEFAULT else org_id,
        )
    )


def _no_reach() -> SimpleNamespace:
    """The person has no installation of their own the pod has not declared."""
    return SimpleNamespace(
        offered=AsyncMock(return_value=[]),
        installation=AsyncMock(return_value=None),
        slug_for=AsyncMock(side_effect=lambda ctx, pod_id, name: name),
    )


def _patch_services(monkeypatch, services) -> None:
    if not hasattr(services, "reach"):
        services.reach = _no_reach()

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


def _connector(slug: str) -> SimpleNamespace:
    return SimpleNamespace(
        slug=slug,
        connector_id="gmail",
        mode=SimpleNamespace(value="PER_MEMBER"),
        purpose=None,
        status=SimpleNamespace(value="READY"),
    )


async def test_search_without_a_connector_spans_every_one_the_agent_may_use(
    monkeypatch,
):
    """The whole point of making `connector` optional: an agent that knows the
    task but not which connector provides it must not have to guess first."""
    discover = AsyncMock(
        side_effect=lambda ctx, **kw: SimpleNamespace(
            items=[{"name": f"{kw['slug']}_send", "relevance_score": 1.0}]
        )
    )
    _patch_services(
        monkeypatch,
        SimpleNamespace(
            ctx=object(),
            pod_connectors=SimpleNamespace(
                usable_connectors=AsyncMock(
                    return_value=[_connector("gmail"), _connector("outlook")]
                )
            ),
            operations=SimpleNamespace(pod_connector_operations=discover),
        ),
    )

    result = await adapter.search_connector_operations(
        _run_ctx(), SearchConnectorOperationsRequest(query="send an email")
    )

    assert {call.kwargs["slug"] for call in discover.await_args_list} == {
        "gmail",
        "outlook",
    }
    assert {hit["connector"] for hit in result["items"]} == {"gmail", "outlook"}


async def test_search_with_a_connector_still_targets_just_that_one(monkeypatch):
    """Naming a connector stays a narrowing, not a hint -- an agent that already
    knows where to look should not pay for a fan-out."""
    usable = AsyncMock()
    discover = AsyncMock(return_value=SimpleNamespace(items=[]))
    _patch_services(
        monkeypatch,
        SimpleNamespace(
            ctx=object(),
            pod_connectors=SimpleNamespace(usable_connectors=usable),
            operations=SimpleNamespace(pod_connector_operations=discover),
        ),
    )

    await adapter.search_connector_operations(
        _run_ctx(),
        SearchConnectorOperationsRequest(connector="support-inbox", query="send"),
    )

    discover.assert_awaited_once()
    assert discover.await_args.kwargs["slug"] == "support-inbox"
    usable.assert_not_awaited()


async def test_a_connector_failure_is_data_not_a_dead_run(monkeypatch):
    """A disconnected account is information the model can report or route
    around. Raising would end the run over something recoverable."""
    _patch_services(
        monkeypatch,
        SimpleNamespace(
            ctx=object(),
            operations=SimpleNamespace(
                pod_connector_operation=AsyncMock(
                    side_effect=DomainError("Account needs reconnecting")
                )
            ),
        ),
    )

    result = await adapter.run_connector_operation(
        _run_ctx(),
        RunConnectorOperationRequest(
            connector="gmail",
            operation="gmail_send_email",
            payload={"recipient_email": "anukul@lemma.work"},
        ),
    )

    assert result["success"] is False
    assert "reconnecting" in result["error"]


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
                pod_connector_operation=AsyncMock(
                    return_value=SimpleNamespace(input_schema=schema)
                ),
                resolve_pod_connector_execution=AsyncMock(),
                execute_resolved=execute,
            ),
        ),
    )

    result = await adapter.run_connector_operation(
        _run_ctx(),
        RunConnectorOperationRequest(
            connector="gmail",
            operation="gmail_send_email",
            payload={"subject": "hi"},
        ),
    )

    assert result["code"] == "invalid_arguments"
    assert result["input_schema"] == schema
    assert any("recipient_email" in v["message"] for v in result["violations"])
    execute.assert_not_awaited()


async def test_a_conversation_without_a_pod_says_so_once(monkeypatch):
    """Connectors are the pod's; an unbound conversation should get one clear
    answer rather than a connection attempt that fails obscurely."""
    ctx = _run_ctx()
    ctx.deps.pod_id = None
    result = await adapter.list_connectors(ctx)
    assert result["code"] == "no_pod"
    assert result["success"] is False


async def test_the_list_names_only_the_connectors_the_agent_may_use(monkeypatch):
    """Built from the agent's grants, so it is never told about a connector it
    would be refused."""
    _patch_services(
        monkeypatch,
        SimpleNamespace(
            ctx=object(),
            pod_connectors=SimpleNamespace(
                usable_connectors=AsyncMock(return_value=[_connector("gmail")])
            ),
        ),
    )

    result = await adapter.list_connectors(_run_ctx())

    assert result["count"] == 1
    assert result["items"][0]["connector"] == "gmail"
    assert result["items"][0]["mode"] == "PER_MEMBER"


def test_the_run_request_has_no_account_argument():
    """Whose account a call runs as is the connector's decision. An
    `account_id` the model could fill in let it act as a colleague (H3)."""
    assert "account_id" not in RunConnectorOperationRequest.model_fields


@pytest.mark.parametrize("limit", [0, 101])
def test_search_limits_stay_bounded(limit):
    """An unbounded limit turns one tool call into an unbounded response."""
    with pytest.raises(ValueError):
        SearchConnectorOperationsRequest(limit=limit)


async def test_the_provider_call_happens_after_the_database_scope_closes(monkeypatch):
    """No pooled connection is held across the call out to the provider.

    The REST path has split resolve from execute since it was written, and says
    why in a long comment. The agent path -- the hottest connector path there
    is -- ran both inside one `connector_services` scope, so a session
    autobegun by the first SELECT stayed checked out for the whole provider
    call: up to sixty seconds for MCP. Ten agents against an unresponsive
    server exhausted a pool of ten, wedging requests that had nothing to do
    with connectors.

    Asserted as an order of events rather than by counting connections,
    because that is the property: the scope must be *closed* before the call
    starts. A test that only checked "execute was called" passed throughout.
    """
    events: list[str] = []

    @asynccontextmanager
    async def recording_services(deps):  # noqa: ANN001 - test stub
        del deps
        events.append("resolve-scope-open")
        try:
            yield SimpleNamespace(
                ctx=None,
                reach=_no_reach(),
                operations=SimpleNamespace(
                    pod_connector_operation=AsyncMock(
                        return_value=SimpleNamespace(input_schema=None)
                    ),
                    resolve_pod_connector_execution=AsyncMock(
                        return_value=SimpleNamespace(
                            connector_id="gmail", input_schema=None, payload={}
                        )
                    ),
                ),
                files=SimpleNamespace(
                    prepare=AsyncMock(side_effect=lambda resolved: resolved)
                ),
            )
        finally:
            events.append("resolve-scope-closed")

    @asynccontextmanager
    async def recording_execution():
        events.append("execute-scope-open")
        try:

            async def _execute(resolved):  # noqa: ANN001 - test stub
                del resolved
                events.append("provider-call")
                return SimpleNamespace(result={"ok": True})

            yield SimpleNamespace(execute_resolved=_execute)
        finally:
            events.append("execute-scope-closed")

    monkeypatch.setattr(adapter, "connector_services", recording_services)
    monkeypatch.setattr(adapter, "connector_execution_only", recording_execution)

    await adapter.run_connector_operation(
        _run_ctx(),
        RunConnectorOperationRequest(
            connector="some-connector", operation="do_thing", payload={}
        ),
    )

    assert events.index("resolve-scope-closed") < events.index("provider-call"), (
        f"the provider was called with the resolve scope still open: {events}"
    )


async def test_an_attachment_is_read_as_the_agent_and_output_path_stays_lemmas(
    monkeypatch,
):
    """Sending a pod file through a connector, end to end through the tool.

    Two things were wrong here. A pod path in the arguments reached the
    provider as that literal dict -- nothing read the file -- and
    `output_path` was copied into the provider's arguments, where Composio and
    MCP received an argument they never declared.
    """
    from app.modules.connectors.domain.execution_plan import (
        ResolvedConnectorExecution,
    )
    from app.modules.connectors.domain.file_input import MaterializedFile
    from app.modules.connectors.services.files.operation_files import OperationFiles

    upload_schema = {
        "type": "object",
        "properties": {
            "file_to_upload": {
                "type": "object",
                "file_uploadable": True,
                "properties": {"name": {}, "mimetype": {}, "s3key": {}},
            }
        },
    }
    reads: list[tuple] = []

    class Pod:
        async def read_bytes(self, *, pod_id, path, ctx):  # noqa: ANN001
            reads.append((pod_id, path, ctx))
            return b"%PDF", "application/pdf", "q3.pdf"

    captured_kwargs: dict = {}

    async def resolve(ctx, **kwargs):  # noqa: ANN001, ANN003 - test stub
        captured_kwargs.update(kwargs)
        return ResolvedConnectorExecution(
            connector_id="googledrive",
            operation_execution_name="GOOGLEDRIVE_UPLOAD_FILE",
            provider="composio",
            third_party_credentials={},
            payload=kwargs["payload"],
            input_schema=upload_schema,
        )

    agent_ctx = object()
    run_ctx = _run_ctx()
    _patch_services(
        monkeypatch,
        SimpleNamespace(
            ctx=agent_ctx,
            files=OperationFiles(Pod(), pod_id=run_ctx.deps.pod_id, ctx=agent_ctx),
            operations=SimpleNamespace(
                pod_connector_operation=AsyncMock(
                    return_value=SimpleNamespace(input_schema=None)
                ),
                resolve_pod_connector_execution=resolve,
            ),
        ),
    )
    sent: list = []

    @asynccontextmanager
    async def execution():
        async def _execute(resolved):  # noqa: ANN001 - test stub
            sent.append(resolved.payload)
            return SimpleNamespace(result={"id": "drive-file"})

        yield SimpleNamespace(execute_resolved=_execute)

    monkeypatch.setattr(adapter, "connector_execution_only", execution)

    await adapter.run_connector_operation(
        run_ctx,
        RunConnectorOperationRequest(
            connector="drive",
            operation="GOOGLEDRIVE_UPLOAD_FILE",
            payload={"file_to_upload": {"pod_path": "/me/q3.pdf"}},
            output_path="/me/out.pdf",
        ),
    )

    assert "output_path" not in captured_kwargs["payload"]
    assert sent == [
        {"file_to_upload": MaterializedFile(b"%PDF", "q3.pdf", "application/pdf")}
    ]
    # Read in the agent's own pod, under the agent's delegated context.
    assert reads == [(run_ctx.deps.pod_id, "/me/q3.pdf", agent_ctx)]


def _offer(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name, connector_id="gmail", kind="COMPOSIO", auth_config_id=uuid4()
    )


async def test_the_list_offers_the_persons_own_installations_too(monkeypatch):
    """Review of #1039: on main the tools reached every installation the
    person had an account on; an ordinary pod declares none, so its assistant
    could not reach their Gmail at all. Those come back, as their own account,
    declared on first use."""
    reach = _no_reach()
    reach.offered = AsyncMock(return_value=[_offer("gmail")])
    _patch_services(
        monkeypatch,
        SimpleNamespace(
            ctx=object(),
            reach=reach,
            pod_connectors=SimpleNamespace(
                usable_connectors=AsyncMock(return_value=[_connector("support")])
            ),
        ),
    )

    result = await adapter.list_connectors(_run_ctx())

    assert [item["connector"] for item in result["items"]] == ["support", "gmail"]
    assert result["items"][1]["mode"] == "PER_MEMBER"


async def test_searching_an_own_installation_declares_nothing(monkeypatch):
    """Browsing reads the installation's operations; only running declares."""
    offer = _offer("gmail")
    reach = _no_reach()
    reach.installation = AsyncMock(return_value=offer)
    discover = AsyncMock(return_value=SimpleNamespace(items=[{"name": "send"}]))
    pod_ops = AsyncMock()
    _patch_services(
        monkeypatch,
        SimpleNamespace(
            ctx=object(),
            reach=reach,
            operations=SimpleNamespace(
                discover_operations=discover, pod_connector_operations=pod_ops
            ),
        ),
    )

    result = await adapter.search_connector_operations(
        _run_ctx(), SearchConnectorOperationsRequest(connector="gmail", query="send")
    )

    assert discover.await_args.kwargs["auth_config_id"] == offer.auth_config_id
    pod_ops.assert_not_awaited()
    reach.slug_for.assert_not_awaited()
    assert result["items"][0]["connector"] == "gmail"


async def test_running_an_own_installation_declares_and_commits_it_first(
    monkeypatch,
):
    """The first run declares the pod connector and commits it before the
    call is authorized, so a named agent refused it can be offered the grant
    on a connector that exists; the run then goes through the declared slug."""
    reach = _no_reach()
    reach.slug_for = AsyncMock(return_value="gmail-2")
    uow = SimpleNamespace(commit=AsyncMock())
    detail = AsyncMock(return_value=SimpleNamespace(input_schema=None))
    refusal = DomainError("no", code="MISSING_WORKLOAD_RESOURCE_GRANT", status_code=403)
    _patch_services(
        monkeypatch,
        SimpleNamespace(
            ctx=object(),
            uow=uow,
            reach=reach,
            operations=SimpleNamespace(
                pod_connector_operation=detail,
                resolve_pod_connector_execution=AsyncMock(side_effect=refusal),
            ),
        ),
    )

    result = await adapter.run_connector_operation(
        _run_ctx(),
        RunConnectorOperationRequest(connector="gmail", operation="send"),
    )

    uow.commit.assert_awaited_once()
    assert detail.await_args.kwargs["slug"] == "gmail-2"
    assert result["success"] is False
    assert result["connector"] == "gmail-2"
