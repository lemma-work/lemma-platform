"""Connecting pods: one lets another ask it, with nobody present.

Against real grants in a real database, because a link *is* grant rows and the
whole point is what those rows let another pod read. Billing connects Support
and shares one table; the run that answers Support then reads that table and
nothing else of Billing's -- not the table it kept, and nothing it could write.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.modules.agent.domain.outsiders import answers_outsiders
from app.modules.agent.domain.pod_asks import (
    ANSWER_SOURCE,
    AskMode,
    AskRefused,
    ask_of,
    linked_asker,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.pod_ask_delivery import PodAskDelivery
from app.modules.agent.services.pod_ask_service import PodAskService
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.tests.e2e.test_agent_e2e import _create_test_pod
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.models import (
    PodGetRecordsRequest,
    PodTablesRequest,
    PodWriteRecordRequest,
)
from app.modules.agent.tools.pod.pydantic_adapter import (
    pod_get_records,
    pod_tables,
    pod_write_record,
)
from app.modules.test_support.e2e.function_helpers import create_table
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

_SHARED = "invoices"
_KEPT = "payroll"
_READ_TABLE = ["datastore.table.read", "datastore.record.read"]


def _uow_factory() -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


async def _billing_with_two_tables(client, org) -> tuple[str, str]:
    support = await _create_test_pod(client, org)
    billing = await _create_test_pod(client, org)
    for table in (_SHARED, _KEPT):
        await create_table(client, billing, table, enable_rls=False)
        made = await client.post(
            f"/pods/{billing}/datastore/tables/{table}/records",
            json={"data": {"title": f"a {table} row"}},
        )
        assert made.status_code == status.HTTP_201_CREATED, made.text
    return support, billing


async def _connect(client, *, billing: str, support: str, shares=None):
    return await client.put(
        f"/pods/{billing}/pod-links/{support}",
        json={"shares": shares or []},
    )


def _as_support_in_billing(*, user_id: UUID, org_id: UUID, billing: str, support: str):
    """The deps a run in Billing answering Support over the link carries."""
    return SimpleNamespace(
        deps=BaseAgentContext(
            user_id=user_id,
            org_id=org_id,
            pod_id=UUID(billing),
            conversation_id=uuid4(),
            answers_outsider=True,
            asking_pod_id=UUID(support),
        )
    )


@pytest.mark.asyncio
async def test_a_connected_pod_reads_what_it_was_shared_and_nothing_else(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _billing_with_two_tables(
        authenticated_client, fixed_test_org
    )
    connected = await _connect(
        authenticated_client,
        billing=billing,
        support=support,
        shares=[
            {
                "resource_type": "datastore_table",
                "resource_name": _SHARED,
                "permission_ids": _READ_TABLE,
            }
        ],
    )
    assert connected.status_code == status.HTTP_204_NO_CONTENT, connected.text

    links = (await authenticated_client.get(f"/pods/{billing}/pod-links")).json()
    [link] = links["items"]
    assert link["pod_id"] == support
    assert link["steward_user_id"] == fixed_test_user["id"]
    assert [one["resource_name"] for one in link["shared"]] == [_SHARED]

    askable = (await authenticated_client.get(f"/pods/{support}/askable-pods")).json()
    billing_row = next(item for item in askable["items"] if item["pod_id"] == billing)
    assert billing_row["connected"] is True

    ctx = _as_support_in_billing(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        billing=billing,
        support=support,
    )
    listed = await pod_tables(ctx, PodTablesRequest())
    assert listed["success"] is True, listed
    assert [table["name"] for table in listed["tables"]] == [_SHARED]

    shared = await pod_get_records(ctx, PodGetRecordsRequest(table_name=_SHARED))
    assert shared["success"] is True, shared
    assert [row["title"] for row in shared["records"]] == [f"a {_SHARED} row"]

    kept = await pod_get_records(ctx, PodGetRecordsRequest(table_name=_KEPT))
    assert kept["success"] is False

    written = await pod_write_record(
        ctx,
        PodWriteRecordRequest(
            table_name=_SHARED, action="create", data={"title": "from support"}
        ),
    )
    assert written["success"] is False


@pytest.mark.asyncio
async def test_a_link_can_share_for_reading_only(authenticated_client, fixed_test_org):
    support, billing = await _billing_with_two_tables(
        authenticated_client, fixed_test_org
    )
    refused = await _connect(
        authenticated_client,
        billing=billing,
        support=support,
        shares=[
            {
                "resource_type": "datastore_table",
                "resource_name": _SHARED,
                "permission_ids": ["datastore.record.write"],
            }
        ],
    )
    assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.text


@pytest.mark.asyncio
async def test_only_an_admin_here_who_is_also_in_the_other_pod_can_connect(
    authenticated_client, async_client, fixed_test_org
):
    support, billing = await _billing_with_two_tables(
        authenticated_client, fixed_test_org
    )
    colleague = await signup_user(async_client, f"colleague-{uuid4().hex[:6]}")
    member = await invite_org_member(
        authenticated_client, async_client, org_id=fixed_test_org["id"], user=colleague
    )
    for pod in (support, billing):
        await add_pod_member(
            authenticated_client,
            pod_id=pod,
            organization_member_id=member["id"],
            role="POD_USER",
            roles=["POD_USER"],
        )

    # In both pods, but not an admin of the one being asked.
    not_admin = await async_client.put(
        f"/pods/{billing}/pod-links/{support}",
        json={"shares": []},
        headers=auth_headers(colleague),
    )
    assert not_admin.status_code == status.HTTP_403_FORBIDDEN, not_admin.text

    # An admin here, but not in the pod they would let in.
    theirs = await async_client.post(
        "/pods",
        json={
            "name": f"Theirs {uuid4().hex[:6]}",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
        headers=auth_headers(colleague),
    )
    assert theirs.status_code == status.HTTP_201_CREATED, theirs.text
    outside = await _connect(
        authenticated_client, billing=billing, support=theirs.json()["id"]
    )
    assert outside.status_code == status.HTTP_409_CONFLICT, outside.text


@pytest.mark.asyncio
async def test_with_nobody_present_a_pod_asks_over_its_link_and_hears_back(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _billing_with_two_tables(
        authenticated_client, fixed_test_org
    )
    assert (
        await _connect(authenticated_client, billing=billing, support=support)
    ).status_code == status.HTTP_204_NO_CONTENT
    # A conversation nobody typed in, the way a schedule's is.
    made = await authenticated_client.post(
        f"/pods/{support}/conversations", json={"title": "nightly", "type": "TASK"}
    )
    assert made.status_code == status.HTTP_201_CREATED, made.text
    asking = UUID(made.json()["id"])
    deps = BaseAgentContext(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=UUID(support),
        conversation_id=asking,
        is_pod_default_agent=True,
    )
    service = PodAskService(_uow_factory())
    teammate = next(
        one
        for one in await service.teammates(
            user_id=deps.user_id, organization_id=deps.org_id, pod_id=deps.pod_id
        )
        if str(one.pod_id) == billing
    )
    assert await service.ensure_may_ask(deps, teammate=teammate) is AskMode.LINK

    with suppress_agent_run_enqueue():
        outcome = await service.ask(
            deps, teammate=teammate, request="Anything overdue?", wait_seconds=0
        )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversations = ConversationRepository(uow)
        thread = await conversations.get_conversation(
            outcome.conversation_id, include_runs=True
        )
        assert thread is not None
        assert ask_of(thread).mode is AskMode.LINK
        # The run answering it is the outsider machinery, as Support.
        assert answers_outsiders(thread)
        assert linked_asker(thread) == UUID(support)
        # Nobody in Billing typed the request, and the message says whose it is.
        [request] = (
            await conversations.list_messages(conversation_id=thread.id, limit=10)
        )[0]
        assert request.metadata["ask_from_pod_id"] == support
        assert request.metadata["ask_mode"] == AskMode.LINK.value
        assert request.metadata["ask_from_pod_name"]
        run = thread.agent_runs[-1]
        await conversations.append_message(
            conversation_id=thread.id,
            agent_run_id=run.id,
            draft=MessageDraft.of_text("Two are overdue.", role=MessageRole.ASSISTANT),
        )
        await conversations.finish_agent_run(
            agent_run_id=run.id,
            status=AgentRunStatus.COMPLETED,
            conversation_status=ConversationStatus.COMPLETED,
        )
        await uow.commit()

    with suppress_agent_run_enqueue():
        assert (
            await PodAskDelivery(_uow_factory()).settle(
                conversation_id=outcome.conversation_id
            )
            == 1
        )
    async with create_uow_from_session_maker(async_session_maker) as uow:
        messages, _ = await ConversationRepository(uow).list_messages(
            conversation_id=asking, limit=50
        )
    delivered = [
        m for m in messages if (m.metadata or {}).get("source") == ANSWER_SOURCE
    ]
    assert len(delivered) == 1
    assert "Two are overdue." in delivered[0].text


@pytest.mark.asyncio
async def test_disconnecting_ends_the_asks_and_takes_back_what_was_shared(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _billing_with_two_tables(
        authenticated_client, fixed_test_org
    )
    await _connect(
        authenticated_client,
        billing=billing,
        support=support,
        shares=[
            {
                "resource_type": "datastore_table",
                "resource_name": _SHARED,
                "permission_ids": _READ_TABLE,
            }
        ],
    )
    gone = await authenticated_client.delete(f"/pods/{billing}/pod-links/{support}")
    assert gone.status_code == status.HTTP_204_NO_CONTENT, gone.text
    again = await authenticated_client.delete(f"/pods/{billing}/pod-links/{support}")
    assert again.status_code == status.HTTP_404_NOT_FOUND, again.text

    askable = (await authenticated_client.get(f"/pods/{support}/askable-pods")).json()
    billing_row = next(item for item in askable["items"] if item["pod_id"] == billing)
    assert billing_row["connected"] is False

    ctx = _as_support_in_billing(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        billing=billing,
        support=support,
    )
    read = await pod_get_records(ctx, PodGetRecordsRequest(table_name=_SHARED))
    assert read["success"] is False

    made = await authenticated_client.post(
        f"/pods/{support}/conversations", json={"title": "nightly", "type": "TASK"}
    )
    deps = BaseAgentContext(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=UUID(support),
        conversation_id=UUID(made.json()["id"]),
        is_pod_default_agent=True,
    )
    service = PodAskService(_uow_factory())
    teammate = next(
        one
        for one in await service.teammates(
            user_id=deps.user_id, organization_id=deps.org_id, pod_id=deps.pod_id
        )
        if str(one.pod_id) == billing
    )
    with pytest.raises(AskRefused):
        await service.ensure_may_ask(deps, teammate=teammate)
