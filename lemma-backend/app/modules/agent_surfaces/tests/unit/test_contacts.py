"""Somebody outside the pod, writing to its bot privately.

The decisions pinned here each fail open if they are wrong:

* who takes the contact path -- never a member, only in a private chat, only
  on a bot that is the pod's own, and only when its policy is on;
* whose handle counts -- only one something vouched for, and never one with
  nothing left once normalised.

What the door then decides is pinned against the real tables in
``tests/e2e/test_contact_door_e2e.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.email_authentication import (
    EmailAuthenticationVerdict,
)
from app.modules.agent_surfaces.services.contacts import (
    ContactDoor,
    contact_handle,
    is_pods_own_bot,
    parked_mail_notice,
)
from app.modules.contacts.contracts import IdentityKind

pytestmark = pytest.mark.unit

POD = uuid4()
OWNER = uuid4()


def _surface(
    *,
    answer: str = "anyone",
    looked_after_by=OWNER,
    platform: SurfacePlatform = SurfacePlatform.WHATSAPP,
    own: bool = True,
):
    config = SurfaceConfig.model_validate(
        {"contacts": {"answer": answer, "looked_after_by": looked_after_by}}
    )
    return SimpleNamespace(
        id=uuid4(),
        pod_id=POD,
        name="support",
        surface_type=platform,
        account_id=uuid4() if own else None,
        credential_mode=SurfaceCredentialMode.SYSTEM,
        surface_identity_email="help@acme.example",
        config=config,
        channel_route_for=lambda **_: None,
    )


def _event(
    *,
    platform: SurfacePlatform = SurfacePlatform.WHATSAPP,
    is_dm: bool = True,
    authentication: EmailAuthenticationVerdict | None = None,
    cc: list[str] | None = None,
) -> ParsedInboundSurfaceEvent:
    email = platform.is_email
    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=(
            ConversationType.EXTERNAL_DM if is_dm else ConversationType.EXTERNAL_GROUP
        ),
        external_channel_id="chan",
        external_thread_id="thread",
        external_message_id="m1",
        sender_external_user_id="dana@client.example" if email else "447700900123",
        sender_email="dana@client.example" if email else None,
        sender_phone=None if email else "447700900123",
        sender_display_name="Dana",
        sender_authentication=authentication.value if authentication else None,
        message_text="Where is my order?",
        metadata={"subject": "Order 1182"},
        reply_target={"cc": cc} if cc is not None else {},
        is_dm=is_dm,
        mentioned_agent=True,
    )


def _stranger(**overrides) -> ResolvedSurfaceUser:
    return ResolvedSurfaceUser(
        external_user_id="447700900123", display_name="Dana", **overrides
    )


def _door(*, member_of_pod: bool = False) -> ContactDoor:
    """The door as ingress builds it; ``applies`` reads only who is in the pod."""

    async def _pod_ids(user_id):
        if user_id == OWNER or member_of_pod:
            return [POD]
        return [uuid4()]

    return ContactDoor(
        uow=SimpleNamespace(), membership=SimpleNamespace(get_user_pod_ids=_pod_ids)
    )


# ------------------------------------------------------------ who takes the path


async def test_a_stranger_writing_privately_to_the_pods_own_bot_takes_the_path():
    assert await _door().applies(
        surface=_surface(), parsed=_event(), sender=_stranger()
    )


async def test_a_member_is_never_a_contact():
    assert not await _door(member_of_pod=True).applies(
        surface=_surface(),
        parsed=_event(),
        sender=_stranger(internal_user_id=uuid4()),
    )


async def test_a_lemma_user_from_another_pod_is_a_contact_here():
    assert await _door().applies(
        surface=_surface(),
        parsed=_event(),
        sender=_stranger(internal_user_id=uuid4()),
    )


@pytest.mark.parametrize(
    ("surface", "event"),
    [
        (_surface(answer="off"), _event()),
        (_surface(own=False), _event()),
        (_surface(), _event(is_dm=False)),
        (
            _surface(platform=SurfacePlatform.SLACK),
            _event(platform=SurfacePlatform.SLACK),
        ),
    ],
    ids=["switched-off", "shared-bot", "group", "workspace-chat"],
)
async def test_nobody_is_a_contact_where_the_bot_does_not_answer_contacts(
    surface, event
):
    assert not await _door().applies(surface=surface, parsed=event, sender=_stranger())


def test_an_email_address_always_belongs_to_the_pod_that_receives_on_it():
    surface = _surface(platform=SurfacePlatform.RESEND, own=False)
    surface.account_id = None

    assert is_pods_own_bot(surface)


def test_a_bot_on_the_pods_own_credentials_is_its_own():
    surface = _surface(own=False)
    surface.credential_mode = SurfaceCredentialMode.CUSTOM

    assert is_pods_own_bot(surface)


def test_only_an_authenticated_email_address_is_vouched_for():
    for verdict, vouched in (
        (EmailAuthenticationVerdict.PASS, True),
        (EmailAuthenticationVerdict.FAIL, False),
        (EmailAuthenticationVerdict.UNKNOWN, False),
        (None, False),
    ):
        handle = contact_handle(
            _event(platform=SurfacePlatform.RESEND, authentication=verdict)
        )
        assert handle is not None
        assert handle.kind is IdentityKind.EMAIL
        assert handle.vouched_for is vouched


# ------------------------------------------------------------ unverified email


def test_the_parked_mail_notice_names_the_sender_and_asks_nothing():
    surface = _surface(platform=SurfacePlatform.RESEND)
    notice = parked_mail_notice(
        surface=surface,
        parsed=_event(
            platform=SurfacePlatform.RESEND,
            authentication=EmailAuthenticationVerdict.FAIL,
        ),
        owner=OWNER,
        member_id=uuid4(),
    )

    assert notice.recipient_user_id == OWNER
    assert notice.title == "Unverified email from dana@client.example"
    assert "Subject: Order 1182" in notice.body
    assert notice.expects_response is False


# ------------------------------------------------------------ handles


async def test_a_handle_with_nothing_left_once_normalised_names_nobody():
    event = _event().model_copy(
        update={"sender_phone": "+ -", "sender_external_user_id": "+ -"}
    )

    assert contact_handle(event) is None
    assert not await _door().applies(
        surface=_surface(), parsed=event, sender=_stranger()
    )
