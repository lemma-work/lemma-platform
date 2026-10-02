"""Somebody outside the pod, writing to its bot privately.

The decisions pinned here each fail open if they are wrong:

* who takes the contact path -- never a member, only in a private chat, only
  on a bot that is the pod's own, and only when its policy is on;
* who becomes a contact -- only a handle something vouched for, and a stranger
  only where the bot answers anyone;
* unauthenticated email is never answered: a reply would go to whoever the
  ``From:`` line names;
* where the turn runs -- the contact's own conversation, owned by the member
  who looks after contacts, marked so the run authorizes as nobody.
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
from app.modules.agent_surfaces.domain.groups import contact_link_user
from app.modules.agent_surfaces.platforms.email_authentication import (
    EmailAuthenticationVerdict,
)
from app.modules.agent_surfaces.services.contacts import (
    ContactDoor,
    contact_handle,
    is_pods_own_bot,
    parked_mail_notice,
)
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.contacts.contracts import ContactRef, IdentityKind, IdentityStrength

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


def _route() -> ResolvedSurfaceRoute:
    return ResolvedSurfaceRoute(
        pod_id=POD,
        agent_id=POD,
        agent_name="pod_default",
        agent_display_name="Kit",
        conversation_kind="DM",
        route_key="dm",
    )


class _Binder:
    def __init__(self) -> None:
        self.seen: dict = {}

    async def bind_conversation(self, **kwargs):
        self.seen = kwargs
        return SimpleNamespace(conversation_id=uuid4()), "Where is my order?"


class _Limiter:
    def __init__(self, *, allow: bool = True, allow_new: bool = True) -> None:
        self._allow, self._allow_new = allow, allow_new

    async def allow(self, *, surface_id, contact_id):
        return self._allow

    async def allow_new_contact(self, *, surface_id):
        return self._allow_new


class _Book:
    """The contacts a pod knows, without a database."""

    def __init__(self) -> None:
        self.known: dict[tuple[IdentityKind, str], ContactRef] = {}
        self.opened: list[tuple[IdentityKind, str, IdentityStrength]] = []

    async def find(self, *, pod_id, kind, value):
        return self.known.get((kind, value))

    async def open(self, *, pod_id, kind, value, strength, display_name):
        ref = ContactRef(id=uuid4(), pod_id=pod_id, display_name=display_name)
        self.opened.append((kind, value, strength))
        self.known[(kind, value)] = ref
        return ref


class _ParkedMail:
    def __init__(self) -> None:
        self.told: list[tuple[str | None, object]] = []

    async def tell(self, *, surface, parsed, owner):
        self.told.append((parsed.sender_email, owner))


@pytest.fixture
def directory():
    """Who is in the pod, the contacts it knows, and what was parked."""
    return SimpleNamespace(
        members={OWNER}, book=_Book(), parked=_ParkedMail(), member_of_pod=False
    )


def _door(directory, *, member_of_pod: bool = False, limiter: _Limiter | None = None):
    async def _pod_ids(user_id):
        if user_id in directory.members or member_of_pod:
            return [POD]
        return [uuid4()]

    return ContactDoor(
        membership=SimpleNamespace(get_user_pod_ids=_pod_ids),
        book=directory.book,
        parked_mail=directory.parked,
        limiter=limiter or _Limiter(),
    )


# ------------------------------------------------------------ who takes the path


async def test_a_stranger_writing_privately_to_the_pods_own_bot_takes_the_path(
    directory,
):
    assert await _door(directory).applies(
        surface=_surface(), parsed=_event(), sender=_stranger()
    )


async def test_a_member_is_never_a_contact(directory):
    assert not await _door(directory, member_of_pod=True).applies(
        surface=_surface(),
        parsed=_event(),
        sender=_stranger(internal_user_id=uuid4()),
    )


async def test_a_lemma_user_from_another_pod_is_a_contact_here(directory):
    assert await _door(directory).applies(
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
    directory, surface, event
):
    assert not await _door(directory).applies(
        surface=surface, parsed=event, sender=_stranger()
    )


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


# ------------------------------------------------------------ who becomes one


async def test_a_strangers_first_message_makes_them_a_contact(directory):
    binder = _Binder()

    context = await _door(directory).prepare(
        surface=_surface(),
        parsed=_event(),
        sender=_stranger(),
        route=_route(),
        binder=binder,
    )

    assert context is not None
    assert [(kind, value) for kind, value, _ in directory.book.opened] == [
        (IdentityKind.PHONE, "447700900123")
    ]


async def test_a_bot_answering_known_contacts_only_ignores_strangers(directory):
    binder = _Binder()

    context = await _door(directory).prepare(
        surface=_surface(answer="known"),
        parsed=_event(),
        sender=_stranger(),
        route=_route(),
        binder=binder,
    )

    assert context is None
    assert directory.book.opened == []
    assert binder.seen == {}


async def test_a_known_contact_is_answered_by_a_bot_answering_known_contacts_only(
    directory,
):
    known = ContactRef(id=uuid4(), pod_id=POD, display_name="Dana")
    directory.book.known[(IdentityKind.PHONE, "447700900123")] = known
    binder = _Binder()

    context = await _door(directory).prepare(
        surface=_surface(answer="known"),
        parsed=_event(),
        sender=_stranger(),
        route=_route(),
        binder=binder,
    )

    assert context is not None
    assert binder.seen["for_contact"] == known.id


async def test_past_the_days_new_contacts_a_stranger_is_not_met(directory):
    context = await _door(directory, limiter=_Limiter(allow_new=False)).prepare(
        surface=_surface(),
        parsed=_event(),
        sender=_stranger(),
        route=_route(),
        binder=_Binder(),
    )

    assert context is None
    assert directory.book.opened == []


async def test_over_the_limit_a_contact_is_not_answered(directory):
    context = await _door(directory, limiter=_Limiter(allow=False)).prepare(
        surface=_surface(),
        parsed=_event(),
        sender=_stranger(),
        route=_route(),
        binder=_Binder(),
    )

    assert context is None


async def test_with_nobody_in_the_pod_looking_after_contacts_nobody_is_answered(
    directory,
):
    directory.members.clear()

    context = await _door(directory).prepare(
        surface=_surface(),
        parsed=_event(),
        sender=_stranger(),
        route=_route(),
        binder=_Binder(),
    )

    assert context is None
    assert directory.book.opened == []


# ------------------------------------------------------------ unverified email


@pytest.mark.parametrize(
    "verdict", [EmailAuthenticationVerdict.FAIL, EmailAuthenticationVerdict.UNKNOWN]
)
async def test_unauthenticated_email_is_parked_never_answered(directory, verdict):
    binder = _Binder()

    context = await _door(directory).prepare(
        surface=_surface(platform=SurfacePlatform.RESEND),
        parsed=_event(platform=SurfacePlatform.RESEND, authentication=verdict),
        sender=ResolvedSurfaceUser(external_user_id="dana@client.example"),
        route=_route(),
        binder=binder,
    )

    assert context is None
    assert binder.seen == {}
    assert directory.book.opened == []
    assert directory.parked.told == [("dana@client.example", OWNER)]


# ------------------------------------------------------------ where it runs


async def test_a_contacts_turn_runs_in_their_own_conversation_as_nobody(directory):
    binder = _Binder()

    context = await _door(directory).prepare(
        surface=_surface(),
        parsed=_event(),
        sender=_stranger(),
        route=_route(),
        binder=binder,
    )

    contact_id = binder.seen["for_contact"]
    assert binder.seen["resolved_user"].internal_user_id == OWNER
    assert binder.seen["resolved_user"].external_user_id == contact_link_user(
        contact_id
    )
    assert context is not None
    assert context.answers_outsider is True
    assert context.user_id == OWNER
    assert context.message_metadata.sender_display_name == "Dana"


async def test_a_contacts_email_reply_copies_nobody_else(directory):
    binder = _Binder()
    directory.book.known[(IdentityKind.EMAIL, "dana@client.example")] = ContactRef(
        id=uuid4(), pod_id=POD, display_name="Dana"
    )

    await _door(directory).prepare(
        surface=_surface(platform=SurfacePlatform.RESEND),
        parsed=_event(
            platform=SurfacePlatform.RESEND,
            authentication=EmailAuthenticationVerdict.PASS,
            cc=["boss@client.example"],
        ),
        sender=ResolvedSurfaceUser(external_user_id="dana@client.example"),
        route=_route(),
        binder=binder,
    )

    assert binder.seen["parsed"].reply_target["cc"] == []


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
