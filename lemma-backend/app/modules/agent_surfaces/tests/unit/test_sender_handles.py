"""The handle somebody outside the pod is a contact by, read off what they sent."""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.sender_handles import (
    group_handle,
    private_handle,
    workspace_handle,
)
from app.modules.contacts.contracts import IdentityKind

pytestmark = pytest.mark.unit


def _event(
    platform: SurfacePlatform,
    *,
    is_dm: bool,
    sender: str = "U777",
    phone: str | None = None,
    **metadata: object,
) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=(
            ConversationType.EXTERNAL_DM if is_dm else ConversationType.EXTERNAL_GROUP
        ),
        external_channel_id="C1",
        external_thread_id="C1",
        external_message_id="m1",
        sender_external_user_id=sender,
        sender_phone=phone,
        message_text="hello",
        is_dm=is_dm,
        metadata=metadata,
    )


@pytest.mark.parametrize(
    ("platform", "phone", "kind", "value"),
    [
        (
            SurfacePlatform.WHATSAPP,
            "+44 7700 900123",
            IdentityKind.PHONE,
            "+447700900123",
        ),
        (SurfacePlatform.TELEGRAM, None, IdentityKind.TELEGRAM, "U777"),
    ],
    ids=["whatsapp", "telegram"],
)
def test_a_chat_platform_names_its_sender_the_same_in_a_group_and_in_private(
    platform, phone, kind, value
):
    in_private = private_handle(_event(platform, is_dm=True, phone=phone))
    in_group = group_handle(_event(platform, is_dm=False, phone=phone))

    assert in_private == in_group
    assert in_group is not None
    assert (in_group.kind, in_group.value, in_group.vouched_for) == (kind, value, True)


def test_a_slack_sender_from_the_other_company_is_known_by_their_team_and_user():
    event = _event(
        SurfacePlatform.SLACK,
        is_dm=False,
        sender_is_external=True,
        sender_team_id="T999",
    )

    handle = group_handle(event)

    assert handle is not None
    assert (handle.kind, handle.value) == (IdentityKind.SLACK, "T999:U777")


@pytest.mark.parametrize(
    "metadata",
    [{}, {"sender_is_external": True}, {"sender_team_id": "T999"}],
    ids=["a-colleague", "no-team", "not-marked-external"],
)
def test_a_slack_sender_from_the_pods_own_workspace_is_never_a_contact(metadata):
    """Not by ``group_handle``, which the group log asks before anybody knows
    whether the sender is in the pod."""
    assert group_handle(_event(SurfacePlatform.SLACK, is_dm=False, **metadata)) is None


@pytest.mark.parametrize("is_dm", [True, False])
def test_a_slack_colleague_is_named_by_their_own_team_and_user(is_dm):
    event = _event(SurfacePlatform.SLACK, is_dm=is_dm, sender_team_id="T0HOME")

    handle = workspace_handle(event)

    assert handle is not None
    assert (handle.kind, handle.value, handle.vouched_for) == (
        IdentityKind.SLACK,
        "T0HOME:U777",
        True,
    )


def test_one_person_is_one_handle_whether_external_or_not():
    """The other company's person in a shared channel and in a DM are one."""
    shared = _event(
        SurfacePlatform.SLACK,
        is_dm=False,
        sender_is_external=True,
        sender_team_id="T999",
    )
    private = _event(SurfacePlatform.SLACK, is_dm=True, sender_team_id="T999")

    assert workspace_handle(private) == group_handle(shared)


@pytest.mark.parametrize(
    ("platform", "metadata"),
    [
        (SurfacePlatform.SLACK, {}),
        (SurfacePlatform.TEAMS, {"sender_team_id": "T0HOME"}),
    ],
    ids=["slack-names-no-team", "not-slack"],
)
def test_no_workspace_handle_without_a_slack_team(platform, metadata):
    assert workspace_handle(_event(platform, is_dm=True, **metadata)) is None


def test_a_private_message_on_slack_is_a_colleagues_not_a_contacts():
    event = _event(
        SurfacePlatform.SLACK, is_dm=True, sender_is_external=True, sender_team_id="T9"
    )

    assert private_handle(event) is None


def test_a_number_whatsapp_vouches_for_is_international_whatever_the_pods_region(
    monkeypatch,
):
    """Meta sends ``4155550100`` for +41 55 555 01 00. Read in a US pod's
    region it was +1 415 555 0100 -- a different person's contact."""
    from app.modules.contacts.config import contacts_settings

    monkeypatch.setattr(contacts_settings, "contacts_default_phone_region", "US")
    event = _event(SurfacePlatform.WHATSAPP, is_dm=True, phone="4155550100")

    handle = private_handle(event)

    assert handle is not None
    assert (handle.kind, handle.value) == (IdentityKind.PHONE, "+4155550100")


def test_a_whatsapp_group_sender_with_no_number_is_no_phone_handle():
    """A participant WhatsApp names only by a business-scoped user id: that id
    is not a phone number, and read as one it named a stranger."""
    event = _event(
        SurfacePlatform.WHATSAPP, is_dm=False, sender="US.13491208655302741918"
    )

    assert group_handle(event) is None
