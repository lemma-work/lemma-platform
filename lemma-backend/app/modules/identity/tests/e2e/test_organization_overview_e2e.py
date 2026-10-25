"""An organization's overview, read once for every pod a member may open.

Four endpoints -- what waits on the member, what moved in each pod, every app,
and when they last looked at Home -- each replacing a request per pod. So two
things are pinned: that each shows exactly what the per-pod reads show this
member (their own conversations, the waits assigned to them, contacts only
where they may see contacts, apps they may open), and that the query count
stays flat as pods are added, which is the reason they exist.

Against Postgres, because almost every rule here is a SQL predicate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentRuntimeConfig,
    ConversationStatus,
    MessageDraft,
)
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.apps.infrastructure.models import AppModel
from app.modules.channels.tests.e2e.channel_pod import contact_place
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
)
from app.modules.test_support.query_counting import counted_queries
from app.modules.workflow.infrastructure.models import (
    WorkflowModel,
    WorkflowRunModel,
    WorkflowRunWaitModel,
)

pytestmark = [pytest.mark.e2e]

_QUESTION = {"questions": [{"question": "Which region should it cover?"}]}
_APPROVAL = {"title": "Delete the draft invoices", "tool_name": "pod_delete_record"}


# -- the organization and the people in it ----------------------------------------


async def _org_with_pods(scenario: E2EScenario, count: int) -> list[str]:
    await scenario.create_org(name_prefix="Overview")
    return [
        (await scenario.create_pod(name_prefix=f"Overview {index}"))["id"]
        for index in range(count)
    ]


async def _member(
    scenario: E2EScenario, *, pods: dict[str, str], prefix: str
) -> dict[str, str]:
    """A new organization member, in each of ``pods`` with the role given."""
    person = await scenario.create_user(prefix)
    org_member = await invite_org_member(
        scenario.owner_client,
        scenario.async_client,
        org_id=scenario.org_id,
        user=person,
    )
    for pod_id, role in pods.items():
        await add_pod_member(
            scenario.owner_client,
            pod_id=pod_id,
            organization_member_id=org_member["id"],
            role=role,
        )
    return person


def _as(scenario: E2EScenario, who: dict[str, str] | None):
    """GET as ``who``, or as the owner."""

    async def get(url: str, **kwargs):
        if who is None:
            return await scenario.owner_client.get(url, **kwargs)
        return await scenario.async_client.get(url, headers=auth_headers(who), **kwargs)

    return get


async def _pod_member_id(scenario: E2EScenario, pod_id: str, user_id: str) -> UUID:
    found = await scenario.owner_client.get(
        f"/pods/{pod_id}/members/lookup/by-user-id/{user_id}"
    )
    assert found.status_code == status.HTTP_200_OK, found.text
    return UUID(found.json()["pod_member_id"])


# -- conversations -----------------------------------------------------------------


async def _conversation(
    scenario: E2EScenario,
    pod_id: str,
    *,
    title: str,
    who: dict[str, str] | None = None,
    metadata: dict | None = None,
    kind: str = "CHAT",
) -> UUID:
    body = {"title": title, "type": kind, "metadata": metadata or {}}
    if who is None:
        created = await scenario.owner_client.post(
            f"/pods/{pod_id}/conversations", json=body
        )
    else:
        created = await scenario.async_client.post(
            f"/pods/{pod_id}/conversations", json=body, headers=auth_headers(who)
        )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return UUID(created.json()["id"])


async def _touch(conversation_id: UUID, **values) -> None:
    """Scenery written straight in: how a conversation got here is not the
    subject."""
    async with async_session_maker() as session:
        await session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .values(**values)
        )
        await session.commit()


async def _pause(
    conversation_id: UUID, *, tool_name: str, tool_args: dict, ago: timedelta
) -> str:
    """A run that called ``tool_name`` ``ago`` and left the conversation WAITING."""
    tool_call_id = f"call-{uuid4().hex[:12]}"
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        run = await repo.create_agent_run(
            conversation_id=conversation_id,
            agent_id=None,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
        )
        message = await repo.append_message(
            conversation_id=conversation_id,
            agent_run_id=run.id,
            draft=MessageDraft.of_tool_call(
                tool_call_id=tool_call_id, tool_name=tool_name, tool_args=tool_args
            ),
        )
        # A pause ends its run and leaves the conversation waiting.
        await uow.session.execute(
            update(AgentRunModel)
            .where(AgentRunModel.id == run.id)
            .values(status=AgentRunStatus.COMPLETED.value)
        )
        await uow.session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .values(status=ConversationStatus.WAITING.value)
        )
        await uow.session.execute(
            update(MessageModel)
            .where(MessageModel.id == message.id)
            .values(created_at=datetime.now(UTC) - ago)
        )
        await uow.commit()
    return tool_call_id


# -- workflows, schedules, apps ------------------------------------------------------


async def _workflow(
    pod_id: str, user_id: str, *, name: str, visibility: str = "POD"
) -> UUID:
    async with async_session_maker() as session:
        flow = WorkflowModel(
            pod_id=UUID(pod_id),
            user_id=UUID(user_id),
            name=name,
            nodes=[],
            edges=[],
            visibility=visibility,
        )
        session.add(flow)
        await session.commit()
        return flow.id


async def _run(
    flow_id: UUID, pod_id: str, user_id: str, *, ago: timedelta, run_status="COMPLETED"
) -> UUID:
    async with async_session_maker() as session:
        run = WorkflowRunModel(
            flow_id=flow_id,
            pod_id=UUID(pod_id),
            user_id=UUID(user_id),
            status=run_status,
            created_at=datetime.now(UTC) - ago,
        )
        session.add(run)
        await session.commit()
        return run.id


async def _form_wait(
    flow_id: UUID, run_id: UUID, pod_id: str, *, assignee: UUID, ago: timedelta
) -> UUID:
    async with async_session_maker() as session:
        wait = WorkflowRunWaitModel(
            run_id=run_id,
            flow_id=flow_id,
            pod_id=UUID(pod_id),
            node_id="approve",
            wait_type="HUMAN",
            status="ACTIVE",
            assigned_pod_member_id=assignee,
            payload={"input_schema": {"title": "Approve the refund"}},
            created_at=datetime.now(UTC) - ago,
        )
        session.add(wait)
        await session.commit()
        return wait.id


async def _schedule(
    pod_id: str,
    user_id: str,
    *,
    name: str,
    visibility: str = "POD",
    internal: bool = False,
) -> UUID:
    async with async_session_maker() as session:
        schedule = Schedule(
            pod_id=UUID(pod_id),
            user_id=UUID(user_id),
            name=name,
            schedule_type=ScheduleType.TIME,
            config={"cron": "0 9 * * *"},
            visibility=visibility,
            is_internal=internal,
        )
        session.add(schedule)
        await session.commit()
        return schedule.id


async def _fire(
    schedule_id: UUID,
    *,
    ago: timedelta,
    fire_status: str = "COMPLETED",
    target_run_id: UUID | None = None,
) -> UUID:
    async with async_session_maker() as session:
        fire = ScheduleRun(
            schedule_id=schedule_id,
            source_event_id=uuid4().hex,
            status=fire_status,
            target_kind="AGENT",
            target_run_id=str(target_run_id) if target_run_id else None,
            created_at=datetime.now(UTC) - ago,
        )
        session.add(fire)
        await session.commit()
        return fire.id


async def _app(
    pod_id: str, user_id: str, *, name: str, visibility: str, updated_ago: timedelta
) -> UUID:
    async with async_session_maker() as session:
        app = AppModel(
            pod_id=UUID(pod_id),
            user_id=UUID(user_id),
            name=name,
            public_slug=f"{name}-{uuid4().hex[:6]}",
            status="READY",
            visibility=visibility,
        )
        session.add(app)
        await session.flush()
        await session.execute(
            update(AppModel)
            .where(AppModel.id == app.id)
            .values(updated_at=datetime.now(UTC) - updated_ago)
        )
        await session.commit()
        return app.id


async def _contact_waiting(
    scenario: E2EScenario, db_session: AsyncSession, pod_id: str
) -> UUID:
    """A contact whose agents are off: open, nobody on it -- waiting."""
    source, _ = await contact_place(db_session, SimpleNamespace(id=pod_id))
    switched = await scenario.owner_client.patch(
        f"/pods/{pod_id}/channels/{source.channel_id}", json={"agents_answer": False}
    )
    assert switched.status_code == status.HTTP_200_OK, switched.text
    return source.channel_id


# -- Needs you ---------------------------------------------------------------------


async def test_needs_you_is_every_pods_waits_oldest_first(
    scenario: E2EScenario, db_session: AsyncSession
):
    first, second = await _org_with_pods(scenario, 2)
    owner = scenario.owner_user
    member = await _member(scenario, pods={first: "POD_USER"}, prefix="ov-needs")

    asked = await _conversation(scenario, first, title="Quarterly report")
    asked_call = await _pause(
        asked, tool_name="ask_user", tool_args=_QUESTION, ago=timedelta(hours=3)
    )
    # Two pauses in one conversation: one item, as old as the first.
    approving = await _conversation(scenario, second, title="Invoices")
    await _pause(
        approving,
        tool_name="request_approval",
        tool_args=_APPROVAL,
        ago=timedelta(hours=2),
    )
    await _pause(
        approving, tool_name="ask_user", tool_args=_QUESTION, ago=timedelta(minutes=30)
    )
    # Another member's pause, in a pod the owner can open: never the owner's.
    theirs = await _conversation(scenario, first, title="Mine", who=member)
    await _pause(
        theirs, tool_name="ask_user", tool_args=_QUESTION, ago=timedelta(days=1)
    )

    flow = await _workflow(second, owner["id"], name="refund-review")
    run = await _run(
        flow, second, owner["id"], ago=timedelta(hours=1), run_status="WAITING"
    )
    wait = await _form_wait(
        flow,
        run,
        second,
        assignee=await _pod_member_id(scenario, second, owner["id"]),
        ago=timedelta(hours=1),
    )
    channel = await _contact_waiting(scenario, db_session, first)

    url = f"/organizations/{scenario.org_id}/needs-you"
    listed = await _as(scenario, None)(url)
    assert listed.status_code == status.HTTP_200_OK, listed.text
    body = listed.json()
    assert [item["key"] for item in body["items"]] == [
        f"conversation:{asked}",
        f"conversation:{approving}",
        f"wait:{wait}",
        f"contact:{channel}",
    ]
    assert body["truncated"] is False
    question, approval, form, contact = body["items"]
    assert question["kind"] == "QUESTION" and question["pod_id"] == first
    assert question["title"] == "Which region should it cover?"
    assert question["detail"] == "Quarterly report"
    assert question["tool_call_id"] == asked_call
    assert approval["kind"] == "APPROVAL"
    assert approval["title"] == "Delete the draft invoices"
    assert form["kind"] == "FORM" and form["workflow_run_id"] == str(run)
    assert (form["title"], form["detail"]) == ("Approve the refund", "refund-review")
    assert form["conversation_id"] is None
    assert contact["kind"] == "CONTACT" and contact["title"] == "Dana"
    assert contact["channel_id"] == str(channel)
    named = await scenario.owner_client.get(f"/pods/{first}")
    assert question["pod_name"] == named.json()["name"]

    capped = (await _as(scenario, None)(url, params={"limit": 2})).json()
    assert [item["key"] for item in capped["items"]] == [
        f"conversation:{asked}",
        f"conversation:{approving}",
    ]
    assert capped["truncated"] is True

    # The member sees their own pause, and no contacts: a pod user does not
    # hold `contact.read`.
    as_member = (await _as(scenario, member)(url)).json()
    assert [item["key"] for item in as_member["items"]] == [f"conversation:{theirs}"]


async def test_every_read_refuses_somebody_outside_the_organization(
    scenario: E2EScenario,
):
    await _org_with_pods(scenario, 1)
    stranger = await scenario.create_user("ov-stranger")
    for url in ("needs-you", "recents", "apps", "home/seen"):
        refused = await _as(scenario, stranger)(
            f"/organizations/{scenario.org_id}/{url}"
        )
        assert refused.status_code == status.HTTP_403_FORBIDDEN, (url, refused.text)


# -- Recents -----------------------------------------------------------------------


async def test_recents_are_each_pods_chats_automations_and_days(
    scenario: E2EScenario,
):
    busy, quiet = await _org_with_pods(scenario, 2)
    owner = scenario.owner_user
    member = await _member(scenario, pods={busy: "POD_EDITOR"}, prefix="ov-recents")
    now = datetime.now(UTC)

    #: When each of the owner's conversations was last active, and whether an
    #: agent started it: what the day counts must add up to.
    worked: list[tuple[datetime, bool]] = []

    async def conversation(title: str, at: datetime, *, by_agent=False, **made):
        made_id = await _conversation(scenario, busy, title=title, **made)
        await _touch(made_id, last_activity_at=at)
        worked.append((at, by_agent))
        return made_id

    started = {"kind": "TASK", "metadata": {"source": "SCHEDULE"}}
    older_chat = await conversation("Pricing", now - timedelta(days=2))
    newer_chat = await conversation("Launch", now)
    scheduled = await conversation("Digest", now, by_agent=True, **started)
    asking = await conversation(
        "Digest asks", now - timedelta(seconds=30), by_agent=True, **started
    )
    await _pause(asking, tool_name="ask_user", tool_args=_QUESTION, ago=timedelta(0))
    # A run starting is activity; put it back where the scene wants it.
    await _touch(asking, last_activity_at=now - timedelta(seconds=30))
    archived = await conversation("Old thread", now)
    await _touch(archived, is_archived=True)
    await conversation("Plan's chat", now, metadata={"lemma_resource": "file:/plan.md"})
    theirs = await _conversation(scenario, busy, title="Not yours", who=member)

    flow = await _workflow(busy, owner["id"], name="triage")
    await _run(flow, busy, owner["id"], ago=timedelta(days=20))
    await _run(flow, busy, owner["id"], ago=timedelta(days=3))
    latest_run = await _run(
        flow, busy, member["id"], ago=timedelta(hours=2), run_status="RUNNING"
    )
    private = await _workflow(busy, member["id"], name="theirs", visibility="PERSONAL")
    await _run(private, busy, member["id"], ago=timedelta(hours=1))

    schedule = await _schedule(busy, owner["id"], name="morning-digest")
    await _fire(schedule, ago=timedelta(days=1))
    await _fire(schedule, ago=timedelta(minutes=10), fire_status="FILTERED")
    latest_fire = await _fire(
        schedule, ago=timedelta(minutes=30), target_run_id=scheduled
    )
    machinery = await _schedule(busy, owner["id"], name="wait-timer", internal=True)
    await _fire(machinery, ago=timedelta(minutes=5))

    url = f"/organizations/{scenario.org_id}/recents"
    read = await _as(scenario, None)(url, params={"days": 14, "timezone": "UTC"})
    assert read.status_code == status.HTTP_200_OK, read.text
    body = read.json()
    assert (body["days"], body["timezone"]) == (14, "UTC")
    pods = {pod["pod_id"]: pod for pod in body["pods"]}
    assert set(pods) == {busy, quiet}, "quiet pods are listed too"

    chats = pods[busy]["chats"]
    assert [chat["conversation_id"] for chat in chats] == [
        str(newer_chat),
        str(asking),
        str(older_chat),
    ]
    waiting = next(chat for chat in chats if chat["conversation_id"] == str(asking))
    assert (waiting["origin"], waiting["waiting_on_you"]) == ("schedule", True)
    assert str(theirs) not in {chat["conversation_id"] for chat in chats}

    automations = {
        (each["kind"], each["id"]): each for each in pods[busy]["automations"]
    }
    assert set(automations) == {("WORKFLOW", str(flow)), ("SCHEDULE", str(schedule))}
    triage = automations[("WORKFLOW", str(flow))]
    assert (triage["run_count"], triage["latest_run_id"]) == (2, str(latest_run))
    assert (triage["latest_status"], triage["workflow_run_id"]) == (
        "RUNNING",
        str(latest_run),
    )
    digest = automations[("SCHEDULE", str(schedule))]
    assert (digest["run_count"], digest["latest_run_id"]) == (2, str(latest_fire))
    assert digest["conversation_id"] == str(scheduled)
    assert [each["kind"] for each in pods[busy]["automations"]] == [
        "SCHEDULE",
        "WORKFLOW",
    ], "the most recent first"

    activity = pods[busy]["activity"]
    assert len(activity) == 14
    # Archived and document chats count too: both were work that happened.
    expected: dict[str, list[int]] = {}
    for at, by_agent in worked:
        counts = expected.setdefault(at.date().isoformat(), [0, 0])
        counts[0 if by_agent else 1] += 1
    counted = {
        day["day"]: [day["by_agent"], day["by_people"]]
        for day in activity
        if day["by_agent"] or day["by_people"]
    }
    assert counted == expected
    assert sum(by_agent for _, by_agent in worked) == 2

    assert pods[quiet]["chats"] == [] and pods[quiet]["automations"] == []
    assert [day["by_agent"] + day["by_people"] for day in pods[quiet]["activity"]] == [
        0
    ] * 14

    one = (await _as(scenario, None)(url, params={"chats": 1})).json()
    [only] = next(pod for pod in one["pods"] if pod["pod_id"] == busy)["chats"]
    assert only["conversation_id"] == str(newer_chat)

    # The member may read the shared workflow and their own private one.
    as_member = (await _as(scenario, member)(url)).json()
    [their_pod] = as_member["pods"]
    assert {each["name"] for each in their_pod["automations"]} == {
        "triage",
        "theirs",
        "morning-digest",
    }
    assert [chat["conversation_id"] for chat in their_pod["chats"]] == [str(theirs)]


async def test_recents_refuse_a_zone_nobody_knows(scenario: E2EScenario):
    await _org_with_pods(scenario, 1)
    refused = await _as(scenario, None)(
        f"/organizations/{scenario.org_id}/recents", params={"timezone": "Mars/Olympus"}
    )
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, refused.text


# -- Apps --------------------------------------------------------------------------


async def test_apps_are_every_pods_newest_first_and_only_what_you_may_open(
    scenario: E2EScenario,
):
    first, second = await _org_with_pods(scenario, 2)
    owner = scenario.owner_user
    member = await _member(scenario, pods={first: "POD_USER"}, prefix="ov-apps")

    older = await _app(
        first,
        owner["id"],
        name="tracker",
        visibility="POD",
        updated_ago=timedelta(days=2),
    )
    newer = await _app(
        second, owner["id"], name="board", visibility="PUBLIC", updated_ago=timedelta(0)
    )
    personal = await _app(
        first,
        member["id"],
        name="scratch",
        visibility="PERSONAL",
        updated_ago=timedelta(hours=1),
    )

    url = f"/organizations/{scenario.org_id}/apps"
    as_owner = await _as(scenario, None)(url)
    assert as_owner.status_code == status.HTTP_200_OK, as_owner.text
    assert [app["id"] for app in as_owner.json()["items"]] == [str(newer), str(older)]
    board = as_owner.json()["items"][0]
    assert (board["pod_id"], board["visibility"], board["status"]) == (
        second,
        "PUBLIC",
        "READY",
    )

    # Their own personal app, and the pod's -- nothing from a pod they are not in.
    as_member = (await _as(scenario, member)(url)).json()
    assert [app["id"] for app in as_member["items"]] == [str(personal), str(older)]


# -- Home seen ---------------------------------------------------------------------


async def test_home_seen_is_each_members_own_and_only_moves_forward(
    scenario: E2EScenario,
):
    (pod_id,) = await _org_with_pods(scenario, 1)
    member = await _member(scenario, pods={pod_id: "POD_USER"}, prefix="ov-seen")
    url = f"/organizations/{scenario.org_id}/home/seen"
    owner = scenario.owner_client

    assert (await owner.get(url)).json() == {"seen_at": None, "previous_seen_at": None}

    loaded = datetime.now(UTC) - timedelta(minutes=5)
    marked = await owner.put(url, json={"seen_at": loaded.isoformat()})
    assert marked.status_code == status.HTTP_200_OK, marked.text
    assert marked.json()["previous_seen_at"] is None
    assert datetime.fromisoformat(marked.json()["seen_at"]) == loaded

    # An older tab cannot move it back.
    stale = await owner.put(
        url, json={"seen_at": (loaded - timedelta(days=1)).isoformat()}
    )
    assert datetime.fromisoformat(stale.json()["seen_at"]) == loaded

    # Nothing is seen in the future; omitted is now.
    ahead = await owner.put(
        url, json={"seen_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}
    )
    assert datetime.fromisoformat(ahead.json()["seen_at"]) <= datetime.now(UTC)
    assert datetime.fromisoformat(ahead.json()["previous_seen_at"]) == loaded
    assert (await owner.put(url)).status_code == status.HTTP_200_OK

    naive = await owner.put(url, json={"seen_at": "2026-01-01T00:00:00"})
    assert naive.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    # Theirs alone.
    assert (await _as(scenario, member)(url)).json()["seen_at"] is None


# -- the point of all four ---------------------------------------------------------


async def _furnish(scenario: E2EScenario, pod_id: str) -> None:
    """Something in a pod for every read to find."""
    owner = scenario.owner_user
    chat = await _conversation(scenario, pod_id, title="Chat")
    await _pause(chat, tool_name="ask_user", tool_args=_QUESTION, ago=timedelta(0))
    flow = await _workflow(pod_id, owner["id"], name=f"flow-{uuid4().hex[:6]}")
    await _run(flow, pod_id, owner["id"], ago=timedelta(hours=1))
    schedule = await _schedule(pod_id, owner["id"], name=f"every-{uuid4().hex[:6]}")
    await _fire(schedule, ago=timedelta(hours=1))
    await _app(
        pod_id, owner["id"], name="app", visibility="POD", updated_ago=timedelta(0)
    )


async def _counted(scenario: E2EScenario, path: str) -> list[str]:
    url = f"/organizations/{scenario.org_id}/{path}"
    # Warm whatever a first request of a session pays once (the caller's user).
    assert (await scenario.owner_client.get(url)).status_code == status.HTTP_200_OK
    with counted_queries() as statements:
        answer = await scenario.owner_client.get(url)
    assert answer.status_code == status.HTTP_200_OK, answer.text
    return statements


@pytest.mark.parametrize("path", ["needs-you", "recents", "apps"])
async def test_a_read_costs_the_same_for_one_pod_as_for_five(
    scenario: E2EScenario, path: str
):
    """Each endpoint replaces a request per pod, so a query per pod would undo
    it: five pods must cost what one does."""
    (only,) = await _org_with_pods(scenario, 1)
    await _furnish(scenario, only)
    small = await _counted(scenario, path)

    for pod_id in await _org_with_pods(scenario, 5):
        await _furnish(scenario, pod_id)
    large = await _counted(scenario, path)

    assert len(large) == len(small), (
        f"{path}: 5 pods issued {len(large)} queries where 1 issued {len(small)}:\n"
        + "\n".join(f"  {statement[:120]}" for statement in large)
    )
