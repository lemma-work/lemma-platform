"""Connecting pods: one lets another ask it, with nobody present.

Against real grants in a real database, because a link *is* grant rows and the
whole point is what those rows let another pod read. Billing connects Support
and shares one table; the run that answers Support then reads that table and
nothing else of Billing's -- not the table it kept, and nothing it could write.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.pod_asks import (
    ANSWER_SOURCE,
    AskMode,
    AskRefused,
    ask_of,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    MessageDraft,
    MessageRole,
)
import app.modules.agent.events.pod_links_lifecycle  # noqa: F401  subscribes to PodDeleted
from app.modules.agent.infrastructure.pod_ask_queries import PodAskQueries
from app.modules.agent.infrastructure.pod_link_queries import PodLinkQueries
from app.modules.agent.services.pod_link_guards import LINK_RUNS_PER_WINDOW
from app.modules.agent.tests.e2e.pod_link_helpers import (
    asking_deps,
    folder_share,
    task_conversation,
    teammate as find_teammate,
)
from app.modules.pod.contracts.lifecycle import PodDeleted
from app.core.infrastructure.work import WorkClass, facts
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.test_support.e2e.function_helpers import create_folder
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.runs import Runs
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
            audience=Audience.pod(UUID(support)),
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
        conversations = Conversations(uow)
        messages_repo = Messages(uow)
        runs = Runs(uow)
        thread = await conversations.get_conversation(
            outcome.conversation_id, include_runs=True
        )
        assert thread is not None
        assert ask_of(thread).mode is AskMode.LINK
        # The run answering it is the outsider machinery, as Support.
        audience = Audience.of(thread)
        assert audience.answers_outsiders
        assert audience.asking_pod_id == UUID(support)
        # Nobody in Billing typed the request, and the message says whose it is.
        [request] = (
            await messages_repo.list_messages(conversation_id=thread.id, limit=10)
        )[0]
        assert request.metadata["ask_from_pod_id"] == support
        assert request.metadata["ask_mode"] == AskMode.LINK.value
        assert request.metadata["ask_from_pod_name"]
        run = thread.agent_runs[-1]
        await messages_repo.append_message(
            conversation_id=thread.id,
            agent_run_id=run.id,
            draft=MessageDraft.of_text("Two are overdue.", role=MessageRole.ASSISTANT),
        )
        await runs.finish_agent_run(
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
        messages, _ = await Messages(uow).list_messages(
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


@pytest.mark.parametrize(
    ("share", "code"),
    [
        (
            {
                "resource_type": "datastore_table",
                "resource_name": _SHARED,
                "permission_ids": ["datastore.table.read", "datastore.table.delete"],
            },
            "POD_LINK_READ_ONLY",
        ),
        (
            {
                "resource_type": "datastore_table",
                "resource_name": _SHARED,
                "permission_ids": ["datastore.record.read", "datastore.record.delete"],
            },
            "POD_LINK_READ_ONLY",
        ),
        (
            {
                "resource_type": "folder",
                "resource_name": "/library",
                "permission_ids": ["folder.read", "folder.write"],
            },
            "POD_LINK_READ_ONLY",
        ),
        (
            {
                "resource_type": "function",
                "resource_name": "anything",
                "permission_ids": ["function.execute"],
            },
            "POD_LINK_UNSHAREABLE",
        ),
        (
            {
                "resource_type": "datastore_table",
                "resource_name": "mine",
                "permission_ids": _READ_TABLE,
            },
            "POD_LINK_PERSONAL",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_link_refuses_anything_but_reading_a_pod_table_or_folder(
    authenticated_client, fixed_test_org, share, code
):
    support, billing = await _billing_with_two_tables(
        authenticated_client, fixed_test_org
    )
    await create_folder(authenticated_client, billing, "/library")
    await create_table(
        authenticated_client, billing, "mine", visibility="PERSONAL", enable_rls=False
    )

    refused = await _connect(
        authenticated_client, billing=billing, support=support, shares=[share]
    )

    assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.text
    assert refused.json()["code"] == code, refused.text
    links = (await authenticated_client.get(f"/pods/{billing}/pod-links")).json()
    assert links["items"] == []


@pytest.mark.asyncio
async def test_a_personal_folder_is_not_shareable_either(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _billing_with_two_tables(
        authenticated_client, fixed_test_org
    )
    await create_folder(authenticated_client, billing, "/me/drafts")

    # Shared by its stored path: ``/me`` is the owner's alias for it, and a
    # grant names the folder itself.
    refused = await _connect(
        authenticated_client,
        billing=billing,
        support=support,
        shares=[folder_share(f"/{fixed_test_user['id']}/drafts")],
    )

    assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.text
    assert refused.json()["code"] == "POD_LINK_PERSONAL", refused.text


@pytest.mark.asyncio
async def test_pods_in_different_organizations_cannot_be_connected(
    authenticated_client, fixed_test_org
):
    billing = await _create_test_pod(authenticated_client, fixed_test_org)
    other_org = await authenticated_client.post(
        "/organizations", json={"name": f"Elsewhere {uuid4().hex[:8]}"}
    )
    assert other_org.status_code == status.HTTP_201_CREATED, other_org.text
    elsewhere = await _create_test_pod(authenticated_client, other_org.json())

    refused = await _connect(authenticated_client, billing=billing, support=elsewhere)

    assert refused.status_code == status.HTTP_409_CONFLICT, refused.text
    assert "same organization" in refused.text


@pytest.mark.drives_work
@pytest.mark.asyncio
async def test_a_deleted_pod_takes_its_links_with_it(
    authenticated_client, fixed_test_org
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
    # An unrelated link the deletion must leave alone.
    bystander = await _create_test_pod(authenticated_client, fixed_test_org)
    await _connect(authenticated_client, billing=billing, support=bystander)

    # As the pod's deletion writes it: the fact, then the work it fans out to.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await facts.emit(
            uow,
            PodDeleted(
                pod_id=UUID(support), organization_id=UUID(fixed_test_org["id"])
            ),
        )
        await uow.commit()
    await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])

    async with create_uow_from_session_maker(async_session_maker) as uow:
        links = PodLinkQueries(uow)
        assert (
            await links.link(
                answering_pod_id=UUID(billing), asking_pod_id=UUID(support)
            )
            is None
        )
        assert (
            await links.shared(
                answering_pod_id=UUID(billing), asking_pod_id=UUID(support)
            )
            == []
        )
        assert [
            row.asking_pod_id
            for row in await links.incoming(answering_pod_id=UUID(billing))
        ] == [UUID(bystander)]


async def _colleague_stewarding(
    client, async_client, org
) -> tuple[str, str, dict, dict]:
    """Billing linked to Support by a colleague: admin of Billing, in Support."""
    support, billing = await _billing_with_two_tables(client, org)
    colleague = await signup_user(async_client, f"steward-{uuid4().hex[:6]}")
    member = await invite_org_member(
        client, async_client, org_id=org["id"], user=colleague
    )
    memberships = {}
    for pod, role in ((support, "POD_USER"), (billing, "POD_ADMIN")):
        memberships[pod] = await add_pod_member(
            client,
            pod_id=pod,
            organization_member_id=member["id"],
            role=role,
            roles=[role],
        )
    linked = await async_client.put(
        f"/pods/{billing}/pod-links/{support}",
        json={"shares": []},
        headers=auth_headers(colleague),
    )
    assert linked.status_code == status.HTTP_204_NO_CONTENT, linked.text
    return support, billing, colleague, memberships


async def _ask_over_link(client, org, user, *, support: str, billing: str):
    deps = asking_deps(
        user_id=UUID(user["id"]),
        org_id=UUID(org["id"]),
        pod_id=support,
        conversation_id=await task_conversation(client, support),
    )
    with suppress_agent_run_enqueue():
        return await PodAskService(_uow_factory()).ask(
            deps,
            teammate=await find_teammate(deps, billing),
            request="Anything overdue?",
            wait_seconds=0,
        )


def _member_id(membership: dict) -> str:
    return str(membership.get("pod_member_id") or membership["id"])


@pytest.mark.asyncio
async def test_a_link_stops_working_once_its_steward_could_no_longer_make_it(
    authenticated_client, async_client, fixed_test_org, fixed_test_user
):
    client = authenticated_client
    support, billing, _, memberships = await _colleague_stewarding(
        client, async_client, fixed_test_org
    )

    async def ask():
        return await _ask_over_link(
            client, fixed_test_org, fixed_test_user, support=support, billing=billing
        )

    opened = await ask()
    assert opened.mode is AskMode.LINK
    async with create_uow_from_session_maker(async_session_maker) as uow:
        thread = await Conversations(uow).get_conversation(opened.conversation_id)
    assert thread is not None and thread.user_id != UUID(fixed_test_user["id"])

    billing_member = _member_id(memberships[billing])
    demoted = await client.patch(
        f"/pods/{billing}/members/{billing_member}/roles", json={"roles": ["POD_USER"]}
    )
    assert demoted.status_code == status.HTTP_200_OK, demoted.text
    with pytest.raises(AskRefused, match="looks after its link"):
        await ask()

    promoted = await client.patch(
        f"/pods/{billing}/members/{billing_member}/roles", json={"roles": ["POD_ADMIN"]}
    )
    assert promoted.status_code == status.HTTP_200_OK, promoted.text
    assert (await ask()).mode is AskMode.LINK

    left = await client.delete(
        f"/pods/{support}/members/{_member_id(memberships[support])}"
    )
    assert left.status_code == status.HTTP_204_NO_CONTENT, left.text
    with pytest.raises(AskRefused, match="looks after its link"):
        await ask()


@pytest.mark.asyncio
async def test_a_link_stops_working_once_its_steward_leaves_the_pod_it_shares(
    authenticated_client, async_client, fixed_test_org, fixed_test_user
):
    client = authenticated_client
    support, billing, _, memberships = await _colleague_stewarding(
        client, async_client, fixed_test_org
    )
    left = await client.delete(
        f"/pods/{billing}/members/{_member_id(memberships[billing])}"
    )
    assert left.status_code == status.HTTP_204_NO_CONTENT, left.text

    with pytest.raises(AskRefused, match="looks after its link"):
        await _ask_over_link(
            client, fixed_test_org, fixed_test_user, support=support, billing=billing
        )


@pytest.mark.asyncio
async def test_a_link_starts_at_most_its_hourly_cap_of_runs(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _billing_with_two_tables(
        authenticated_client, fixed_test_org
    )
    await _connect(authenticated_client, billing=billing, support=support)
    first = await _ask_over_link(
        authenticated_client,
        fixed_test_org,
        fixed_test_user,
        support=support,
        billing=billing,
    )
    # Runs the link already started this hour, all but one of the cap: the
    # thread the first ask opened carries them, as a busy schedule's would.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversations = Conversations(uow)
        runs = Runs(uow)
        thread = await conversations.get_conversation(
            first.conversation_id, include_runs=True
        )
        assert thread is not None
        model_run = thread.agent_runs[-1]
        await runs.finish_agent_run(
            agent_run_id=model_run.id, status=AgentRunStatus.COMPLETED
        )
        # One run at a time per conversation, so each is finished as it goes.
        for _ in range(LINK_RUNS_PER_WINDOW - 2):
            run = await runs.create_agent_run(
                conversation_id=thread.id,
                agent_id=model_run.agent_id,
                agent_runtime=model_run.agent_runtime,
            )
            await runs.finish_agent_run(
                agent_run_id=run.id, status=AgentRunStatus.COMPLETED
            )
        await uow.commit()
    async with create_uow_from_session_maker(async_session_maker) as uow:
        started = await PodAskQueries(uow).link_runs_since(
            to_pod_id=UUID(billing),
            from_pod_id=UUID(support),
            since=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    assert started == LINK_RUNS_PER_WINDOW - 1

    # The thirtieth is allowed; the thirty-first is not.
    thirtieth = await _ask_over_link(
        authenticated_client,
        fixed_test_org,
        fixed_test_user,
        support=support,
        billing=billing,
    )
    assert thirtieth.mode is AskMode.LINK
    with pytest.raises(
        AskRefused, match=f"{LINK_RUNS_PER_WINDOW} times in the last hour"
    ):
        await _ask_over_link(
            authenticated_client,
            fixed_test_org,
            fixed_test_user,
            support=support,
            billing=billing,
        )
