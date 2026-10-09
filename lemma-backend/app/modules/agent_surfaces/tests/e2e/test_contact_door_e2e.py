"""What the contact path decides about a private message from outside the pod.

Against the real contacts tables, notifications and pod membership, with only
the conversation binder (whose own tests live elsewhere) and, where a test is
about a limit, the turn limiter standing in. The decisions pinned here each
fail open if they are wrong:

* who becomes a contact -- only a handle something vouched for, and a stranger
  only where the bot answers anyone; a bot for known contacts refuses them once
  a day;
* unauthenticated email is never answered but parked, a few notes an hour;
* mail a machine sent is neither answered nor parked;
* "STOP" unsubscribes, and is not the contact opting back in;
* where the turn runs -- the contact's own conversation, owned by the member
  who looks after contacts, marked so the run authorizes as nobody.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.groups import contact_link_user
from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.models import NotificationModel
from app.modules.agent_surfaces.platforms.email_authentication import (
    EmailAuthenticationVerdict,
)
from app.modules.agent_surfaces.services.contact_windows import ContactWindows
from app.modules.agent_surfaces.services.contacts import ContactDoor
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.contacts.contracts import (
    IdentityKind,
    IdentityStrength,
    open_contact,
)
from app.modules.contacts.infrastructure.models import ContactIdentityModel

pytestmark = pytest.mark.e2e

PHONE = "447700900123"
EMAIL = "dana@client.example"


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


class _FailingRedis:
    async def set(self, *args, **kwargs):
        raise RedisError("connection refused")

    async def eval(self, *args, **kwargs):
        raise RedisError("connection refused")


class _Pod:
    """A real pod and its owner, and the door as ingress builds it."""

    def __init__(self, db_session: AsyncSession, pod_id: UUID, owner: UUID) -> None:
        self.db = db_session
        self.uow = SqlAlchemyUnitOfWork(db_session)
        self.pod_id = pod_id
        self.owner = owner

    def surface(
        self,
        *,
        answer: str = "anyone",
        looked_after_by: UUID | None = None,
        platform: SurfacePlatform = SurfacePlatform.WHATSAPP,
    ):
        config = SurfaceConfig.model_validate(
            {
                "contacts": {
                    "answer": answer,
                    "looked_after_by": looked_after_by or self.owner,
                }
            }
        )
        return SimpleNamespace(
            id=uuid4(),
            pod_id=self.pod_id,
            name="support",
            surface_type=platform,
            account_id=uuid4(),
            credential_mode=SurfaceCredentialMode.SYSTEM,
            surface_identity_email="help@acme.example",
            config=config,
        )

    def route(self) -> ResolvedSurfaceRoute:
        return ResolvedSurfaceRoute(
            pod_id=self.pod_id,
            agent_id=self.pod_id,
            agent_name="pod_default",
            agent_display_name="Kit",
            conversation_kind="DM",
            route_key="dm",
        )

    def door(self, *, limiter: _Limiter | None = None, redis=None) -> ContactDoor:
        return ContactDoor(
            uow=self.uow,
            membership=SqlAlchemySurfaceRoutingResolutionAdapter(self.uow),
            limiter=limiter or _Limiter(),
            windows=ContactWindows(redis=redis) if redis else None,
        )

    async def prepare(self, surface, event, *, binder=None, **door):
        return await self.door(**door).prepare(
            surface=surface,
            parsed=event,
            sender=ResolvedSurfaceUser(
                external_user_id=event.sender_external_user_id, display_name="Dana"
            ),
            route=self.route(),
            binder=binder or _Binder(),
        )

    async def known(self, kind: IdentityKind, value: str):
        return await open_contact(
            self.uow,
            pod_id=self.pod_id,
            kind=kind,
            value=value,
            strength=IdentityStrength.CHANNEL,
            display_name="Dana",
        )

    async def handles(self) -> list[ContactIdentityModel]:
        return list(
            await self.db.scalars(
                select(ContactIdentityModel).where(
                    ContactIdentityModel.pod_id == self.pod_id
                )
            )
        )

    async def notes(self) -> list[str]:
        return list(
            await self.db.scalars(
                select(NotificationModel.title)
                .where(NotificationModel.pod_id == self.pod_id)
                .order_by(NotificationModel.created_at)
            )
        )


@pytest.fixture
def pod(db_session: AsyncSession, test_pod, fixed_test_user) -> _Pod:
    return _Pod(db_session, UUID(test_pod["id"]), UUID(fixed_test_user["id"]))


def _event(
    *,
    platform: SurfacePlatform = SurfacePlatform.WHATSAPP,
    authentication: EmailAuthenticationVerdict | None = None,
    cc: list[str] | None = None,
    text: str = "Where is my order?",
    sender: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ParsedInboundSurfaceEvent:
    email = platform.is_email
    handle = sender or (EMAIL if email else PHONE)
    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="chan",
        external_thread_id="thread",
        external_message_id="m1",
        sender_external_user_id=handle,
        sender_email=handle if email else None,
        sender_phone=None if email else handle,
        sender_display_name="Dana",
        sender_authentication=authentication.value if authentication else None,
        message_text=text,
        metadata=metadata if metadata is not None else {"subject": "Order 1182"},
        reply_target={"cc": cc} if cc is not None else {},
        is_dm=True,
        mentioned_agent=True,
    )


def _email(**overrides) -> ParsedInboundSurfaceEvent:
    return _event(
        platform=SurfacePlatform.RESEND,
        authentication=overrides.pop("authentication", EmailAuthenticationVerdict.PASS),
        **overrides,
    )


# ------------------------------------------------------------ who becomes one


async def test_a_strangers_first_message_makes_them_a_contact(pod):
    context = await pod.prepare(pod.surface(), _event())

    assert context is not None
    [handle] = await pod.handles()
    assert (handle.kind, handle.value, handle.strength) == ("PHONE", PHONE, "CHANNEL")
    # And their writing is noted: it is what lets the pod write back.
    assert handle.last_inbound_at is not None


async def test_a_bot_for_known_contacts_refuses_a_stranger_once_a_day(pod):
    binder = _Binder()
    surface = pod.surface(answer="known")

    first = await pod.prepare(surface, _event(), binder=binder)
    second = await pod.prepare(surface, _event(), binder=binder)

    assert isinstance(first, SurfaceReplyContext)
    assert first.reply_kind == "contact_refusal"
    assert "Kit only answers people it already knows" in first.reply_message
    assert second is None
    assert await pod.handles() == []
    assert binder.seen == {}


async def test_a_known_contact_is_answered_by_a_bot_for_known_contacts(pod):
    known = await pod.known(IdentityKind.PHONE, PHONE)
    binder = _Binder()

    context = await pod.prepare(pod.surface(answer="known"), _event(), binder=binder)

    assert context is not None
    assert binder.seen["for_contact"] == known.id


async def test_past_the_days_new_contacts_a_stranger_is_not_met(pod):
    context = await pod.prepare(
        pod.surface(), _event(), limiter=_Limiter(allow_new=False)
    )

    assert context is None
    assert await pod.handles() == []


async def test_over_the_limit_a_contact_is_not_answered(pod):
    context = await pod.prepare(pod.surface(), _event(), limiter=_Limiter(allow=False))

    assert context is None


async def test_with_nobody_in_the_pod_looking_after_contacts_nobody_is_answered(
    pod,
):
    context = await pod.prepare(pod.surface(looked_after_by=uuid4()), _event())

    assert context is None
    assert await pod.handles() == []


# ------------------------------------------------------------ unverified email


@pytest.mark.parametrize(
    "verdict", [EmailAuthenticationVerdict.FAIL, EmailAuthenticationVerdict.UNKNOWN]
)
async def test_unauthenticated_email_is_parked_never_answered(pod, verdict):
    binder = _Binder()

    context = await pod.prepare(
        pod.surface(platform=SurfacePlatform.RESEND),
        _email(authentication=verdict),
        binder=binder,
    )

    assert context is None
    assert binder.seen == {}
    assert await pod.handles() == []
    assert await pod.notes() == [f"Unverified email from {EMAIL}"]


async def test_a_flood_of_unverified_mail_leaves_a_few_notes_and_one_summary(
    pod, monkeypatch
):
    monkeypatch.setattr(
        surface_settings, "surface_parked_mail_notes_per_surface_per_hour", 2
    )
    surface = pod.surface(platform=SurfacePlatform.RESEND)

    for index in range(5):
        forged = _email(
            authentication=EmailAuthenticationVerdict.FAIL,
            sender=f"forged{index}@client.example",
        )
        assert await pod.prepare(surface, forged) is None

    assert await pod.notes() == [
        "Unverified email from forged0@client.example",
        "Unverified email from forged1@client.example",
        "More unverified email",
    ]


async def test_without_redis_unverified_mail_leaves_no_note(pod):
    await pod.prepare(
        pod.surface(platform=SurfacePlatform.RESEND),
        _email(authentication=EmailAuthenticationVerdict.FAIL),
        redis=_FailingRedis(),
    )

    assert await pod.notes() == []


# ------------------------------------------------------------ mail from machines


@pytest.mark.parametrize(
    ("metadata", "sender"),
    [
        ({"automated": "auto_submitted"}, EMAIL),
        ({}, "help@acme.example"),
    ],
    ids=["auto-reply", "our-own-address"],
)
async def test_mail_a_machine_sent_is_neither_answered_nor_parked(
    pod, metadata, sender
):
    binder = _Binder()

    context = await pod.prepare(
        pod.surface(platform=SurfacePlatform.RESEND),
        _email(sender=sender, metadata={**metadata, "subject": "Out of office"}),
        binder=binder,
    )

    assert context is None
    assert binder.seen == {}
    assert await pod.handles() == []
    assert await pod.notes() == []


# ------------------------------------------------------------ STOP


@pytest.mark.parametrize("text", ["STOP", " stop ", "Unsubscribe.", "STOPALL"])
async def test_stop_unsubscribes_the_handle_and_starts_nothing(pod, text):
    await pod.known(IdentityKind.PHONE, PHONE)
    binder = _Binder()

    context = await pod.prepare(pod.surface(), _event(text=text), binder=binder)

    assert context is None
    [handle] = await pod.handles()
    assert handle.unsubscribed_at is not None
    # Not noted as writing again, which is how a contact opts back in.
    assert handle.last_inbound_at is None
    assert binder.seen == {}


async def test_stop_from_a_stranger_makes_no_contact(pod):
    context = await pod.prepare(pod.surface(), _event(text="stop"))

    assert context is None
    assert await pod.handles() == []


async def test_a_sentence_with_stop_in_it_is_a_question_not_an_unsubscribe(pod):
    context = await pod.prepare(pod.surface(), _event(text="Stop the order please"))

    assert context is not None
    [handle] = await pod.handles()
    assert handle.unsubscribed_at is None


# ------------------------------------------------------------ where it runs


async def test_a_contacts_turn_runs_in_their_own_conversation_as_nobody(pod):
    binder = _Binder()

    context = await pod.prepare(pod.surface(), _event(), binder=binder)

    contact_id = binder.seen["for_contact"]
    assert binder.seen["resolved_user"].internal_user_id == pod.owner
    assert binder.seen["resolved_user"].external_user_id == contact_link_user(
        contact_id
    )
    assert context is not None
    assert context.answers_outsider is True
    assert context.user_id == pod.owner
    assert context.message_metadata.sender_display_name == "Dana"


async def test_a_contacts_email_reply_copies_nobody_else(pod):
    await pod.known(IdentityKind.EMAIL, EMAIL)
    binder = _Binder()

    await pod.prepare(
        pod.surface(platform=SurfacePlatform.RESEND),
        _email(cc=["boss@client.example"]),
        binder=binder,
    )

    assert binder.seen["parsed"].reply_target["cc"] == []
