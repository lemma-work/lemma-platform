"""E2E for a contact's cases: one episode of the pod answering them at a time.

A contact's channel is made with its first case; a member writing steps in
and is its first response; closing ends it, and the contact writing again
opens the next, still held by the member who answered; a member reopening a
closed case by hand takes that case up again. Every change is recorded. Only
a contact's channel has cases -- the database holds it to that -- and a fold
keeps both channels' cases, merging two open ones by a fixed rule.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import NewLine, Sender, call_for_person, post
from app.modules.channels.infrastructure.models import (
    ChannelCaseEventModel,
    ChannelCaseModel,
    ChannelMessageModel,
    ChannelModel,
)
from app.modules.channels.tests.e2e.channel_pod import (
    Pod,
    contact_place,
    group,
    say,
)
from app.modules.channels.tests.e2e.channel_scale import shape_of
from app.modules.channels.tests.e2e.test_channel_fold_e2e import (
    _contact,
    _contact_of,
    _fold,
    _merge,
)

pytestmark = pytest.mark.e2e


async def _cases(session: AsyncSession, channel_id: UUID) -> list[ChannelCaseModel]:
    session.expire_all()
    return list(
        await session.scalars(
            select(ChannelCaseModel)
            .where(ChannelCaseModel.channel_id == channel_id)
            .order_by(ChannelCaseModel.opened_at)
        )
    )


async def _events(session: AsyncSession, case_id: UUID) -> list[str]:
    return list(
        await session.scalars(
            select(ChannelCaseEventModel.kind)
            .where(ChannelCaseEventModel.case_id == case_id)
            .order_by(ChannelCaseEventModel.id)
        )
    )


async def test_a_contacts_case_runs_from_open_to_closed_and_the_next_opens(
    pod: Pod, db_session: AsyncSession
):
    source, contact_id = await contact_place(db_session, pod)
    channel = f"{pod.base}/{source.channel_id}"
    editor = pod.client(pod.editor)
    [first] = await _cases(db_session, source.channel_id)
    assert (first.status, first.assignee_user_id, first.opened_seq) == ("open", None, 0)

    answer = await say(pod, source.channel_id, "On it", as_user=pod.editor)
    shown = (await editor.get(channel)).json()
    assert shown["status"] == "waiting_on_contact"
    assert shown["assignee"]["id"] == pod.editor["id"]
    [first] = await _cases(db_session, source.channel_id)
    answered_at = await db_session.scalar(
        select(ChannelMessageModel.created_at).where(
            ChannelMessageModel.id == UUID(answer["id"])
        )
    )
    # The first member line is the case's first response.
    assert first.first_response_by_user_id == UUID(pod.editor["id"])
    assert first.first_response_at == answered_at

    closed = await editor.patch(channel, json={"status": "closed"})
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    [first] = await _cases(db_session, source.channel_id)
    assert first.closed_at is not None
    assert first.closed_by_user_id == UUID(pod.editor["id"])
    assert await _events(db_session, first.id) == [
        "opened",
        "assigned",
        "first_response",
        "status",
        "closed",
    ]

    # The contact writes again: a new case, still the editor's to answer.
    uow = SqlAlchemyUnitOfWork(db_session)
    again = await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=Sender.contact(contact_id),
            text="One more thing",
            source_id=source.id,
        ),
    )
    await db_session.commit()
    assert again is not None
    shown = (await editor.get(channel)).json()
    assert shown["status"] == "open"
    assert shown["assignee"]["id"] == pod.editor["id"]
    first, second = await _cases(db_session, source.channel_id)
    assert first.closed_at is not None and second.closed_at is None
    assert second.opened_seq == again.seq - 1
    assert second.first_response_at is None
    channel_row = await db_session.get(ChannelModel, source.channel_id)
    assert channel_row is not None and channel_row.current_case_id == second.id

    # Reopened by hand, a closed case is taken up again: no third case.
    await editor.patch(channel, json={"status": "closed"})
    reopened = await editor.patch(channel, json={"status": "waiting_on_contact"})
    assert reopened.json()["status"] == "waiting_on_contact"
    cases = await _cases(db_session, source.channel_id)
    assert [case.id for case in cases] == [first.id, second.id]
    assert (await _events(db_session, second.id))[-3:] == [
        "closed",
        "reopened",
        "status",
    ]


async def test_only_a_contacts_channel_has_a_case(pod: Pod, db_session: AsyncSession):
    made = await group(pod)
    uow = SqlAlchemyUnitOfWork(db_session)
    # A run asking for a person in a group flags nothing there: it has no case.
    await call_for_person(uow, channel_id=UUID(made["id"]), reason="Order 1182")
    await db_session.commit()
    assert await _cases(db_session, UUID(made["id"])) == []
    shown = (await pod.client().get(f"{pod.base}/{made['id']}")).json()
    assert shown["attention"] is None and shown["status"] is None

    def a_case_for(channel_id: str) -> object:
        return db_session.execute(
            text(
                "INSERT INTO channel_cases (id, channel_id, pod_id, opened_at,"
                " opened_seq, created_at, updated_at)"
                " VALUES (:id, :channel, :pod, now(), 0, now(), now())"
            ),
            {"id": uuid4(), "channel": channel_id, "pod": pod.id},
        )

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await a_case_for(made["id"])
    source, _ = await contact_place(db_session, pod)
    # One open case a channel.
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await a_case_for(str(source.channel_id))


async def test_a_fold_keeps_both_channels_cases_and_merges_two_open_ones(
    scenario, db_session: AsyncSession
):
    """The survivor's open case goes on with the merged one's assignee (the
    more recently assigned), its own call for a person, and the earlier first
    response; the merged case is closed into it and kept."""
    await scenario.create_org_with_pod(name_prefix="Channel fold cases")
    pod_id = UUID(scenario.pod_id)
    owner = UUID(scenario.owner_user["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    kept = await _contact(uow, pod_id, "5553001")
    folded = await _contact(uow, pod_id, "5553002")
    survivor = await _contact_of(db_session, kept)
    merged = await _contact_of(db_session, folded)
    await post(
        uow,
        channel_id=folded.channel_id,
        line=NewLine(sender=Sender.member(owner), text="Let me check"),
    )
    await call_for_person(uow, channel_id=kept.channel_id, reason="Refund?")
    await _merge(db_session, merged=merged, into=survivor)
    await db_session.commit()
    [ours] = await _cases(db_session, kept.channel_id)
    [theirs] = await _cases(db_session, folded.channel_id)

    await _fold(uow, pod_id, merged=merged, survivor=survivor)
    await db_session.commit()

    cases = {case.id: case for case in await _cases(db_session, kept.channel_id)}
    assert set(cases) == {ours.id, theirs.id}
    head = await db_session.get(ChannelModel, kept.channel_id)
    assert head is not None and head.current_case_id == ours.id
    current, closed = cases[ours.id], cases[theirs.id]
    assert current.closed_at is None and closed.closed_at is not None
    assert current.assignee_user_id == owner
    assert current.attention_reason == "Refund?"
    assert current.first_response_by_user_id == owner
    assert (await _events(db_session, current.id))[-1] == "merged"
    assert (await _events(db_session, closed.id))[-1] == "closed"


# -- contacts waiting on a person, in a pod of thousands ---------------------------

#: The pod's contacts, and every other pod's.
POD_CONTACTS = 3000
OTHER_PODS = 20
OTHER_CONTACTS = 500


async def _seed_contacts(session: AsyncSession, *, pod_id: UUID, editor: UUID) -> int:
    """Contacts' channels, each with its current case -- most answered by the
    agent, some assigned, some closed, one in seventy-five asking for a
    person, one in a hundred and fifty with its agent off -- and every third
    with a closed case before it; in this pod and in many others. How many of
    this pod's wait on a person.

    Fabricated pods and contacts: foreign keys are not under test, and
    ``truncate_all`` empties these tables before the next test.
    """
    await session.execute(text("SET LOCAL session_replication_role = 'replica'"))
    await session.execute(
        text(f"""
        CREATE TEMP TABLE seed_contacts ON COMMIT DROP AS
        SELECT CAST(:pod AS uuid) AS pod_id, n, n % 75 = 1 AS asks,
               n % 150 = 3 AS agents_off, n % 100 = 9 AS assigned,
               n % 100 = 7 AS closed
        FROM generate_series(1, {POD_CONTACTS}) n
        UNION ALL
        SELECT md5('platform:pod:' || p)::uuid, n, n % 75 = 1, n % 150 = 3,
               n % 100 = 9, n % 100 = 7
        FROM generate_series(1, {OTHER_PODS}) p,
             generate_series(1, {OTHER_CONTACTS}) n
        """),
        {"pod": pod_id},
    )
    await session.execute(
        text("""
        INSERT INTO contacts (id, pod_id, display_name, created_at, updated_at)
        SELECT md5(pod_id || ':contact:' || n)::uuid, pod_id, 'Contact ' || n,
               now(), now()
        FROM seed_contacts
        """)
    )
    await session.execute(
        text("""
        INSERT INTO channels
          (id, pod_id, kind, visibility, contact_id, agents_answer, current_case_id,
           last_seq, last_line_at, last_rev, changed_rev, created_at, updated_at)
        SELECT md5(pod_id || ':channel:' || n)::uuid, pod_id, 'CONTACT', 'PRIVATE',
               md5(pod_id || ':contact:' || n)::uuid, NOT agents_off,
               md5(pod_id || ':case:' || n)::uuid, 2,
               now() - n * interval '1 minute', 2, 0, now(), now()
        FROM seed_contacts
        """)
    )
    await session.execute(
        text("""
        INSERT INTO channel_cases
          (id, channel_id, pod_id, opened_at, opened_seq, status, assignee_user_id,
           assigned_at, attention_at, attention_reason, closed_at, created_at,
           updated_at)
        SELECT md5(pod_id || ':case:' || n)::uuid,
               md5(pod_id || ':channel:' || n)::uuid, pod_id,
               now() - interval '1 day', 1,
               CASE WHEN closed THEN 'closed' ELSE 'open' END,
               CASE WHEN assigned THEN CAST(:editor AS uuid) END,
               CASE WHEN assigned THEN now() END,
               CASE WHEN asks THEN now() END, CASE WHEN asks THEN 'Refund?' END,
               CASE WHEN closed THEN now() END, now(), now()
        FROM seed_contacts
        UNION ALL
        SELECT md5(pod_id || ':before:' || n)::uuid,
               md5(pod_id || ':channel:' || n)::uuid, pod_id,
               now() - interval '1 week', 0, 'closed', NULL, NULL,
               CASE WHEN n % 2 = 0 THEN now() - interval '1 week' END, NULL,
               now() - interval '6 days', now(), now()
        FROM seed_contacts WHERE n % 3 = 0
        """),
        {"editor": editor},
    )
    waiting = await session.scalar(
        text(
            "SELECT count(*) FROM seed_contacts WHERE pod_id = :pod"
            " AND NOT assigned AND NOT closed AND (asks OR agents_off)"
        ),
        {"pod": pod_id},
    )
    await session.commit()
    for table in ("contacts", "channels", "channel_cases"):
        await session.execute(text(f"ANALYZE {table}"))
    await session.commit()
    return int(waiting or 0)


async def test_contacts_waiting_on_a_person_are_found_by_their_cases(
    pod: Pod, db_session: AsyncSession, db_manager
):
    """``needs_person`` reads the few cases that ask for a person and the few
    channels whose agent is off -- never every contact of the pod -- and
    still lists them most recently active first."""
    waiting = await _seed_contacts(
        db_session, pod_id=UUID(pod.id), editor=UUID(pod.editor["id"])
    )
    editor = pod.client(pod.editor)

    async def read() -> dict:
        listed = await editor.get(pod.base, params={"needs_person": True, "limit": 100})
        assert listed.status_code == 200, listed.text
        return listed.json()

    page = await read()
    assert len(page["items"]) == waiting
    times = [item["last_line_at"] for item in page["items"]]
    assert times == sorted(times, reverse=True)
    assert all(
        item["assignee"] is None and item["status"] == "open" for item in page["items"]
    )

    shape = await shape_of(db_manager.engine, read)
    whole = [
        f"{node['Node Type']} on {node['Relation Name']}"
        for plan in shape.plans
        for node in plan.nodes()
        if node.get("Node Type") == "Seq Scan"
        and node.get("Relation Name") in ("channels", "channel_cases")
    ]
    assert not whole, f"needs_person scans {whole}:\n{shape.describe(verbose=True)}"
    for relation in ("channels", "channel_cases"):
        read_rows = shape.rows_read(relation)
        assert read_rows <= 4 * waiting, (
            f"needs_person read {read_rows} rows of {relation} for {waiting} "
            f"waiting:\n{shape.describe(verbose=True)}"
        )


async def test_a_contacts_cases_are_read_back_newest_first_with_their_events(
    pod: Pod, db_session: AsyncSession
):
    source, contact_id = await contact_place(db_session, pod)
    cases = f"{pod.base}/{source.channel_id}/cases"
    editor = pod.client(pod.editor)
    await say(pod, source.channel_id, "On it", as_user=pod.editor)
    await editor.patch(f"{pod.base}/{source.channel_id}", json={"status": "closed"})
    await post(
        SqlAlchemyUnitOfWork(db_session),
        channel_id=source.channel_id,
        line=NewLine(sender=Sender.contact(contact_id), text="Me again"),
    )
    await db_session.commit()

    listed = await editor.get(cases)
    assert listed.status_code == 200, listed.text
    newest, oldest = listed.json()["items"]
    assert (newest["status"], newest["closed_at"]) == ("open", None)
    assert newest["assignee"]["id"] == pod.editor["id"]
    assert oldest["status"] == "closed"
    assert oldest["first_response_by"]["id"] == pod.editor["id"]
    assert oldest["first_response_at"] is not None
    assert oldest["closed_by"]["id"] == pod.editor["id"]
    assert [event["kind"] for event in oldest["events"]] == [
        "opened",
        "assigned",
        "first_response",
        "status",
        "closed",
    ]
    assert oldest["events"][-1]["actor"]["id"] == pod.editor["id"]

    first = await editor.get(cases, params={"limit": 1})
    assert [case["id"] for case in first.json()["items"]] == [newest["id"]]
    rest = await editor.get(
        cases, params={"limit": 1, "cursor": first.json()["next_cursor"]}
    )
    assert [case["id"] for case in rest.json()["items"]] == [oldest["id"]]

    # A member who may not see contacts finds no such channel; a group has
    # no cases.
    assert (await pod.client(pod.user).get(cases)).status_code == 404
    made = await group(pod)
    assert (await pod.client().get(f"{pod.base}/{made['id']}/cases")).status_code == 409


async def test_a_contacts_channel_names_its_current_case_wherever_it_is_listed(
    pod: Pod, db_session: AsyncSession
):
    source, _ = await contact_place(db_session, pod)
    [case] = await _cases(db_session, source.channel_id)
    editor = pod.client(pod.editor)

    listed = (await editor.get(pod.base, params={"kind": "CONTACT"})).json()
    [row] = [item for item in listed["items"] if item["id"] == str(source.channel_id)]
    assert row["case_id"] == str(case.id), row
    shown = (await editor.get(f"{pod.base}/{source.channel_id}")).json()
    assert shown["case_id"] == str(case.id), shown
    # No other kind of channel has one.
    crew = await group(pod, title="crew")
    assert (await editor.get(f"{pod.base}/{crew['id']}")).json()["case_id"] is None
