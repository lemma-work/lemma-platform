"""Finding a colleague before messaging one.

``message_user`` resolves a pod member id, a user id, or an exact email address
and nothing else — a bare "Priya" returns None. That made the tool unusable
unless an id happened to be sitting in the conversation, which is the gap
``list_pod_members`` closes. So the claim under test is narrow and specific:
what comes out of the lookup must go straight into ``message_user`` unchanged.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging.models import (
    ListPodMembersRequest,
    MessageChannel,
    MessageUserRequest,
)
from app.modules.agent.tools.messaging.pydantic_adapter import (
    list_pod_members,
    message_user,
)
from app.modules.pod.contracts.directory import PodDirectoryMember, PodDirectoryPage

pytestmark = pytest.mark.asyncio

PRIYA = uuid4()
BOB = uuid4()
ME = uuid4()
# The pod-member id and the user id are different ids for the same person, and
# reachability is keyed by the second: holding one value for both would let a
# test pass while the tool looked reach up under the wrong key.
PRIYA_USER = uuid4()
BOB_USER = uuid4()


def _ctx(pod_id=None, user_id=None) -> RunContext[BaseAgentContext]:
    return RunContext(
        deps=BaseAgentContext(
            user_id=user_id or ME,
            pod_id=pod_id or uuid4(),
            conversation_id=uuid4(),
        ),
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
    )


def _podless_ctx() -> RunContext[BaseAgentContext]:
    """``pod_id`` is a required UUID, so this state cannot be built normally.

    ``model_construct`` skips validation to reach the defensive branch anyway —
    the same guard ``message_user`` and ``check_messages`` carry, kept for
    consistency with them rather than because the model can get here.
    """
    deps = BaseAgentContext.model_construct(
        user_id=ME, pod_id=None, conversation_id=uuid4()
    )
    return RunContext(
        deps=deps,
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
    )


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

    Not on the tool module: a patch installed inside the unit under test
    certifies the half the test did not write, and this tool's whole job is the
    composition -- ask `pod` who is in the pod, ask `agent_surfaces` where they
    can be reached, and hand the model one row per person.
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
        "app.modules.agent_surfaces.contracts.notifications.reachable_channels", _reach
    )
    return _fake


async def test_the_lookup_hands_back_something_message_user_accepts(monkeypatch):
    """The whole point: `to` is copied through, not re-derived by the model."""
    from uuid import UUID

    _patch(monkeypatch, _page(_members(), 2, False))

    result = await list_pod_members(_ctx(uuid4()), ListPodMembersRequest())

    assert result.success is True
    assert [m.to for m in result.members] == [str(PRIYA), str(BOB)]
    # Every `to` is a real pod member id — the form resolve_pod_recipient tries
    # first — rather than a display name the resolver would reject.
    for member in result.members:
        UUID(member.to)


async def test_a_bare_first_name_is_passed_through_as_the_search(monkeypatch):
    """The literal scenario that made message_user unusable."""
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


# ------------------------------------------------------ who is doing the asking


def _patch_send(monkeypatch, sent: dict):
    async def _resolve(*, pod_id, reference):
        return uuid4()

    async def _send(**kwargs):
        sent.update(kwargs)
        return {
            "notification_id": uuid4(),
            "delivery_status": "DELIVERED",
            "delivered_via": "RESEND",
            "undeliverable_reason": None,
        }

    monkeypatch.setattr(
        "app.modules.agent.tools.messaging.pydantic_adapter.resolve_recipient", _resolve
    )
    monkeypatch.setattr(
        "app.modules.agent.tools.messaging.pydantic_adapter.send_notification", _send
    )


async def test_the_pod_assistant_does_not_claim_to_be_an_agent_row(monkeypatch):
    """`notifications.actor_agent_id` is a foreign key into `agents`.

    A delegation token can still name the assistant by the sentinel
    `00000000-…-0001`, which is not a row in that table -- tokens are signed and
    outlive the deploy that stopped issuing them. Passing it through fails the
    insert, and because the failure escapes before commit the notification row
    is rolled back too and the recipient gets nothing at all.

    The assistant has a row now, so the fix is no longer to send nothing: it is
    to send the row's id, which is the pod's own.
    """
    from app.core.authorization.delegation import (
        DEFAULT_POD_AGENT_ID,
        DEFAULT_RESPONDER_NAME,
    )
    from app.modules.agent.tools.messaging.models import MessageUserRequest
    from app.modules.agent.tools.messaging.pydantic_adapter import message_user

    sent: dict = {}
    _patch_send(monkeypatch, sent)

    ctx = _ctx(uuid4())
    ctx.deps.workload_id = DEFAULT_POD_AGENT_ID
    ctx.deps.is_pod_default_agent = True
    ctx.deps.agent_name = "pod_default"
    ctx.deps.agent_display_name = None

    result = await message_user(
        ctx, MessageUserRequest(to="someone@example.com", message="hi")
    )

    assert result.success is True
    assert sent["actor_agent_id"] == ctx.deps.pod_id
    # Still attributed, so the recipient is not messaged by nobody -- under the
    # name it answers to. `pod_default` is the row's, and this value becomes a
    # chat bot's username and an email `From`.
    assert sent["agent_name"] == DEFAULT_RESPONDER_NAME


async def test_a_real_agent_still_reports_its_own_id(monkeypatch):
    from app.modules.agent.tools.messaging.models import MessageUserRequest
    from app.modules.agent.tools.messaging.pydantic_adapter import message_user

    agent_id = uuid4()
    sent: dict = {}
    _patch_send(monkeypatch, sent)

    ctx = _ctx(uuid4())
    ctx.deps.workload_id = agent_id
    ctx.deps.is_pod_default_agent = False
    ctx.deps.agent_display_name = "Ops"

    await message_user(ctx, MessageUserRequest(to="someone@example.com", message="hi"))

    assert sent["actor_agent_id"] == agent_id
    assert sent["agent_name"] == "Ops"


# ------------------------------------------------- choosing where it goes


def _sent(monkeypatch, **result):
    """Stand in for the whole delivery stack, and record what it was asked for."""
    payload = {
        "notification_id": uuid4(),
        "delivery_status": "DELIVERED",
        "delivered_via": "TELEGRAM",
        "undeliverable_reason": None,
    }
    payload.update(result)

    async def _resolve(*, pod_id, reference):
        return PRIYA

    async def _send(**kwargs):
        _send.seen = kwargs
        return payload

    monkeypatch.setattr(
        "app.modules.agent.tools.messaging.pydantic_adapter.resolve_recipient",
        _resolve,
    )
    monkeypatch.setattr(
        "app.modules.agent.tools.messaging.pydantic_adapter.send_notification", _send
    )
    return _send


async def test_the_channel_the_agent_named_reaches_the_router(monkeypatch):
    """Passed as the plain channel name, which is the whole vocabulary.

    The tool speaks in channels because that is what an agent can reason about;
    surface ids and mail providers are not things it can choose between.
    """
    send = _sent(monkeypatch, delivered_via="WHATSAPP")

    await message_user(
        _ctx(),
        MessageUserRequest(
            to=str(PRIYA), message="Standup?", channel=MessageChannel.WHATSAPP
        ),
    )

    assert send.seen["channel"] == "whatsapp"


async def test_leaving_the_channel_out_leaves_the_routing_alone(monkeypatch):
    """The default has to stay the default: no channel means no filter."""
    send = _sent(monkeypatch)

    await message_user(_ctx(), MessageUserRequest(to=str(PRIYA), message="Standup?"))

    assert send.seen["channel"] is None


async def test_a_refused_channel_does_not_read_as_a_routing_failure(monkeypatch):
    """ "No chat app could carry this" is true of routing and false of this.

    Something could have carried it. What stopped the send was the agent's own
    choice, and unless the answer says so the model reads the generic sentence,
    concludes the person is unreachable, and gives up on someone it could have
    reached by dropping one argument.
    """
    _sent(
        monkeypatch,
        delivery_status="UNDELIVERABLE",
        delivered_via=None,
        undeliverable_reason=(
            "They have not messaged this agent on WhatsApp. Nothing was sent "
            "elsewhere; email would reach them."
        ),
    )

    result = await message_user(
        _ctx(),
        MessageUserRequest(
            to=str(PRIYA), message="Standup?", channel=MessageChannel.WHATSAPP
        ),
    )

    assert result.success is True
    assert "you asked for whatsapp" in result.message
    assert "email would reach them" in result.message


async def test_the_lookup_says_which_channels_can_reach_each_person(monkeypatch):
    """So that naming a channel is reading an answer, not guessing at one.

    Reach is `agent_surfaces`' answer, joined onto `pod`'s member list by user
    id. The two are separate lookups, so this also pins the join: a member the
    reach lookup said nothing about must read as "inbox only", never as
    somebody else's channels.
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
