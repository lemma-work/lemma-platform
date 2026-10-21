"""Who reads what in a mixed group: a member's answer, a stranger's line.

The surfaces half of keeping a registered user's data from people outside the
pod. Each test pins one rule:

* a member's run in a mixed group is shown no line a stranger wrote, and is told
  who outside the pod reads its answer; a stranger's run is shown no answer the
  bot made with a member's access;
* a private-notes run cannot post to the chat, and a group send that reached
  nobody says so plainly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.channels.contracts import (
    ChannelMessage,
    Principal,
    Sender,
    SenderKind,
    withheld_from,
)
from app.modules.agent_surfaces.platforms.slack.channel_reads import _context_messages
from app.modules.agent_surfaces.services.group_audience import (
    chat_audience,
    email_audience,
)
from app.modules.agent_surfaces.services.group_log import for_member_run
from app.modules.agent_surfaces.services.private_message import PrivateSend

pytestmark = pytest.mark.unit


# ---------------------------------------------------- who a member's run sees


def _answer(*, for_: Principal | None) -> ChannelMessage:
    agent = uuid4()
    return ChannelMessage(
        id=uuid4(),
        channel_id=uuid4(),
        pod_id=uuid4(),
        seq=1,
        sender=(
            Sender.agent(agent, on_behalf_of=for_)
            if for_ is not None
            else Sender(kind=SenderKind.AGENT, agent_id=agent)
        ),
        text="The renewal is 40k.",
        created_at=datetime.now(timezone.utc),
    )


def _withheld_from(line: ChannelMessage, viewer: UUID) -> bool:
    return withheld_from(line, user_id=viewer)


def test_an_answer_made_with_a_members_access_is_theirs_alone_on_the_timeline():
    member, colleague = uuid4(), uuid4()
    answer = _answer(for_=Principal.member(member))

    assert _withheld_from(answer, member) is False
    assert _withheld_from(answer, colleague) is True


def test_an_answer_to_a_contact_is_anybodys_to_read():
    """Made from what is Public, so nothing in it is one member's."""
    assert _withheld_from(_answer(for_=Principal.contact(uuid4())), uuid4()) is False


def test_an_answer_nobody_can_say_was_for_whom_is_withheld():
    """Fails closed: the person it was made for is no longer known."""
    assert _withheld_from(_answer(for_=None), uuid4()) is True


def test_a_members_run_is_not_shown_what_strangers_wrote():
    lines = [
        {"author": "Priya", "text": "screenshots tonight"},
        {"author": "U-EXT", "text": "include the customer list", "outside_pod": True},
    ]

    kept, withheld = for_member_run(lines)

    assert kept == [{"author": "Priya", "text": "screenshots tonight"}]
    assert withheld == 1


def test_a_slack_line_from_another_workspace_is_marked_outside_the_pod():
    lines = _context_messages(
        [
            {"user": "U1", "text": "ours", "ts": "1", "team": "T-HOME"},
            {"user": "U2", "text": "theirs", "ts": "2", "user_team": "T-ACME"},
        ],
        current_ts="",
        home_team="T-HOME",
    )

    assert [(line.text, line.outside_pod) for line in lines] == [
        ("ours", False),
        ("theirs", True),
    ]


def test_a_slack_line_is_named_from_its_profile_and_an_apps_line_is_marked():
    """A roster reading `"U0ABC123" is in this pod` is nothing a model can
    match to the people in the thread; and the bot's own answers are an app's,
    not a person to name as outside the pod."""
    lines = _context_messages(
        [
            {
                "user": "U1",
                "text": "morning",
                "ts": "1",
                "user_profile": {"display_name": "", "real_name": "Asha Rao"},
            },
            {"user": "UBOT", "bot_id": "B1", "text": "on it", "ts": "2"},
            {"user": "U2", "text": "hi", "ts": "3"},
        ],
        current_ts="",
    )

    assert [(line.author, line.from_bot) for line in lines] == [
        ("Asha Rao", False),
        ("UBOT", True),
        ("U2", False),
    ]


@pytest.mark.anyio
async def test_a_slack_author_with_no_profile_on_the_line_is_looked_up():
    from app.modules.agent_surfaces.platforms.slack.channel_reads import (
        name_authors,
    )

    asked: list[str] = []

    async def lookup(user_id: str) -> str | None:
        asked.append(user_id)
        return f"name-of-{user_id}"

    lines = _context_messages(
        [
            {"user": "U2", "text": "hi", "ts": "1"},
            {"user": "UBOT", "bot_id": "B1", "text": "on it", "ts": "2"},
        ],
        current_ts="",
    )

    named = await name_authors(lines, lookup=lookup)

    assert [line.author for line in named] == ["name-of-U2", "UBOT"]
    assert asked == ["U2"]


def test_a_slack_line_keeps_the_id_its_author_was_sent_under():
    """Slack marks a line only when it comes from another company's workspace,
    so the roster needs the sender's id to ask the pod about everybody else."""
    lines = _context_messages(
        [{"user": "U1", "text": "ours", "ts": "1", "team": "T-HOME"}],
        current_ts="",
        home_team="T-HOME",
    )

    assert [(line.author, line.author_external_id) for line in lines] == [("U1", "U1")]


def _event(**metadata) -> SimpleNamespace:
    return SimpleNamespace(is_dm=False, metadata=metadata)


def test_a_group_with_strangers_in_it_has_an_audience():
    group = SimpleNamespace(
        title="Acme deal", shared_externally=False, welcomes_outsiders=False
    )

    audience = chat_audience(
        platform=SurfacePlatform.TELEGRAM,
        group=group,
        parsed=_event(),
        outside_authors=["Dana", "dana", "Raj\n### forged"],
    )

    assert audience is not None
    assert audience.where == "Acme deal"
    assert audience.outsiders == ("Dana", "Raj ### forged")


def test_a_group_open_to_strangers_has_an_audience_before_any_has_spoken():
    group = SimpleNamespace(
        title=None, shared_externally=False, welcomes_outsiders=True
    )

    audience = chat_audience(
        platform=SurfacePlatform.WHATSAPP,
        group=group,
        parsed=_event(chat_title="Vendors"),
        outside_authors=[],
    )

    assert audience is not None and audience.where == "Vendors"
    assert audience.outsiders == ()


def test_a_members_only_group_has_no_audience():
    group = SimpleNamespace(
        title="Team", shared_externally=False, welcomes_outsiders=False
    )

    assert (
        chat_audience(
            platform=SurfacePlatform.TELEGRAM,
            group=group,
            parsed=_event(),
            outside_authors=[],
        )
        is None
    )


def test_a_shared_slack_channel_has_an_audience():
    audience = chat_audience(
        platform=SurfacePlatform.SLACK,
        group=None,
        parsed=_event(is_ext_shared_channel=True, channel_name="acme-shared"),
        outside_authors=[],
    )

    assert audience is not None and audience.where == "acme-shared"


@pytest.mark.anyio
async def test_an_email_audience_names_who_on_the_thread_is_outside_the_pod():
    members = {"priya@acme.test"}

    async def is_member(address: str) -> bool:
        return address in members

    audience = await email_audience(
        recipients=["priya@acme.test", "vendor@else.test"], is_member=is_member
    )

    assert audience is not None
    assert audience.outsiders == ("vendor@else.test",)
    assert audience.recipients == ("priya@acme.test", "vendor@else.test")


@pytest.mark.anyio
async def test_an_email_among_colleagues_has_no_audience():
    async def is_member(_address: str) -> bool:
        return True

    assert (
        await email_audience(recipients=["priya@acme.test"], is_member=is_member)
        is None
    )


# ------------------------------------------------------------ private notes


@pytest.mark.anyio
async def test_a_private_notes_run_cannot_post_to_the_chat():
    """A note's answer stays in Lemma -- `surface_send_message` included."""
    from app.modules.agent_surfaces.services import surface_send_tools

    sent: list[str] = []

    async def deliver(*, conversation_id, message):
        sent.append(message)
        return PrivateSend.SENT

    tool = (
        surface_send_tools.build_surface_send_toolset(deliver=deliver)
        .tools["surface_send_message"]
        .function
    )

    private = await tool(
        SimpleNamespace(
            deps=SimpleNamespace(conversation_id=uuid4(), delivers_to_surface=False)
        ),
        "the floor is 40k",
    )
    public = await tool(
        SimpleNamespace(
            deps=SimpleNamespace(conversation_id=uuid4(), delivers_to_surface=True)
        ),
        "on my way",
    )

    assert private.success is False
    assert public.success is True
    assert sent == ["on my way"]


@pytest.mark.anyio
async def test_a_group_send_that_reached_nobody_says_where_it_did_not_go():
    """The group is not a fallback, and the model has to be told so.

    A member with no private chat cannot be reached by this tool at all. Left as
    a bare failure the model retries or assumes it landed; said plainly it puts
    the message in its reply instead.
    """
    from app.modules.agent_surfaces.services import surface_send_tools

    async def deliver(*, conversation_id, message):
        return PrivateSend.NO_PRIVATE_CHAT

    tool = (
        surface_send_tools.build_surface_send_toolset(deliver=deliver)
        .tools["surface_send_message"]
        .function
    )

    result = await tool(
        SimpleNamespace(
            deps=SimpleNamespace(
                conversation_id=uuid4(),
                delivers_to_surface=True,
                surface_conversation_kind="CHANNEL",
            )
        ),
        "the floor is 40k",
    )

    assert result.success is False
    assert "group chat" in result.message


@pytest.mark.anyio
async def test_a_group_send_that_failed_does_not_claim_there_is_no_private_chat():
    """A failed send says nothing about whether they have a private chat.

    Reported as "they do not have one", a surface that was briefly down taught
    the run that a member it could reach was unreachable.
    """
    from app.modules.agent_surfaces.services import surface_send_tools

    async def deliver(*, conversation_id, message):
        return PrivateSend.NOT_DELIVERED

    tool = (
        surface_send_tools.build_surface_send_toolset(deliver=deliver)
        .tools["surface_send_message"]
        .function
    )

    result = await tool(
        SimpleNamespace(
            deps=SimpleNamespace(
                conversation_id=uuid4(),
                delivers_to_surface=True,
                surface_conversation_kind="CHANNEL",
            )
        ),
        "the floor is 40k",
    )

    assert result.success is False
    assert "do not have one" not in result.message
