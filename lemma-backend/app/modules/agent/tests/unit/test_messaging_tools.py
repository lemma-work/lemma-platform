"""The messaging toolset: finding a colleague, and the pod's channels.

``channel_post`` with ``to`` resolves a pod member id, a user id, or an exact
email address and nothing else -- a bare "Priya" returns None. That made it
unusable unless an id happened to be sitting in the conversation, which is the
gap ``list_pod_members`` closes: what comes out of the lookup must go straight
into ``channel_post`` unchanged.

Doubles here stand only for ``pod``'s directory and ``agent_surfaces``'
reachability. What the channel tools write and read is the channels module's
half too, so those run against Postgres (``tests/e2e/test_channel_tools_e2e.py``);
these pin only the refusals that happen before anything is touched.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.messaging.models import (
    ChannelListRequest,
    ChannelPostRequest,
    ChannelReadRequest,
    ChannelSearchRequest,
    ListPodMembersRequest,
)
from app.modules.agent.infrastructure.tools.messaging.tools import (
    channel_list,
    channel_post,
    channel_read,
    channel_search,
    line_sender_name,
    list_pod_members,
)
from app.modules.agent.domain.outsiders import Audience
from app.modules.channels.contracts import ChannelMessage, Sender, SenderKind
from app.modules.pod.contracts.directory import PodDirectoryMember, PodDirectoryPage

pytestmark = pytest.mark.unit

PRIYA = uuid4()
BOB = uuid4()
ME = uuid4()
# The pod-member id and the user id are different ids for the same person, and
# reachability is keyed by the second: holding one value for both would let a
# test pass while the tool looked reach up under the wrong key.
PRIYA_USER = uuid4()
BOB_USER = uuid4()
NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def _ctx(pod_id=None, user_id=None, *, run_id=None, tool_call_id=None) -> ToolContext:
    return ToolContext(
        user_id=user_id or ME,
        pod_id=pod_id or uuid4(),
        conversation_id=uuid4(),
        agent_run_id=run_id,
        tool_call_id=tool_call_id,
    )


def _podless_ctx() -> ToolContext:
    """``pod_id`` is a required UUID, so this state cannot be built normally.

    ``model_construct`` skips validation to reach the defensive branch anyway --
    the same guard every tool here carries, kept for consistency rather than
    because the model can get here.
    """
    return ToolContext.model_construct(user_id=ME, pod_id=None, conversation_id=uuid4())


# ------------------------------------------------------------ list_pod_members


def _members() -> list[PodDirectoryMember]:
    return [
        PodDirectoryMember(
            to=str(PRIYA),
            name="Priya Sharma",
            email="priya@example.com",
            role="USER",
            is_you=False,
            user_id=PRIYA_USER,
        ),
        PodDirectoryMember(
            to=str(BOB),
            name="Bob Jones",
            email="bob@example.com",
            role="ADMIN",
            is_you=True,
            user_id=BOB_USER,
        ),
    ]


def _page(members, total, truncated) -> PodDirectoryPage:
    return PodDirectoryPage(
        members=tuple(members), total_matched=total, truncated=truncated
    )


def _patch(monkeypatch, result, reach=None):
    """Double the two published operations the tool composes, where they live.

    Not on the tool module: this tool's whole job is the composition -- ask
    `pod` who is in the pod, ask `agent_surfaces` where they can be reached,
    and hand the model one row per person.
    """

    async def _fake(*, pod_id, requester_user_id, search, limit):
        _fake.seen = {
            "search": search,
            "limit": limit,
            "pod_id": pod_id,
        }
        return result

    async def _reach(*, pod_id, recipients, actor_agent_id):
        _fake.seen["actor_agent_id"] = actor_agent_id
        _fake.seen["recipients"] = recipients
        return dict(reach or {})

    monkeypatch.setattr("app.modules.pod.contracts.directory.list_pod_members", _fake)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.contracts.direct_messages.reachable_channels",
        _reach,
    )
    return _fake


async def test_the_lookup_hands_back_something_channel_post_accepts(monkeypatch):
    """The whole point: `to` is copied through, not re-derived by the model."""
    _patch(monkeypatch, _page(_members(), 2, False))

    result = await list_pod_members(_ctx(uuid4()), ListPodMembersRequest())

    assert result.success is True
    assert [m.to for m in result.members] == [str(PRIYA), str(BOB)]
    # Every `to` is a real pod member id -- the form resolve_recipient tries
    # first -- rather than a display name the resolver would reject.
    for member in result.members:
        UUID(member.to)
    assert "channel_post" in (result.message or "")


async def test_a_bare_first_name_is_passed_through_as_the_search(monkeypatch):
    """The literal scenario that made messaging by name unusable."""
    fake = _patch(monkeypatch, _page([_members()[0]], 1, False))

    result = await list_pod_members(
        _ctx(uuid4()), ListPodMembersRequest(search="priya")
    )

    assert fake.seen["search"] == "priya"
    assert result.members[0].name == "Priya Sharma"


async def test_the_caller_is_flagged_so_the_agent_knows_who_it_is(monkeypatch):
    """Reaching the run's own owner needs no permission; the others do."""
    _patch(monkeypatch, _page(_members(), 2, False))

    result = await list_pod_members(_ctx(uuid4()), ListPodMembersRequest())

    assert [m.is_you for m in result.members] == [False, True]


async def test_no_pod_access_is_an_error_result_not_an_exception(monkeypatch):
    """A tool must hand the model something it can act on, not a traceback."""
    _patch(monkeypatch, None)

    result = await list_pod_members(_ctx(uuid4()), ListPodMembersRequest())

    assert result.success is False
    assert "access" in (result.error or "").lower()


async def test_an_empty_match_says_so_rather_than_looking_broken(monkeypatch):
    _patch(monkeypatch, _page([], 0, False))

    result = await list_pod_members(
        _ctx(uuid4()), ListPodMembersRequest(search="nobody")
    )

    assert result.success is True
    assert result.members == []
    assert "nobody" in (result.message or "")


async def test_outside_a_pod_the_tool_refuses(monkeypatch):
    result = await list_pod_members(_podless_ctx(), ListPodMembersRequest())

    assert result.success is False
    assert "pod" in (result.error or "").lower()


async def test_truncation_tells_the_model_to_narrow_the_search(monkeypatch):
    """Silently returning a partial list is how an agent messages the wrong person."""
    _patch(monkeypatch, _page(_members(), 87, True))

    result = await list_pod_members(_ctx(uuid4()), ListPodMembersRequest())

    assert result.truncated is True
    assert result.total_matched == 87
    assert "87" in (result.message or "")


async def test_the_lookup_says_which_channels_can_reach_each_person(monkeypatch):
    """So that naming a platform is reading an answer, not guessing at one.

    Reach is `agent_surfaces`' answer, joined onto `pod`'s member list by user
    id. The two are separate lookups, so this also pins the join: a member the
    reach lookup said nothing about must read as "their channel in Lemma only",
    never as somebody else's platforms.
    """
    _patch(
        monkeypatch,
        _page(_members(), 2, False),
        reach={PRIYA_USER: ["email", "telegram"]},
    )

    result = await list_pod_members(_ctx(), ListPodMembersRequest())

    assert result.members[0].reachable_on == ["email", "telegram"]
    assert result.members[1].reachable_on == []


async def test_reach_is_asked_only_about_the_people_being_returned(monkeypatch):
    """Reachability costs queries per surface; rows nobody sees are not worth it."""
    fake = _patch(monkeypatch, _page(_members(), 2, False))

    await list_pod_members(_ctx(), ListPodMembersRequest())

    assert set(fake.seen["recipients"]) == {PRIYA_USER, BOB_USER}


# ---------------------------------------------- channel tools: refused up front


def _resolving(monkeypatch, recipient):
    async def _resolve(*, pod_id, reference):
        return recipient

    monkeypatch.setattr(
        "app.modules.agent_surfaces.contracts.direct_messages.resolve_recipient",
        _resolve,
    )


def _no_database(monkeypatch):
    def _uow():  # pragma: no cover - must not be reached
        raise AssertionError("the tool reached the database")

    monkeypatch.setattr(
        "app.modules.agent.infrastructure.tools.messaging.tools._uow", _uow
    )


async def test_a_name_that_resolves_to_nobody_points_at_the_lookup(monkeypatch):
    """Refused before anything is posted, with the tool that fixes it named."""
    _resolving(monkeypatch, None)
    _no_database(monkeypatch)

    result = await channel_post(_ctx(), ChannelPostRequest(text="hi", to="Priya"))

    assert result.success is False
    assert "list_pod_members" in (result.error or "")


@pytest.mark.parametrize(
    "request_",
    [
        ChannelPostRequest(text="hi"),
        ChannelPostRequest(text="hi", to="priya@example.com", channel_id=uuid4()),
    ],
    ids=["neither", "both"],
)
async def test_a_post_names_exactly_one_place(monkeypatch, request_):
    """A member or a channel: guessing between two would post somewhere the
    agent did not mean."""
    _no_database(monkeypatch)

    result = await channel_post(_ctx(), request_)

    assert result.success is False


async def test_a_question_in_a_channel_is_refused_not_dropped(monkeypatch):
    """`wants_answer` asks one person. In a channel nobody would be waited on,
    so the asker would never be woken: say so, rather than post it as a plain
    line and leave the agent waiting for an answer that cannot come."""
    _no_database(monkeypatch)

    result = await channel_post(
        _ctx(),
        ChannelPostRequest(text="Ship it?", channel_id=uuid4(), wants_answer=True),
    )

    assert result.success is False
    assert "`to`" in (result.error or "")


async def test_outside_a_pod_nothing_is_posted(monkeypatch):
    _no_database(monkeypatch)

    result = await channel_post(
        _podless_ctx(), ChannelPostRequest(text="hi", to=str(PRIYA))
    )

    assert result.success is False


@pytest.mark.parametrize(
    ("tool", "request_"),
    [
        (channel_post, ChannelPostRequest(text="hi", to=str(PRIYA))),
        (channel_read, ChannelReadRequest(asked_by_me=True)),
        (channel_search, ChannelSearchRequest(query="invoice")),
        (channel_list, ChannelListRequest()),
    ],
    ids=["post", "read", "search", "list"],
)
async def test_a_strangers_run_touches_no_channel(monkeypatch, tool, request_):
    _no_database(monkeypatch)
    ctx = _ctx()
    ctx.audience = Audience.contact(uuid4())

    result = await tool(ctx, request_)

    assert result.success is False
    assert "ask_a_person" in (result.error or "")


async def test_a_read_names_a_channel_or_asks_for_the_questions(monkeypatch):
    _no_database(monkeypatch)

    result = await channel_read(_ctx(), ChannelReadRequest())

    assert result.success is False
    assert "asked_by_me" in (result.error or "")


async def test_an_unknown_kind_is_refused_with_the_ones_that_exist(monkeypatch):
    _no_database(monkeypatch)

    result = await channel_list(_ctx(), ChannelListRequest(kind="team"))

    assert result.success is False
    assert "DIRECT" in (result.error or "")


def _line(sender: Sender) -> ChannelMessage:
    return ChannelMessage(
        id=uuid4(),
        channel_id=uuid4(),
        pod_id=uuid4(),
        seq=1,
        sender=sender,
        text="x",
        created_at=NOW,
    )


def test_a_sender_with_no_name_is_named_by_what_they_are():
    """The agent reads who said a line; a nameless one still says whether it
    was a member, a contact, or an agent -- never an id."""
    assert line_sender_name(_line(Sender.member(uuid4()))) == "a member"
    assert line_sender_name(_line(Sender.contact(uuid4()))) == "a contact"
    agent = Sender(kind=SenderKind.AGENT, agent_id=uuid4())
    assert line_sender_name(_line(agent)) == "an agent"
    named = Sender.member(uuid4(), display_name="Priya")
    assert line_sender_name(_line(named)) == "Priya"
