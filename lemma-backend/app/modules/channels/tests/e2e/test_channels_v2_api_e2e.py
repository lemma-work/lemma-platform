"""The channels API v2, as a pod's members use it -- end to end, on Postgres.

The four members of ``channel_pod``. One rule decides who sees a channel, and
every read -- get, lines, search, inbox, counts -- answers by it: a channel the
reader may not see is missing, never forbidden.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import (
    AccountKey,
    DeliveryReasonCode,
    GroupHome,
    NewLine,
    NewQuestion,
    Place,
    Principal,
    QuestionKind,
    Sender,
    Source,
    SourceKind,
    channel_by_id,
    join,
    post,
    source_for,
)
from app.modules.channels.infrastructure.deliveries import DeliveryRepository
from app.modules.channels.tests.e2e.channel_pod import Pod, an_asker, contact_place
from app.modules.channels.tests.e2e.channel_pod import (
    group as _group,
)
from app.modules.channels.tests.e2e.channel_pod import (
    say as _say,
)

pytestmark = pytest.mark.e2e


async def _mirrored_group(db_session: AsyncSession, pod: Pod) -> Source:
    """A public Telegram group's channel, with one line said there."""
    uow = SqlAlchemyUnitOfWork(db_session)
    source = await source_for(
        uow,
        pod_id=UUID(pod.id),
        place=Place(
            surface_id=None,
            platform="TELEGRAM",
            account=AccountKey(workspace="bot-1"),
            kind=SourceKind.CHAT,
            external_channel_id="-100",
            external_user_id=None,
            title="Launch crew",
            is_private=False,
        ),
        home=GroupHome(title="Launch crew"),
    )
    await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=Sender.member(UUID(pod.owner["id"])),
            text="said on telegram",
            source_id=source.id,
            external_message_id="tg-1",
        ),
    )
    await db_session.commit()
    return source


# -- who sees what ---------------------------------------------------------------


async def test_a_private_group_is_missing_to_who_is_not_in_it(pod: Pod):
    public = await _group(pod, title="Town square")
    private = await _group(
        pod, title="Secret plans", visibility="PRIVATE", members=[pod.editor["id"]]
    )
    await _say(pod, private["id"], "the secret plan")
    await _say(pod, public["id"], "an open plan")
    user = pod.client(pod.user)

    listed = await user.get(pod.base)
    assert listed.status_code == 200, listed.text
    assert [c["id"] for c in listed.json()["items"]] == [public["id"]]
    for path in (
        f"{pod.base}/{private['id']}",
        f"{pod.base}/{private['id']}/messages",
    ):
        refused = await user.get(path)
        assert refused.status_code == 404, refused.text
        assert "secret" not in refused.text
    joined = await user.post(f"{pod.base}/{private['id']}/members/me")
    assert joined.status_code == 404, joined.text
    found = await user.get(f"/pods/{pod.id}/search", params={"q": "plan"})
    assert [hit["message"]["text"] for hit in found.json()["items"]] == ["an open plan"]
    scoped = await user.get(
        f"/pods/{pod.id}/search", params={"q": "plan", "channel_id": private["id"]}
    )
    assert scoped.status_code == 404, scoped.text

    editor = pod.client(pod.editor)
    seen = await editor.get(f"{pod.base}/{private['id']}/messages")
    assert [m["text"] for m in seen.json()["items"]] == ["the secret plan"]
    assert {c["id"] for c in (await editor.get(pod.base)).json()["items"]} == {
        public["id"],
        private["id"],
    }


async def test_a_members_own_channel_is_theirs_alone_and_no_get_makes_it(pod: Pod):
    user = pod.client(pod.user)

    assert (await user.get(f"{pod.base}/me")).status_code == 404
    assert (await user.get(pod.base, params={"kind": "DIRECT"})).json()["items"] == []

    made = await user.post(f"{pod.base}/me")
    assert made.status_code == 201, made.text
    again = await user.post(f"{pod.base}/me")
    assert again.status_code == 200 and again.json()["id"] == made.json()["id"]
    mine = made.json()
    assert mine["kind"] == "DIRECT" and mine["person"]["id"] == pod.user["id"]
    assert (await user.get(f"{pod.base}/me")).json()["id"] == mine["id"]

    for who in (None, pod.editor):
        for path in (f"{pod.base}/{mine['id']}", f"{pod.base}/{mine['id']}/messages"):
            refused = await pod.client(who).get(path)
            assert refused.status_code == 404, refused.text


async def test_a_contacts_channel_is_missing_without_contact_read(
    pod: Pod, db_session: AsyncSession
):
    source, _ = await contact_place(db_session, pod)
    channel = f"{pod.base}/{source.channel_id}"
    # A seat does not open it: the user is in it, and still may not see it.
    found = await channel_by_id(SqlAlchemyUnitOfWork(db_session), source.channel_id)
    assert found is not None
    await join(
        SqlAlchemyUnitOfWork(db_session),
        channel=found,
        principal=Principal.member(UUID(pod.user["id"])),
    )
    await db_session.commit()

    for who in (pod.user, pod.viewer):
        client = pod.client(who)
        for path in (channel, f"{channel}/messages"):
            refused = await client.get(path)
            assert refused.status_code == 404, refused.text
            assert "Where is my order" not in refused.text
        listed = await client.get(pod.base, params={"kind": "CONTACT"})
        assert listed.json()["items"] == []
        inbox = await client.get(f"/pods/{pod.id}/inbox")
        assert inbox.json()["totals"]["channels"] == 0, inbox.text
        found = await client.get(f"/pods/{pod.id}/search", params={"q": "order"})
        assert found.json()["items"] == []

    editor = pod.client(pod.editor)
    seen = await editor.get(f"{channel}/messages")
    assert seen.status_code == 200, seen.text
    assert [m["text"] for m in seen.json()["items"]] == ["Where is my order?"]
    detail = (await editor.get(channel)).json()
    assert detail["person"]["kind"] == "CONTACT" and detail["person"]["name"] == "Dana"
    assert detail["agents_answer"] is True and detail["status"] == "open"


# -- posting ---------------------------------------------------------------------


async def test_naming_somebody_who_cannot_see_the_channel_is_refused(pod: Pod):
    private = await _group(pod, visibility="PRIVATE", members=[pod.editor["id"]])

    def mention(person: dict) -> dict:
        return {"type": "mention", "kind": "MEMBER", "id": person["id"], "name": "P"}

    refused = await pod.client().post(
        f"{pod.base}/{private['id']}/messages",
        json={"text": "@P look", "parts": [mention(pod.user)]},
    )
    assert refused.status_code == 422, refused.text

    named = await _say(pod, private["id"], "@P look", parts=[mention(pod.editor)])
    assert named["parts"][0]["id"] == pod.editor["id"]
    inbox = (await pod.client(pod.editor).get(f"/pods/{pod.id}/inbox")).json()
    assert inbox["totals"]["mentions"] == 1
    assert [item["message"]["id"] for item in inbox["mentions"]] == [named["id"]]
    listed = (await pod.client(pod.editor).get(pod.base)).json()["items"]
    assert listed[0]["unread"]["mentions"] == 1
    renamed = await pod.client().patch(
        f"{pod.base}/{private['id']}/messages/{named['id']}",
        json={"text": "@P look", "parts": [mention(pod.user)]},
    )
    assert renamed.status_code == 422, renamed.text


async def test_a_retried_post_is_the_same_line(pod: Pod):
    group = await _group(pod)
    first = await _say(pod, group["id"], "once", client_message_id="abc-1")
    again = await _say(pod, group["id"], "once", client_message_id="abc-1")
    assert (again["id"], again["seq"]) == (first["id"], first["seq"])

    keyed = [
        await pod.client().post(
            f"{pod.base}/{group['id']}/messages",
            json={"text": "keyed"},
            headers={"Idempotency-Key": "k" * 100},
        )
        for _ in range(2)
    ]
    assert keyed[0].json()["id"] == keyed[1].json()["id"]
    lines = (await pod.client().get(f"{pod.base}/{group['id']}/messages")).json()
    assert [m["text"] for m in lines["items"]] == ["once", "keyed"]


async def test_a_retried_create_is_the_same_channel(pod: Pod):
    made = [
        await pod.client().post(
            pod.base, json={"title": "once"}, headers={"Idempotency-Key": "make-1"}
        )
        for _ in range(2)
    ]
    assert made[0].status_code == 201, made[0].text
    assert made[0].json()["id"] == made[1].json()["id"]
    other = await pod.client(pod.editor).post(
        pod.base, json={"title": "once"}, headers={"Idempotency-Key": "make-1"}
    )
    assert other.json()["id"] != made[0].json()["id"]


async def test_the_timeline_pages_by_number_both_ways(pod: Pod):
    group = await _group(pod)
    for n in range(1, 8):
        await _say(pod, group["id"], f"line {n}")
    messages = f"{pod.base}/{group['id']}/messages"
    owner = pod.client()

    newest = (await owner.get(messages, params={"limit": 3})).json()
    assert [m["seq"] for m in newest["items"]] == [5, 6, 7]
    assert newest["has_more"] and newest["next_before_seq"] == 5
    seen = [m["seq"] for m in newest["items"]]
    before = newest["next_before_seq"]
    while before:
        page = (
            await owner.get(messages, params={"limit": 3, "before_seq": before})
        ).json()
        seen = [m["seq"] for m in page["items"]] + seen
        before = page["next_before_seq"] if page["has_more"] else None
    assert seen == list(range(1, 8))

    forward: list[int] = []
    after = 0
    while True:
        page = (
            await owner.get(messages, params={"limit": 3, "after_seq": after})
        ).json()
        forward += [m["seq"] for m in page["items"]]
        after = page["next_after_seq"]
        if not page["has_more"]:
            break
    assert forward == list(range(1, 8))
    assert (await owner.get(messages, params={"after_seq": 7})).json()["items"] == []


async def test_reading_counts_by_number_and_never_goes_back(pod: Pod):
    group = await _group(pod, members=[pod.editor["id"]])
    for n in range(3):
        await _say(pod, group["id"], f"line {n}")
    editor = pod.client(pod.editor)

    def unread(listing) -> dict:
        (row,) = [c for c in listing.json()["items"] if c["id"] == group["id"]]
        return row["unread"]

    assert unread(await editor.get(pod.base))["lines"] == 3
    assert unread(await pod.client().get(pod.base))["lines"] == 0
    assert (await editor.get(f"/pods/{pod.id}/inbox")).json()["totals"]["channels"] == 1

    read = f"{pod.base}/{group['id']}/read"
    assert (await editor.post(read, json={"seq": 2})).json() == {"read_seq": 2}
    assert unread(await editor.get(pod.base)) == {
        "lines": 1,
        "mentions": 0,
        "waiting": 0,
        "read_seq": 2,
    }
    assert (await editor.post(read, json={"seq": 99})).json() == {"read_seq": 3}
    assert (await editor.post(read, json={"seq": 1})).json() == {"read_seq": 3}
    assert (await editor.get(f"/pods/{pod.id}/inbox")).json()["totals"]["channels"] == 0
    me = (await editor.get(f"{pod.base}/{group['id']}")).json()["me"]
    assert me["joined"] and me["read_seq"] == 3


async def test_a_reply_goes_where_its_thread_is(pod: Pod, db_session: AsyncSession):
    source = await _mirrored_group(db_session, pod)
    channel = str(source.channel_id)
    root = (await pod.client().get(f"{pod.base}/{channel}/messages")).json()["items"][0]
    assert root["source_id"] == str(source.id)

    reply = await _say(
        pod, channel, "answering in the thread", thread_root_id=root["id"]
    )
    assert reply["source_id"] == str(source.id)
    assert reply["thread_root_id"] == root["id"]
    elsewhere = await pod.client().post(
        f"{pod.base}/{channel}/messages",
        json={"text": "x", "thread_root_id": root["id"], "source_id": str(uuid4())},
    )
    assert elsewhere.status_code == 422, elsewhere.text

    timeline = (await pod.client().get(f"{pod.base}/{channel}/messages")).json()
    assert [m["id"] for m in timeline["items"]] == [root["id"]]
    assert timeline["items"][0]["reply_count"] == 1
    thread = (
        await pod.client().get(f"{pod.base}/{channel}/messages/{reply['id']}/thread")
    ).json()
    assert thread["root"]["id"] == root["id"]
    assert [m["id"] for m in thread["items"]] == [reply["id"]]

    sends = f"{pod.base}/{channel}/messages/{reply['id']}/deliveries"
    (delivery,) = (await pod.client().get(sends)).json()["items"]
    assert delivery["target"] == {
        "kind": "source",
        "source_id": str(source.id),
        "person": None,
    }
    assert delivery["status"] == "PENDING"
    retry = f"{sends}/{delivery['id']}/retry"
    assert (await pod.client().post(retry)).status_code == 409
    deliveries = DeliveryRepository(db_session)
    claimed = await deliveries.claim(UUID(delivery["id"]))
    assert claimed is not None and claimed.attempts == 1
    await deliveries.failed(
        claimed,
        code=DeliveryReasonCode.TRANSIENT,
        reason="timeout",
        platform="TELEGRAM",
    )
    await db_session.commit()
    # The user reads the line but did not write it: refused, not hidden.
    refused = await pod.client(pod.user).post(retry)
    assert refused.status_code == 403, refused.text
    retried = await pod.client().post(retry)
    assert retried.status_code == 200, retried.text
    # One more try, counted with the first; why the first failed is not
    # shown as if it were this one's.
    assert retried.json()["status"] == "PENDING"
    assert retried.json()["attempts"] == 1
    assert retried.json()["reason"] is None


async def test_a_deleted_line_cannot_be_edited(pod: Pod):
    group = await _group(pod, members=[pod.editor["id"]])
    line = await _say(pod, group["id"], "oops")
    path = f"{pod.base}/{group['id']}/messages/{line['id']}"

    theirs = await pod.client(pod.editor).patch(path, json={"text": "mine now"})
    assert theirs.status_code == 403, theirs.text
    edited = await pod.client().patch(path, json={"text": "fixed"})
    assert edited.json()["text"] == "fixed" and edited.json()["edited_at"]
    assert (await pod.client().delete(path)).status_code == 204
    assert (await pod.client().patch(path, json={"text": "back"})).status_code == 409
    assert (await pod.client().delete(path)).status_code == 409
    shown = (await pod.client().get(f"{pod.base}/{group['id']}/messages")).json()
    assert shown["items"][0]["deleted"] and shown["items"][0]["text"] is None


# -- questions ---------------------------------------------------------------------


async def test_a_question_is_answered_by_its_person_and_an_approval_needs_a_decision(
    pod: Pod, db_session: AsyncSession
):
    group = await _group(pod, members=[pod.editor["id"]])
    owner = UUID(pod.owner["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    asked = {}
    for kind in (QuestionKind.APPROVAL, QuestionKind.ASK):
        line = await post(
            uow,
            channel_id=UUID(group["id"]),
            line=NewLine(sender=Sender.member(owner), text=f"{kind.value.lower()}?"),
            question=NewQuestion(
                kind=kind,
                ref=f"e2e:{uuid4()}",
                asker=await an_asker(UUID(pod.id), owner),
                question={"prompt": "May I?"},
                awaits=Principal.member(owner),
            ),
        )
        assert line is not None
        asked[kind] = line.id
    await db_session.commit()

    inbox = (await pod.client().get(f"/pods/{pod.id}/inbox")).json()
    assert inbox["totals"]["waiting"] == 2
    assert {item["message"]["id"] for item in inbox["waiting"]} == {
        str(each) for each in asked.values()
    }

    def answer(kind: QuestionKind) -> str:
        return f"{pod.base}/{group['id']}/messages/{asked[kind]}/answer"

    other = await pod.client(pod.editor).post(
        answer(QuestionKind.ASK), json={"answer": {"text": "yes"}}
    )
    assert other.status_code == 403, other.text
    blank = await pod.client().post(answer(QuestionKind.APPROVAL), json={"answer": {}})
    assert blank.status_code == 422, blank.text

    approved = await pod.client().post(
        answer(QuestionKind.APPROVAL), json={"answer": {"decision": "DENY"}}
    )
    assert approved.status_code == 200, approved.text
    question = approved.json()["question"]
    assert question["state"] == "ANSWERED"
    assert question["answer"] == {"decision": "DENY"}
    assert question["answered_by"]["id"] == pod.owner["id"]
    twice = await pod.client().post(
        answer(QuestionKind.APPROVAL), json={"answer": {"decision": "APPROVE_ONCE"}}
    )
    assert twice.status_code == 409, twice.text
    inbox = (await pod.client().get(f"/pods/{pod.id}/inbox")).json()
    assert inbox["totals"]["waiting"] == 1


# -- members and agents ----------------------------------------------------------------


async def test_a_groups_agents_are_added_changed_and_removed(pod: Pod):
    agent = await pod.scenario.create_agent(name="reviewer")
    group = await _group(pod)
    members = f"{pod.base}/{group['id']}/members"

    added = await pod.client().post(
        members, json={"agents": [{"id": agent["id"], "answers": "EVERY_LINE"}]}
    )
    assert added.status_code == 200, added.text
    (seat,) = [m for m in added.json()["members"] if m["person"]["kind"] == "AGENT"]
    assert seat["person"] == {"kind": "AGENT", "id": agent["id"], "name": "reviewer"}
    assert seat["answers"] == "EVERY_LINE"

    stranger = await pod.client().post(
        members, json={"agents": [{"id": str(uuid4()), "answers": "MENTIONED"}]}
    )
    assert stranger.status_code == 422, stranger.text
    not_theirs = await pod.client(pod.user).post(
        members, json={"agents": [{"id": agent["id"]}]}
    )
    assert not_theirs.status_code == 403, not_theirs.text

    changed = await pod.client().patch(
        f"{members}/AGENT:{agent['id']}", json={"answers": "MENTIONED"}
    )
    assert changed.status_code == 200, changed.text
    assert [
        m["answers"]
        for m in changed.json()["members"]
        if m["person"]["kind"] == "AGENT"
    ] == ["MENTIONED"]
    gone = f"{members}/AGENT:{agent['id']}"
    assert (await pod.client().delete(gone)).status_code == 204
    assert (await pod.client().delete(gone)).status_code == 404


async def test_a_member_joins_a_public_group_sets_how_they_hear_and_leaves(pod: Pod):
    group = await _group(pod)
    user = pod.client(pod.user)
    me = f"{pod.base}/{group['id']}/members/me"

    assert (await user.patch(me, json={"notify": "NONE"})).status_code == 409
    joined = await user.post(me)
    assert joined.status_code == 200 and joined.json()["me"]["joined"]
    assert joined.json()["me"]["can_manage"] is False
    quiet = await user.patch(
        me,
        json={"notify": "MENTIONS", "notify_via": ["slack"], "muted_until": None},
    )
    assert quiet.status_code == 200, quiet.text
    assert {k: quiet.json()["me"][k] for k in ("notify", "notify_via")} == {
        "notify": "MENTIONS",
        "notify_via": ["SLACK"],
    }
    cleared = await user.patch(me, json={"notify_via": None})
    assert cleared.json()["me"]["notify_via"] is None
    assert cleared.json()["me"]["notify"] == "MENTIONS"

    left = await user.delete(
        f"{pod.base}/{group['id']}/members/MEMBER:{pod.user['id']}"
    )
    assert left.status_code == 204, left.text
    assert not (await user.get(f"{pod.base}/{group['id']}")).json()["me"]["joined"]
    # Leaving by `me` is the same, and leaving again is nothing.
    assert (await user.post(me)).status_code == 200
    assert (await user.delete(me)).status_code == 204
    assert not (await user.get(f"{pod.base}/{group['id']}")).json()["me"]["joined"]
    assert (await user.delete(me)).status_code == 204


# -- changing a channel ------------------------------------------------------------------


async def test_a_contacts_agent_switched_on_hands_them_back(
    pod: Pod, db_session: AsyncSession
):
    source, _ = await contact_place(db_session, pod)
    channel = f"{pod.base}/{source.channel_id}"
    editor = pod.client(pod.editor)

    assert (
        await pod.client(pod.user).patch(channel, json={"status": "closed"})
    ).status_code == 404
    assert (await editor.patch(channel, json={"title": "Dana"})).status_code == 409
    nobody = await editor.patch(channel, json={"assignee": pod.user["id"]})
    assert nobody.status_code == 422, nobody.text

    assigned = await editor.patch(
        channel, json={"assignee": pod.editor["id"], "agents_answer": False}
    )
    assert assigned.status_code == 200, assigned.text
    body = assigned.json()
    assert body["assignee"]["id"] == pod.editor["id"] and body["agents_answer"] is False
    mine = await editor.get(pod.base, params={"assignee": "me", "kind": "CONTACT"})
    assert [c["id"] for c in mine.json()["items"]] == [str(source.channel_id)]

    on = await editor.patch(channel, json={"agents_answer": True})
    assert on.status_code == 200, on.text
    assert on.json()["agents_answer"] is True and on.json()["assignee"] is None
    unassigned = await editor.get(
        pod.base, params={"unassigned": True, "kind": "CONTACT"}
    )
    assert [c["id"] for c in unassigned.json()["items"]] == [str(source.channel_id)]
    closed = await editor.patch(channel, json={"status": "closed"})
    assert closed.json()["status"] == "closed"


async def test_a_patch_changes_what_it_names_and_null_clears(pod: Pod):
    group = await _group(pod, topic="Q3 launch")
    path = f"{pod.base}/{group['id']}"
    owner = pod.client()

    untouched = await owner.patch(path, json={})
    assert untouched.json()["topic"] == "Q3 launch"
    cleared = await owner.patch(path, json={"topic": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["topic"] is None and cleared.json()["title"] == "launch"
    assert (await owner.patch(path, json={"title": None})).status_code == 422
    assert (await owner.patch(path, json={"agents_answer": False})).status_code == 409
    assert (
        await pod.client(pod.user).patch(path, json={"title": "x"})
    ).status_code == 403

    archived = await owner.patch(path, json={"archived": True})
    assert archived.json()["archived"] is True
    refused = await owner.post(f"{path}/messages", json={"text": "still here?"})
    assert refused.status_code == 409, refused.text
    assert group["id"] not in {
        c["id"] for c in (await owner.get(pod.base)).json()["items"]
    }
    back = await owner.patch(path, json={"archived": False})
    assert back.json()["archived"] is False


async def test_only_a_group_made_in_lemma_is_deleted(
    pod: Pod, db_session: AsyncSession
):
    mirrored = await _mirrored_group(db_session, pod)
    refused = await pod.client().delete(f"{pod.base}/{mirrored.channel_id}")
    assert refused.status_code == 409, refused.text

    group = await _group(pod)
    assert (
        await pod.client(pod.user).delete(f"{pod.base}/{group['id']}")
    ).status_code == 403
    assert (await pod.client().delete(f"{pod.base}/{group['id']}")).status_code == 204
    assert (await pod.client().get(f"{pod.base}/{group['id']}")).status_code == 404


async def test_the_list_pages_without_skipping_or_repeating(pod: Pod):
    made = [(await _group(pod, title=f"g{n}"))["id"] for n in range(5)]
    for channel_id in made:
        await _say(pod, channel_id)
    seen: list[str] = []
    cursor = None
    while True:
        page = await pod.client().get(
            pod.base, params={"limit": 2, **({"cursor": cursor} if cursor else {})}
        )
        assert page.status_code == 200, page.text
        seen += [c["id"] for c in page.json()["items"]]
        cursor = page.json()["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(made) and len(seen) == len(set(seen))
    junk = await pod.client().get(pod.base, params={"cursor": "yesterday"})
    assert junk.status_code == 400
