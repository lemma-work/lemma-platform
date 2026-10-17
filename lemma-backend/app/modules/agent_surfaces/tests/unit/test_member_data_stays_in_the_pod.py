"""A member's files and rows are not posted where people outside the pod read.

A member's run in a group reads with that member's authority. Before, a table
it displayed went to the whole chat -- a Slack Connect channel's other company,
a group opened to strangers. Now the chat gets a note and a link that opens only
in Lemma, and the contents are never read for it.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.contracts import DisplayResourceRequest, DisplayResourceType
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
)
from app.modules.agent_surfaces.services.channel_exposure import outsiders_would_see
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    AnsweredAt,
    _delivering_adapter,
    _slack_event,
    _slack_surface,
    build_egress,
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio


def _channel(*, shared: bool):
    surface = _slack_surface()
    conversation_id = uuid4()
    event = _slack_event().model_copy(
        update={
            "is_dm": False,
            "conversation_type": ConversationType.EXTERNAL_GROUP,
            "external_channel_id": "C-SHARED",
            "metadata": {"is_ext_shared_channel": shared},
        }
    )
    link = AnsweredAt(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id="C-SHARED",
        external_thread_id=event.external_thread_id,
        external_user_id=event.sender_external_user_id,
        conversation_kind="CHANNEL",
        last_event=event.model_dump(mode="json"),
    )
    return surface, conversation_id, event, link


@pytest.mark.parametrize(
    "request_",
    [
        DisplayResourceRequest(type=DisplayResourceType.TABLE, name="deals"),
        DisplayResourceRequest(type=DisplayResourceType.FILE, path="/me/payroll.csv"),
    ],
    ids=["table", "file"],
)
async def test_a_shared_channel_gets_a_link_not_the_contents(request_):
    """Reaching the rows or the file's bytes would need the pod's storage, which
    these doubles do not have: arriving at all is what the test would catch."""
    surface, conversation_id, _event, link = _channel(shared=True)
    adapter = _delivering_adapter()
    egress = build_egress(adapter=adapter, surfaces=[surface], answered=link)

    sent = await egress.send_display_resource_for_conversation(
        conversation_id=conversation_id, request=request_, tool_call_id="t-1"
    )

    assert sent is True
    adapter._render_file.assert_not_awaited()
    adapter._render_resource.assert_awaited_once()
    note = adapter.send_message.await_args.kwargs["message"]
    assert "outside the pod" in note


def _target(surface, event, link, **group) -> SimpleNamespace:
    """The target's place is the group, with the settings it keeps."""
    source = link.source(surface).model_copy(update=group)
    return SimpleNamespace(
        surface=surface, source=source, event=event, is_group=link.is_group
    )


@pytest.mark.parametrize(
    ("group", "outsiders"),
    [
        ({"answers_outsiders": True, "owner_user_id": uuid4()}, True),
        ({"shared_externally": True, "answers_outsiders": False}, True),
        ({"answers_outsiders": False}, False),
        ({"answers_outsiders": True, "owner_user_id": None}, False),
    ],
    ids=["opened-to-outsiders", "shared-outside", "members-only", "nobody-answers"],
)
def test_what_the_pod_knows_about_the_group_decides(group, outsiders):
    surface, _conversation_id, event, link = _channel(shared=False)

    seen = outsiders_would_see(
        _target(surface, event, link, **group),  # type: ignore[arg-type]
        DisplayResourceRequest(type=DisplayResourceType.TABLE, name="deals"),
    )

    assert seen is outsiders


def test_a_card_is_not_member_data():
    """Only what is read with the member's authority is held back."""
    surface, _conversation_id, event, link = _channel(shared=True)

    assert not outsiders_would_see(
        _target(surface, event, link),  # type: ignore[arg-type]
        DisplayResourceRequest(type=DisplayResourceType.APP, name="crm"),
    )
