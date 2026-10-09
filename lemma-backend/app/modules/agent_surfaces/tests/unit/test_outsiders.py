"""Somebody outside the pod, in a group its bot is in.

Three decisions are pinned here, each of which fails open if it is wrong:

* who counts as an outsider -- never a member, and only in a group the pod
  knows, has opened to outsiders, and has somebody answering for;
* where their turn runs -- one conversation per group, the member's, marked as
  answering outsiders so the agent authorizes as nobody;
* that nothing a stranger types is taken as the member deciding something.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.contracts.audience import Audience
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    ConversationType,
    SurfaceConfig,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceLifecycleKind,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.groups import OUTSIDERS_LINK_USER, SurfaceGroup
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.models import SurfaceMessageMetadata
from app.modules.agent_surfaces.platforms.telegram.membership import (
    joined_group_event,
)
from app.modules.agent_surfaces.services.surface_inbound_message import (
    may_answer_a_pause,
)
from app.modules.agent_surfaces.services.group_log import (
    answered_in_group,
    keeps_group_log,
)
from app.modules.agent_surfaces.services.outsiders import OutsiderDoor
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)

pytestmark = pytest.mark.unit

POD = uuid4()
OWNER = uuid4()
CHAT = "-1001234567890"


# ------------------------------------------------------------ being brought in


def _my_chat_member(*, old: str, new: str, chat_type: str = "supergroup", **actor):
    return {
        "update_id": 1,
        "my_chat_member": {
            "chat": {"id": int(CHAT), "type": chat_type, "title": "Launch crew"},
            "from": {"id": 900100, "is_bot": False, **actor},
            "old_chat_member": {"status": old},
            "new_chat_member": {"status": new},
        },
    }


def test_being_added_to_a_group_names_the_group_and_who_did_it():
    event = joined_group_event(_my_chat_member(old="left", new="member"))

    assert event is not None
    assert event.kind is SurfaceLifecycleKind.JOINED_CHANNEL
    assert event.platform is SurfacePlatform.TELEGRAM
    assert event.external_channel_id == CHAT
    assert event.actor_external_user_id == "900100"
    assert event.channel_title == "Launch crew"


@pytest.mark.parametrize(
    ("old", "new", "chat_type", "actor"),
    [
        # A promotion is not an arrival.
        ("member", "administrator", "supergroup", {}),
        # Leaving is not an arrival either.
        ("member", "left", "supergroup", {}),
        # A private chat is not a group, whatever its status does.
        ("kicked", "member", "private", {}),
        # Another bot adding it names nobody who could answer for the group.
        ("left", "member", "group", {"is_bot": True}),
    ],
)
def test_nothing_else_reads_as_being_brought_in(old, new, chat_type, actor):
    assert (
        joined_group_event(
            _my_chat_member(old=old, new=new, chat_type=chat_type, **actor)
        )
        is None
    )


def test_an_ordinary_message_is_not_a_membership_change():
    assert joined_group_event({"message": {"text": "hi"}}) is None


# ------------------------------------------------------------- who is logged


def test_the_pod_keeps_a_log_only_where_the_platform_keeps_no_history():
    assert keeps_group_log("TELEGRAM")
    assert keeps_group_log("WHATSAPP")
    assert not keeps_group_log("SLACK")
    assert not keeps_group_log("TEAMS")


def test_a_group_welcomes_outsiders_only_with_somebody_answering_for_them():
    group = SurfaceGroup(
        pod_id=POD, surface_id=uuid4(), platform="TELEGRAM", external_channel_id=CHAT
    )

    assert not group.welcomes_outsiders
    assert group.model_copy(update={"owner_user_id": OWNER}).welcomes_outsiders
    assert not group.model_copy(
        update={"owner_user_id": OWNER, "answers_outsiders": False}
    ).welcomes_outsiders


# ------------------------------------------------------------- who is outside


def _surface(*, answers_outsiders: bool = True):
    config = SurfaceConfig.model_validate(
        {"groups": {"answers_outsiders": answers_outsiders}}
    )
    return SimpleNamespace(
        id=uuid4(), pod_id=POD, surface_type=SurfacePlatform.TELEGRAM, config=config
    )


def _event(*, is_dm: bool = False) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=(
            ConversationType.EXTERNAL_DM if is_dm else ConversationType.EXTERNAL_GROUP
        ),
        external_channel_id=CHAT,
        external_thread_id=CHAT,
        external_message_id="71",
        sender_external_user_id="777",
        sender_display_name="Tom",
        message_text="@lemmabot when are proofs due?",
        is_dm=is_dm,
        mentioned_agent=True,
    )


def _door(
    *,
    group: SurfaceGroup | None,
    member_of_pod: bool = False,
    allow=True,
    owner_in_pod: bool = True,
):
    async def _get(*, surface_id, external_channel_id):
        return group

    async def _pod_ids(user_id):
        if user_id == OWNER:
            return [POD] if owner_in_pod else [uuid4()]
        return [POD] if member_of_pod else [uuid4()]

    async def _allow(*, group_id, sender_external_id):
        return allow

    door = OutsiderDoor(
        uow=SimpleNamespace(session=None),
        membership=SimpleNamespace(get_user_pod_ids=_pod_ids),
        limiter=SimpleNamespace(allow=_allow),
    )
    door.groups = SimpleNamespace(get=_get)
    return door


def _welcoming_group() -> SurfaceGroup:
    return SurfaceGroup(
        pod_id=POD,
        surface_id=uuid4(),
        platform="TELEGRAM",
        external_channel_id=CHAT,
        owner_user_id=OWNER,
    )


def _stranger(**overrides) -> ResolvedSurfaceUser:
    return ResolvedSurfaceUser(external_user_id="777", display_name="Tom", **overrides)


async def test_a_stranger_in_a_welcoming_group_is_an_outsider():
    group = _welcoming_group()
    door = _door(group=group)

    found = await door.group_welcoming(
        surface=_surface(), parsed=_event(), sender=_stranger()
    )

    assert found == group


async def test_a_member_is_never_an_outsider_there():
    door = _door(group=_welcoming_group(), member_of_pod=True)

    found = await door.group_welcoming(
        surface=_surface(), parsed=_event(), sender=_stranger(internal_user_id=uuid4())
    )

    assert found is None


async def test_a_lemma_user_from_another_pod_is_an_outsider_here():
    group = _welcoming_group()
    door = _door(group=group, member_of_pod=False)

    found = await door.group_welcoming(
        surface=_surface(), parsed=_event(), sender=_stranger(internal_user_id=uuid4())
    )

    assert found == group


@pytest.mark.parametrize(
    "group",
    [
        None,
        SurfaceGroup(
            pod_id=POD,
            surface_id=uuid4(),
            platform="TELEGRAM",
            external_channel_id=CHAT,
        ),
        SurfaceGroup(
            pod_id=POD,
            surface_id=uuid4(),
            platform="TELEGRAM",
            external_channel_id=CHAT,
            owner_user_id=OWNER,
            answers_outsiders=False,
        ),
    ],
    ids=["unknown-group", "nobody-answers-for-it", "switched-off"],
)
async def test_nobody_is_an_outsider_where_the_group_does_not_welcome_them(group):
    door = _door(group=group)

    assert (
        await door.group_welcoming(
            surface=_surface(), parsed=_event(), sender=_stranger()
        )
        is None
    )


async def test_a_private_chat_is_never_an_outsider_path():
    door = _door(group=_welcoming_group())

    found = await door.group_welcoming(
        surface=_surface(), parsed=_event(is_dm=True), sender=_stranger()
    )

    assert found is None


# ------------------------------------------------------------- where it runs


def _route() -> ResolvedSurfaceRoute:
    return ResolvedSurfaceRoute(
        pod_id=POD,
        agent_id=POD,
        agent_name="pod_default",
        agent_display_name="Kit",
        conversation_kind="CHANNEL",
        route_key=f"channel:{CHAT}",
    )


class _Binder:
    def __init__(self) -> None:
        self.seen: dict = {}

    async def bind_conversation(self, **kwargs):
        self.seen = kwargs
        return SimpleNamespace(conversation_id=uuid4()), "when are proofs due?"


def _surface_entity():
    return SimpleNamespace(
        id=uuid4(),
        pod_id=POD,
        surface_type=SurfacePlatform.TELEGRAM,
        name="telegram",
        account_id=None,
        config=SurfaceConfig(),
        channel_route_for=lambda **_: None,
    )


async def test_a_strangers_turn_runs_in_the_members_conversation_for_outsiders():
    group = _welcoming_group()
    binder = _Binder()

    context = await _door(group=group).prepare(
        surface=_surface_entity(),
        parsed=_event(),
        sender=_stranger(),
        group=group,
        route=_route(),
        binder=binder,
    )

    assert context is not None
    assert binder.seen["for_outsiders"] is True
    assert binder.seen["resolved_user"].internal_user_id == OWNER
    assert binder.seen["resolved_user"].external_user_id == OUTSIDERS_LINK_USER
    assert context.audience.answers_outsiders is True
    assert context.user_id == OWNER
    # Who actually asked goes on the message, for whoever reads the thread.
    assert context.message_metadata.sender_display_name == "Tom"


async def test_over_the_limit_the_stranger_is_not_answered():
    group = _welcoming_group()
    binder = _Binder()

    context = await _door(group=group, allow=False).prepare(
        surface=_surface_entity(),
        parsed=_event(),
        sender=_stranger(),
        group=group,
        route=_route(),
        binder=binder,
    )

    assert context is None
    assert binder.seen == {}


# --------------------------------------------------- nothing typed is a decision


def _outsider_context() -> SurfaceChatContext:
    return SurfaceChatContext(
        platform=SurfacePlatform.TELEGRAM,
        pod_id=POD,
        conversation_id=uuid4(),
        user_id=OWNER,
        message_text="approve",
        message_metadata=SurfaceMessageMetadata(surface_platform="TELEGRAM"),
        message_user_id=OWNER,
        event=_event(),
        audience=Audience.outsiders(),
    )


def test_a_stranger_typing_approve_resolves_nothing():
    """The conversation is the member's, so resolving a pause from here would
    record the stranger's "approve" as the member's decision -- and the approved
    call would then run with the member's authority."""
    assert may_answer_a_pause(_outsider_context()) is False
    assert may_answer_a_pause(
        _outsider_context().model_copy(update={"audience": Audience.member()})
    )


def test_a_strangers_turn_queued_before_the_audience_existed_stays_theirs():
    payload = _outsider_context().model_dump(mode="json")
    del payload["audience"]

    queued = SurfaceChatContext.model_validate({**payload, "answers_outsider": True})

    assert queued.audience == Audience.outsiders()
    assert may_answer_a_pause(queued) is False
    assert SurfaceChatContext.model_validate(payload).audience == Audience.member()


# ---------------------------------------------------------- whom it answered


def _thread(external_user_id: str, *, last_sender: str | None):
    return AgentSurfaceConversationLink(
        surface_id=uuid4(),
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_channel_id=CHAT,
        external_thread_id=CHAT,
        external_user_id=external_user_id,
        conversation_kind="CHANNEL",
        last_event={"sender_display_name": last_sender} if last_sender else {},
    )


def test_an_answer_to_a_stranger_says_it_came_from_what_is_public():
    """The group's page shows it; the owner reads what their pod said, and why."""
    answered = answered_in_group(_thread(OUTSIDERS_LINK_USER, last_sender="Tom"))

    assert (answered.name, answered.from_public) == ("Tom", True)


def test_an_answer_to_a_member_was_on_their_own_access():
    answered = answered_in_group(_thread("900100", last_sender="Arjun"))

    assert (answered.name, answered.from_public) == ("Arjun", False)


def test_an_answer_whose_asker_went_unnamed_names_nobody():
    answered = answered_in_group(_thread(OUTSIDERS_LINK_USER, last_sender=None))

    assert answered.name is None


# ------------------------------------------------------------- how often


class _Counters:
    """Redis as the limiter's counter script uses it: increment, return."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def eval(self, _script, _numkeys, key, _ttl):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]


async def test_one_person_looping_does_not_spend_the_groups_day(monkeypatch):
    from app.modules.agent_surfaces.config import surface_settings
    from app.modules.agent_surfaces.services.outsider_limits import (
        OutsiderTurnLimiter,
    )

    monkeypatch.setattr(
        surface_settings, "surface_outsider_turns_per_person_per_10_minutes", 2
    )
    monkeypatch.setattr(surface_settings, "surface_outsider_turns_per_group_per_day", 3)
    counters = _Counters()
    limiter = OutsiderTurnLimiter(redis=counters)
    group_id = uuid4()

    looping = [
        await limiter.allow(group_id=group_id, sender_external_id="777")
        for _ in range(5)
    ]

    assert looping == [True, True, False, False, False]
    # Only the two turns that ran were charged to the group, so another
    # person in it is still answered.
    assert counters.values[f"outsider:turns:{group_id}"] == 2
    assert await limiter.allow(group_id=group_id, sender_external_id="778")


async def test_a_bot_switched_off_for_outsiders_answers_none_of_them():
    door = _door(group=_welcoming_group())

    found = await door.group_welcoming(
        surface=_surface(answers_outsiders=False), parsed=_event(), sender=_stranger()
    )

    assert found is None


async def test_a_group_whose_owner_left_the_pod_answers_nobody_outside_it():
    """Every question passed on would reach somebody who can no longer act."""
    door = _door(group=_welcoming_group(), owner_in_pod=False)

    found = await door.group_welcoming(
        surface=_surface(), parsed=_event(), sender=_stranger()
    )

    assert found is None
