"""Reporting what was said, and blocking a person, end to end.

A report keeps who reported what, why, and a copy of it, and is the
reporter's to replace; it refuses one's own line. A block is the blocker's
alone: the blocked person's lines come to them marked and empty, and to
everybody else as before. Blocking a contact for the pod stops its agents
writing to them, and leaves members free to.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import select, text

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.conversation import (
    ConversationModel,
    MessageModel,
)
from app.modules.channels.domain.entities import Principal
from app.modules.channels.infrastructure.blocks import BlockRepository
from app.modules.channels.tests.e2e.channel_pod import Pod, contact_place, group, say
from app.modules.contacts.contracts import IdentityStrength, open_contact
from app.modules.contacts.contracts.outreach import (
    Address,
    OutreachKind,
    RefusalCode,
    may_send,
)
from app.modules.contacts.domain.kinds import IdentityKind
from app.modules.reports.config import reports_settings
from app.modules.reports.infrastructure.models import ContentReportModel

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _lines(pod: Pod, channel_id, *, as_user=None) -> list[dict]:
    read = await pod.client(as_user).get(f"{pod.base}/{channel_id}/messages")
    assert read.status_code == 200, read.text
    return read.json()["items"]


async def test_a_report_keeps_a_copy_and_is_the_reporters_to_replace(
    pod: Pod, db_session, monkeypatch
):
    monkeypatch.setattr(reports_settings, "reports_email", "team@reports.example")
    source, dana = await contact_place(db_session, pod)
    (line,) = await _lines(pod, source.channel_id)
    url = f"{pod.base}/{source.channel_id}/messages/{line['id']}/report"

    first = await pod.client().post(url, json={"reason": "SPAM"})
    assert first.status_code == 201, first.text
    again = await pod.client().post(
        url, json={"reason": "OFFENSIVE", "note": "  Rude to the team.  "}
    )
    assert again.status_code == 201, again.text
    assert again.json()["id"] == first.json()["id"]

    row = await db_session.scalar(
        select(ContentReportModel).where(
            ContentReportModel.id == UUID(first.json()["id"])
        )
    )
    assert row is not None
    assert (row.place, row.author_kind, row.author_id) == ("CHANNEL", "CONTACT", dana)
    assert (row.reason, row.note, row.status) == (
        "OFFENSIVE",
        "Rude to the team.",
        "OPEN",
    )
    assert row.excerpt[-1]["text"] == "Where is my order?"
    assert row.excerpt[-1]["reported"] is True
    # Told to the team once, however often it was replaced while queued.
    queued = await db_session.scalar(
        text(
            "SELECT count(*) FROM work_items WHERE kind = 'reports.email' "
            "AND dedup_key = :key"
        ),
        {"key": f"content-report:{row.id}"},
    )
    assert queued == 1


async def test_ones_own_line_and_a_line_out_of_reach_cannot_be_reported(pod: Pod):
    channel = await group(pod, members=[pod.editor["id"]])
    mine = await say(pod, channel["id"], "my own words")
    own = await pod.client().post(
        f"{pod.base}/{channel['id']}/messages/{mine['id']}/report",
        json={"reason": "OTHER"},
    )
    assert own.status_code == 422, own.text
    unknown = await pod.client().post(
        f"{pod.base}/{channel['id']}/messages/{channel['id']}/report",
        json={"reason": "OTHER"},
    )
    assert unknown.status_code == 404, unknown.text
    # The editor reports the owner's line: anybody's but their own.
    theirs = await pod.client(pod.editor).post(
        f"{pod.base}/{channel['id']}/messages/{mine['id']}/report",
        json={"reason": "OTHER", "note": "x" * 1001},
    )
    assert theirs.status_code == 422, theirs.text


async def test_a_block_hides_their_lines_from_the_blocker_alone(pod: Pod, db_session):
    source, dana = await contact_place(db_session, pod)
    person = f"/pods/{pod.id}/blocks/CONTACT:{dana}"

    blocked = await pod.client().put(person)
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["person"] == {
        "kind": "CONTACT",
        "id": str(dana),
        "name": "Dana",
    }
    assert (await pod.client().put(person)).status_code == 200

    (mine,) = await _lines(pod, source.channel_id)
    (theirs,) = await _lines(pod, source.channel_id, as_user=pod.editor)
    assert mine["blocked"] and mine["text"] is None and not mine["withheld"]
    assert theirs["text"] == "Where is my order?" and not theirs["blocked"]

    rows = await pod.client().get(f"{pod.base}?kind=CONTACT")
    shown = {row["id"]: row for row in rows.json()["items"]}
    assert shown[str(source.channel_id)]["last_line"]["blocked"] is True

    listed = await pod.client().get(f"/pods/{pod.id}/blocks")
    assert [item["person"]["id"] for item in listed.json()["items"]] == [str(dana)]

    assert (await pod.client().delete(person)).status_code == 204
    assert (await pod.client().delete(person)).status_code == 404
    (back,) = await _lines(pod, source.channel_id)
    assert back["text"] == "Where is my order?" and not back["blocked"]


async def test_only_a_person_in_the_pod_can_be_blocked_and_never_oneself(pod: Pod):
    me = await pod.client().put(f"/pods/{pod.id}/blocks/MEMBER:{pod.owner['id']}")
    assert me.status_code == 422, me.text
    agent = await pod.client().put(f"/pods/{pod.id}/blocks/AGENT:{pod.id}")
    assert agent.status_code == 422, agent.text
    stranger = await pod.client().put(
        f"/pods/{pod.id}/blocks/CONTACT:00000000-0000-0000-0000-000000000001"
    )
    assert stranger.status_code == 404, stranger.text
    member = await pod.client().put(f"/pods/{pod.id}/blocks/MEMBER:{pod.user['id']}")
    assert member.status_code == 200, member.text


async def test_a_contact_the_pod_blocked_gets_nothing_from_an_agent(
    pod: Pod, db_session
):
    _, dana = await contact_place(db_session, pod)
    stopped = await pod.client().patch(
        f"/pods/{pod.id}/contacts/{dana}", json={"blocked": True}
    )
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["blocked_at"] is not None
    # Blocking is not a rename.
    assert stopped.json()["display_name"] == "Dana"

    uow = SqlAlchemyUnitOfWork(db_session)
    to = Address(kind=IdentityKind.TELEGRAM)
    at = datetime.now(timezone.utc)
    for kind in (OutreachKind.AGENT_ANSWER, OutreachKind.ACK):
        verdict = await may_send(
            uow, pod_id=UUID(pod.id), contact_id=dana, to=to, kind=kind, at=at
        )
        assert verdict.code is RefusalCode.BLOCKED
    by_hand = await may_send(
        uow,
        pod_id=UUID(pod.id),
        contact_id=dana,
        to=to,
        kind=OutreachKind.MEMBER_LINE,
        at=at,
    )
    assert by_hand.code is not RefusalCode.BLOCKED

    viewer = await pod.client(pod.viewer).patch(
        f"/pods/{pod.id}/contacts/{dana}", json={"blocked": False}
    )
    assert viewer.status_code == 403, viewer.text
    lifted = await pod.client().patch(
        f"/pods/{pod.id}/contacts/{dana}", json={"blocked": False}
    )
    assert lifted.json()["blocked_at"] is None


async def test_the_owner_of_a_chat_reports_a_reply_nobody_else_can(
    pod: Pod, db_session
):
    owner = UUID(pod.owner["id"])
    conversation = ConversationModel(user_id=owner, pod_id=UUID(pod.id), title="Q4")
    db_session.add(conversation)
    await db_session.flush()
    asked = MessageModel(
        conversation_id=conversation.id,
        sequence=1,
        role="user",
        kind="TEXT",
        text="Draft a reply to Harbor",
    )
    answered = MessageModel(
        conversation_id=conversation.id,
        sequence=2,
        role="assistant",
        kind="TEXT",
        text="Here is a draft",
    )
    db_session.add_all([asked, answered])
    await db_session.commit()
    base = f"/pods/{pod.id}/conversations/{conversation.id}/messages"

    reported = await pod.client().post(
        f"{base}/{answered.id}/report", json={"reason": "HARMFUL"}
    )
    assert reported.status_code == 201, reported.text
    row = await db_session.scalar(
        select(ContentReportModel).where(
            ContentReportModel.id == UUID(reported.json()["id"])
        )
    )
    assert row is not None and (row.place, row.author_kind) == ("CONVERSATION", "AGENT")
    assert [line["text"] for line in row.excerpt] == [
        "Draft a reply to Harbor",
        "Here is a draft",
    ]

    own = await pod.client().post(f"{base}/{asked.id}/report", json={"reason": "OTHER"})
    assert own.status_code == 422, own.text
    elsewhere = await pod.client(pod.editor).post(
        f"{base}/{answered.id}/report", json={"reason": "OTHER"}
    )
    assert elsewhere.status_code == 404, elsewhere.text


async def test_a_block_follows_a_contact_into_the_one_they_turned_out_to_be(
    pod: Pod, db_session
):
    uow = SqlAlchemyUnitOfWork(db_session)
    older = await open_contact(
        uow,
        pod_id=UUID(pod.id),
        kind=IdentityKind.EMAIL,
        value="sam@harbor.example",
        strength=IdentityStrength.CHANNEL,
        display_name="Sam",
    )
    newer = await open_contact(
        uow,
        pod_id=UUID(pod.id),
        kind=IdentityKind.TELEGRAM,
        value="5550099",
        strength=IdentityStrength.CHANNEL,
        display_name="Sam",
    )
    blocks = BlockRepository(db_session)
    owner, editor = UUID(pod.owner["id"]), UUID(pod.editor["id"])
    for who in (owner, editor):
        await blocks.block(
            pod_id=UUID(pod.id), user_id=who, person=Principal.contact(newer.id)
        )
    # The editor had blocked the older one too: carrying it over adds nothing.
    await blocks.block(
        pod_id=UUID(pod.id), user_id=editor, person=Principal.contact(older.id)
    )
    await blocks.carry(pod_id=UUID(pod.id), merged_id=newer.id, survivor_id=older.id)
    await db_session.commit()

    survivor = await blocks.blockers_of(
        pod_id=UUID(pod.id), person=Principal.contact(older.id)
    )
    assert survivor == {owner, editor}
    assert not await blocks.blockers_of(
        pod_id=UUID(pod.id), person=Principal.contact(newer.id)
    )
