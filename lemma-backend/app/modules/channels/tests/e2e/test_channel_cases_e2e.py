"""E2E for a contact's case: the pod answering them, as it stands now.

A contact's channel is made with its case and keeps it; a member writing
steps in; closing ends its wait, and the contact writing again -- or a member
setting it by hand -- opens the same row, still held by the member who
answered. Only a contact's channel has a case -- the database holds it to
that -- and a fold takes the merged channel's case into the survivor's.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import NewLine, Sender, call_for_person, post
from app.modules.channels.infrastructure.models import ChannelCaseModel, ChannelModel
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


async def test_a_contacts_case_is_one_row_that_closes_and_opens_again(
    pod: Pod, db_session: AsyncSession
):
    source, contact_id = await contact_place(db_session, pod)
    channel = f"{pod.base}/{source.channel_id}"
    editor = pod.client(pod.editor)
    [case] = await _cases(db_session, source.channel_id)
    assert (case.status, case.assignee_user_id) == ("open", None)

    await say(pod, source.channel_id, "On it", as_user=pod.editor)
    shown = (await editor.get(channel)).json()
    assert shown["status"] == "waiting_on_contact"
    assert shown["assignee"]["id"] == pod.editor["id"]
    uow = SqlAlchemyUnitOfWork(db_session)
    await call_for_person(uow, channel_id=source.channel_id, reason="Refund?")
    await db_session.commit()

    closed = await editor.patch(channel, json={"status": "closed"})
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    # Closed, its wait is over: it asks for nobody.
    assert closed.json()["attention"] is None
    [closed_case] = await _cases(db_session, source.channel_id)
    assert closed_case.id == case.id and closed_case.closed_at is not None

    # The contact writes again: the same case opens, still the editor's.
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
    assert shown["case_id"] == str(case.id)
    [opened] = await _cases(db_session, source.channel_id)
    assert opened.id == case.id and opened.closed_at is None
    channel_row = await db_session.get(ChannelModel, source.channel_id)
    assert channel_row is not None and channel_row.current_case_id == case.id

    # Set by hand after closing, it is the same case again.
    await editor.patch(channel, json={"status": "closed"})
    reopened = await editor.patch(channel, json={"status": "waiting_on_contact"})
    assert reopened.json()["status"] == "waiting_on_contact"
    [by_hand] = await _cases(db_session, source.channel_id)
    assert by_hand.id == case.id and by_hand.closed_at is None


async def test_a_case_closed_before_it_was_kept_opens_asking_for_nobody(
    pod: Pod, db_session: AsyncSession
):
    """A case closed the old way kept its call for a person and its
    acknowledgement; opened again by the contact's next line, it holds
    neither, and nobody holding it, it is not waiting on a person."""
    source, contact_id = await contact_place(db_session, pod)
    await db_session.execute(
        text(
            "UPDATE channel_cases SET status = 'closed', closed_at = now(),"
            " attention_at = now(), attention_reason = 'Refund?',"
            " acknowledged_at = now() WHERE channel_id = :channel"
        ),
        {"channel": source.channel_id},
    )
    await db_session.commit()

    uow = SqlAlchemyUnitOfWork(db_session)
    await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=Sender.contact(contact_id), text="Back again", source_id=source.id
        ),
    )
    await db_session.commit()

    [case] = await _cases(db_session, source.channel_id)
    assert (case.status, case.closed_at) == ("open", None)
    assert (case.attention_at, case.attention_reason) == (None, None)
    assert case.acknowledged_at is None
    waiting = await pod.client(pod.editor).get(pod.base, params={"needs_person": True})
    assert waiting.status_code == 200, waiting.text
    assert str(source.channel_id) not in {
        each["id"] for each in waiting.json()["items"]
    }


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


async def test_a_fold_takes_the_merged_case_into_the_survivors_one(
    scenario, db_session: AsyncSession
):
    """The survivor keeps its one case, with the merged one's assignee (the
    more recently assigned) and its own call for a person; the merged case
    goes with its channel."""
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
    ours_id = ours.id
    [theirs] = await _cases(db_session, folded.channel_id)
    theirs_id = theirs.id

    await _fold(uow, pod_id, merged=merged, survivor=survivor)
    await db_session.commit()

    [current] = await _cases(db_session, kept.channel_id)
    assert current.id == ours_id
    head = await db_session.get(ChannelModel, kept.channel_id)
    assert head is not None and head.current_case_id == ours_id
    assert current.closed_at is None
    assert current.assignee_user_id == owner
    assert current.attention_reason == "Refund?"
    assert await db_session.get(ChannelCaseModel, theirs_id) is None


async def test_a_fold_into_a_channel_without_a_case_moves_the_merged_one(
    scenario, db_session: AsyncSession
):
    await scenario.create_org_with_pod(name_prefix="Channel fold no case")
    pod_id = UUID(scenario.pod_id)
    owner = UUID(scenario.owner_user["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    kept = await _contact(uow, pod_id, "5554001")
    folded = await _contact(uow, pod_id, "5554002")
    survivor = await _contact_of(db_session, kept)
    merged = await _contact_of(db_session, folded)
    await post(
        uow,
        channel_id=folded.channel_id,
        line=NewLine(sender=Sender.member(owner), text="Let me check"),
    )
    await db_session.execute(
        text("DELETE FROM channel_cases WHERE channel_id = :kept"),
        {"kept": kept.channel_id},
    )
    await _merge(db_session, merged=merged, into=survivor)
    await db_session.commit()
    [theirs] = await _cases(db_session, folded.channel_id)
    theirs_id = theirs.id

    await _fold(uow, pod_id, merged=merged, survivor=survivor)
    await db_session.commit()

    [current] = await _cases(db_session, kept.channel_id)
    assert current.id == theirs_id and current.assignee_user_id == owner
    head = await db_session.get(ChannelModel, kept.channel_id)
    assert head is not None and head.current_case_id == theirs_id


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
