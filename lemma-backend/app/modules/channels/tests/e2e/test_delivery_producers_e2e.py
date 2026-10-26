"""Every line that reaches somebody outside the app is a delivery row.

``channel_message_deliveries`` is the only record of a send: the timeline
shows a line's delivery from it, the worker sends from it, a retry is a row
going round again. A producer that posted a line meant for a person's phone
and planned no row would be a line nobody is ever told of, with nothing to
say so. ``tests/unit/test_delivery_producers.py`` lists every producer and
names the test that proves its rows; these are the proofs for the ones no
other test watched, each driving the producer itself against Postgres.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.contracts import channel_conversations
from app.modules.agent.domain.value_objects import ConversationStatus
from app.modules.agent.services.pause_questions import sync_pause_questions, tool_ref
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    finish,
    pausing_call,
    running_run,
    seeded_conversation,
)
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.messaging.models import ChannelPostRequest
from app.modules.agent.infrastructure.tools.messaging.people import call_a_person_in
from app.modules.agent.infrastructure.tools.messaging.pydantic_adapter import (
    channel_post,
)
from app.modules.agent_surfaces.services.direct_messages import tell_member
from app.modules.channels.contracts import (
    Delivery,
    DeliveryStatus,
    contact_channel,
    direct_channel,
)
from app.modules.channels.contracts import questions as channel_questions
from app.modules.channels.contracts.deliveries import deliveries_of
from app.modules.channels.tests.e2e.push_support import run_pushes
from app.modules.channels.infrastructure.models import ChannelMessageModel
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.workflow.infrastructure.form_questions import FormQuestions

from app.modules.workflow.infrastructure.models import (
    WorkflowModel,
    WorkflowRunModel,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


# ------------------------------------------------------------------- harness


async def _rows(message_id: UUID) -> list[Delivery]:
    """A line's sends, once its pushes are worked out (after its commit)."""
    await run_pushes()
    async with create_uow_from_session_maker(async_session_maker) as uow:
        return await deliveries_of(uow, [message_id])


def _pushes(rows: list[Delivery]) -> set[UUID]:
    """The members a line is pushed to; each planned once, sent by the worker."""
    for row in rows:
        assert row.status is DeliveryStatus.PENDING, row
        assert row.source_id is None, "a push names a member, not a place"
    return {row.recipient_user_id for row in rows if row.recipient_user_id}


async def _lines_in(channel_id: UUID) -> list[ChannelMessageModel]:
    async with async_session_maker() as session:
        return list(
            await session.scalars(
                select(ChannelMessageModel)
                .where(ChannelMessageModel.channel_id == channel_id)
                .order_by(ChannelMessageModel.seq)
            )
        )


async def _colleague(scenario: E2EScenario, name: str) -> tuple[dict, UUID]:
    """Another member of the pod: the person, and their pod membership's id."""
    person = await scenario.create_user(name)
    await scenario.add_user_to_pod(user=person, role="POD_EDITOR")
    found = await scenario.owner_client.get(
        f"/pods/{scenario.pod_id}/members/lookup/by-user-id/{person['id']}"
    )
    assert found.status_code == 200, found.text
    return person, UUID(found.json()["pod_member_id"])


def _ctx(seeded: Seeded, *, call: str) -> RunContext[ToolContext]:
    """The run's context a messaging tool is called with."""
    return RunContext(
        deps=ToolContext(
            user_id=seeded.user_id,
            pod_id=seeded.pod_id,
            conversation_id=seeded.conversation_id,
            workload_id=seeded.agent_id,
            agent_name=seeded.agent_name,
        ),
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
        tool_call_id=call,
    )


# ------------------------------------------------------------ pushes to members


async def test_an_agent_writing_to_a_member_plans_their_push(scenario):
    """``channel_post(to=…)``: a line in their own channel, and its push."""
    seeded = await seeded_conversation(scenario)
    colleague, _ = await _colleague(scenario, "told-by-agent")

    sent = await channel_post(
        _ctx(seeded, call="tell-1"),
        ChannelPostRequest(text="The report is ready.", to=colleague["email"]),
    )

    assert sent.success, sent.error
    assert sent.message_id is not None
    assert _pushes(await _rows(sent.message_id)) == {UUID(colleague["id"])}


async def test_asking_a_member_to_connect_plans_their_push(scenario):
    """ "Ask members to connect" (`pod.connector.manage`): a line in the
    member's own channel, said by the pod's agent for the editor, and its push
    -- the reminder has to reach them, not wait in the app."""
    from app.modules.connectors.infrastructure.adapters.member_coverage import (
        tell_through_channels,
    )

    seeded = await seeded_conversation(scenario)
    colleague, _ = await _colleague(scenario, "asked-to-connect")
    editor = seeded.user_id
    pod_id = seeded.pod_id
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await tell_through_channels(
            uow,
            pod_id=pod_id,
            user_id=UUID(colleague["id"]),
            asked_by=editor,
            text="This pod uses Gmail — connect yours: https://app.test/c",
            key=f"connect-ask:{uuid4()}",
        )
        theirs = await direct_channel(uow, pod_id=pod_id, user_id=UUID(colleague["id"]))
        await uow.commit()

    [line] = await _lines_in(theirs.id)
    assert "connect yours" in line.body
    assert _pushes(await _rows(line.id)) == {UUID(colleague["id"])}


async def test_an_agent_writing_in_its_members_channel_plans_their_push(scenario):
    """``channel_post(channel_id=…)`` -- the one channel it may write in by id
    for the run's member, their own: the line, and its push to them. (A group
    is refused: a line made with one member's access is not the others'.)"""
    seeded = await seeded_conversation(scenario)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        theirs = await direct_channel(uow, pod_id=seeded.pod_id, user_id=seeded.user_id)
        await uow.commit()

    sent = await channel_post(
        _ctx(seeded, call="own-1"),
        ChannelPostRequest(text="Your report is ready.", channel_id=theirs.id),
    )

    assert sent.success, sent.error
    assert sent.message_id is not None
    assert _pushes(await _rows(sent.message_id)) == {seeded.user_id}


async def test_a_paused_run_asks_its_member_and_plans_their_push(scenario):
    """A run paused on ``ask_user`` puts the question in the member's channel
    (``sync_pause_questions``), and it is pushed to them: it waits on them."""
    seeded = await seeded_conversation(scenario)
    run_id = await running_run(seeded)
    call = await pausing_call(
        seeded,
        run_id,
        tool_name="ask_user",
        tool_args={"questions": [{"id": "size", "question": "Which size?"}]},
    )
    await finish(run_id, conversation_status=ConversationStatus.WAITING)

    async with create_uow_from_session_maker(async_session_maker) as uow:
        await sync_pause_questions(uow, seeded.conversation_id)
        await uow.commit()
        asked = await channel_questions.question_by_ref(
            uow, pod_id=seeded.pod_id, ref=tool_ref(seeded.conversation_id, call)
        )

    assert asked is not None
    assert _pushes(await _rows(asked.message_id)) == {seeded.user_id}


async def test_a_workflow_form_plans_a_push_to_whom_it_is_assigned(scenario):
    await scenario.create_org_with_pod(name_prefix="Form push")
    colleague, membership_id = await _colleague(scenario, "form-assignee")
    pod_id, wait_id = UUID(scenario.pod_id), uuid4()
    owner = UUID(scenario.owner_user["id"])

    async with create_uow_from_session_maker(async_session_maker) as uow:
        # The run that asks: a question names it, so it must exist.
        flow = WorkflowModel(pod_id=pod_id, user_id=owner, name="Expenses")
        uow.session.add(flow)
        await uow.session.flush()
        run = WorkflowRunModel(flow_id=flow.id, pod_id=pod_id, user_id=owner)
        uow.session.add(run)
        await uow.session.flush()
        await FormQuestions(uow).ask_form(
            pod_id=pod_id,
            run_id=run.id,
            flow_id=flow.id,
            node_id="approve",
            wait_id=wait_id,
            assigned_pod_member_id=membership_id,
            flow_name="Expenses",
            schema={"properties": {"amount": {"title": "Amount"}}},
            actor_user_id=None,
            expires_at=None,
        )
        await uow.commit()
        direct = await direct_channel(uow, pod_id=pod_id, user_id=UUID(colleague["id"]))

    [line] = [
        line
        for line in await _lines_in(direct.id)
        if line.client_message_id == f"wf:{wait_id}"
    ]
    assert _pushes(await _rows(line.id)) == {UUID(colleague["id"])}


async def test_a_contacts_run_calling_a_person_in_plans_the_keepers_push(scenario):
    """``ask_a_person``: the contact's channel asks for somebody, and the
    member who looks after them hears so, pushed."""
    seeded = await seeded_conversation(scenario)
    keeper, _ = await _colleague(scenario, "keeper")
    keeper_id = UUID(keeper["id"])
    async with create_uow_from_session_maker(async_session_maker) as uow:
        contact = await open_contact(
            uow,
            pod_id=seeded.pod_id,
            kind=IdentityKind.EMAIL,
            value=f"dana-{uuid4().hex[:6]}@example.com",
            strength=IdentityStrength.CHANNEL,
            display_name="Dana",
        )
        theirs = await contact_channel(uow, pod_id=seeded.pod_id, contact_id=contact.id)
        await channel_conversations.place_in_channel(
            uow, conversation_id=seeded.conversation_id, channel_id=theirs.id
        )
        await uow.commit()

    called = await call_a_person_in(
        pod_id=seeded.pod_id,
        conversation_id=seeded.conversation_id,
        keeper_user_id=keeper_id,
        reason="Wants a refund over the limit",
        key="refund-1",
    )

    assert called
    async with create_uow_from_session_maker(async_session_maker) as uow:
        direct = await direct_channel(uow, pod_id=seeded.pod_id, user_id=keeper_id)
    [told] = await _lines_in(direct.id)
    assert _pushes(await _rows(told.id)) == {keeper_id}


# ------------------------------------------------------------ nothing outside


async def test_a_notice_is_a_note_in_their_channel_and_never_sent(scenario):
    """``tell_member``: a members-only note waits in the app, so it has no row
    at all -- not a push, not a place."""
    await scenario.create_org_with_pod(name_prefix="Notice")
    pod_id, owner = UUID(scenario.pod_id), UUID(scenario.owner_user["id"])

    async with create_uow_from_session_maker(async_session_maker) as uow:
        line = await tell_member(
            uow, pod_id=pod_id, user_id=owner, text="Your export is ready."
        )
        await uow.commit()

    assert line is not None and line.members_only
    assert await _rows(line.id) == []
