"""Who, in a Slack workspace the pod's organization holds, is one of its people.

The predicate itself (``held_for_pods``) reads two tables and is exercised end
to end in ``test_slack_workspace_contacts_e2e``; here it is a stand-in, and
what is pinned is everything decided around it: whose team counts, that a
private chat wants Slack's own word that somebody is no guest, and that a new
bot's default never overrides a policy somebody set, nor names a keeper who
may not read contacts.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.models import SurfaceSenderProfile
from app.modules.agent_surfaces.domain.surface_config import ContactAnswer
from app.modules.agent_surfaces.services.slack_workspace_people import (
    WorkspacePeople,
    slack_guest,
)

pytestmark = pytest.mark.unit

POD = uuid4()
HOME = "T0HOME"
_MODULE = "app.modules.agent_surfaces.services.slack_workspace_people"


@pytest.fixture(autouse=True)
def held(monkeypatch):
    """Which workspaces Lemma's app holds for the pod's organization."""
    teams = {HOME}

    async def held_for_pods(uow, team_id, pod_ids):
        return team_id in teams and set(pod_ids) == {POD}

    monkeypatch.setattr(f"{_MODULE}.held_for_pods", held_for_pods)
    return teams


def _people() -> WorkspacePeople:
    return WorkspacePeople(SimpleNamespace(session=None))


def _surface(*, workspace: str = HOME, pod_id: UUID = POD) -> AgentSurfaceEntity:
    return AgentSurfaceEntity.create(
        pod_id=pod_id,
        surface_type=SurfacePlatform.SLACK,
        name="slack",
        agent_id=uuid4(),
        account_id=uuid4(),
        external_workspace_id=workspace,
    )


def _said(*, team: str | None = HOME, tenant: str = HOME) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.SLACK,
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id=tenant,
        external_channel_id="D1",
        external_thread_id="D1",
        external_message_id="m1",
        sender_external_user_id="U777",
        message_text="hello",
        is_dm=True,
        metadata={"sender_team_id": team} if team else {},
    )


def _sender(guest: bool | None) -> ResolvedSurfaceUser:
    return ResolvedSurfaceUser(external_user_id="U777", is_guest=guest)


@pytest.mark.parametrize(
    ("raw", "guest"),
    [
        ({"is_restricted": False, "is_ultra_restricted": False}, False),
        ({"is_restricted": True, "is_ultra_restricted": False}, True),
        ({"is_restricted": False, "is_ultra_restricted": True}, True),
        ({"is_restricted": False}, None),
        ({}, None),
    ],
    ids=["member", "multi-channel-guest", "single-channel-guest", "half", "none"],
)
def test_only_slacks_own_word_makes_somebody_a_member_or_a_guest(raw, guest):
    assert slack_guest(SurfaceSenderProfile(raw_profile=raw)) is guest


def test_no_profile_says_nothing():
    assert slack_guest(None) is None


async def test_a_colleague_writing_privately_is_one_of_the_workspaces_people():
    handle = await _people().colleague_handle(
        _surface(), _said(), _sender(False), private=True
    )

    assert handle is not None and handle.value == f"{HOME}:U777"


@pytest.mark.parametrize("guest", [True, None], ids=["guest", "slack-said-nothing"])
async def test_a_private_chat_is_closed_to_anybody_slack_does_not_call_a_member(guest):
    assert (
        await _people().colleague_handle(
            _surface(), _said(), _sender(guest), private=True
        )
        is None
    )


async def test_a_guest_is_one_of_them_in_a_group():
    handle = await _people().colleague_handle(
        _surface(), _said(), _sender(True), private=False
    )

    assert handle is not None


@pytest.mark.parametrize(
    ("team", "workspace", "is_held"),
    [
        ("T0OTHER", HOME, True),
        (None, HOME, True),
        (HOME, "T0OTHER", True),
        (HOME, HOME, False),
    ],
    ids=["another-team", "team-unsaid", "bot-elsewhere", "not-held"],
)
async def test_nobody_else_is(held, team, workspace, is_held):
    if not is_held:
        held.clear()

    handle = await _people().colleague_handle(
        _surface(workspace=workspace), _said(team=team), _sender(False), private=False
    )

    assert handle is None


async def test_a_bot_whose_pod_is_in_another_organization_answers_nobody_here():
    """The holding check is asked for this bot's pod, not for anybody's."""
    handle = await _people().colleague_handle(
        _surface(pod_id=uuid4()), _said(), _sender(False), private=True
    )

    assert handle is None


# ------------------------------------------------------- a new bot's default


@pytest.fixture
def may_look_after(monkeypatch):
    """Who connected the bot, and who holds ``contact.read`` in the pod."""
    allowed: set[UUID] = set()
    connector = uuid4()

    async def holds(uow, pod_id, user_id, permission_id):
        return pod_id == POD and user_id in allowed

    class _Accounts:
        def __init__(self, uow):
            pass

        async def list_account_summaries(self, account_ids):
            return {
                account_id: SimpleNamespace(user_id=connector)
                for account_id in account_ids
            }

    monkeypatch.setattr(f"{_MODULE}.pod_member_holds", holds)
    monkeypatch.setattr(f"{_MODULE}.SqlAlchemySurfaceAccountAdapter", _Accounts)
    return SimpleNamespace(allowed=allowed, connector=connector)


async def test_a_new_bot_in_a_held_workspace_answers_anyone_for_its_connector(
    may_look_after,
):
    surface = _surface()
    may_look_after.allowed.add(may_look_after.connector)

    await _people().answer_by_default(surface)

    assert surface.config.contacts.answer is ContactAnswer.ANYONE
    assert surface.config.contacts.looked_after_by == may_look_after.connector


async def test_nobody_is_made_to_look_after_contacts_who_may_not_read_them(
    may_look_after,
):
    surface = _surface()

    await _people().answer_by_default(surface)

    assert surface.config.contacts.answer is ContactAnswer.OFF


@pytest.mark.parametrize(
    "chosen",
    [
        {"answer": "known"},
        {"answer": "off", "looked_after_by": str(POD)},
    ],
    ids=["chose-known", "named-a-keeper"],
)
async def test_a_policy_somebody_set_is_left_as_it_came(may_look_after, chosen):
    surface = _surface()
    may_look_after.allowed.add(may_look_after.connector)
    surface.config = SurfaceConfig.model_validate({"contacts": chosen})
    before = surface.config.contacts.model_copy()

    await _people().answer_by_default(surface)

    assert surface.config.contacts == before


async def test_a_bot_in_a_workspace_nobody_holds_keeps_off(held, may_look_after):
    surface = _surface()
    may_look_after.allowed.add(may_look_after.connector)
    held.clear()

    await _people().answer_by_default(surface)

    assert surface.config.contacts.answer is ContactAnswer.OFF


@pytest.mark.parametrize(
    ("chosen", "config_given"),
    [(True, False), (None, True)],
    ids=["sent-off", "brought-a-config-and-did-not-say"],
)
async def test_off_sent_on_purpose_is_kept(may_look_after, chosen, config_given):
    """Its value is the default's, so only the creator can say it was sent."""
    surface = _surface()
    may_look_after.allowed.add(may_look_after.connector)

    await _people().answer_by_default(surface, chosen=chosen, config_given=config_given)

    assert surface.config.contacts.answer is ContactAnswer.OFF
