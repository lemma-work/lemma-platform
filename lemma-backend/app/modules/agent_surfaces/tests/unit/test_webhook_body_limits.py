"""Every surface webhook route holds at most its platform's ceiling of a body.

These routes are unauthenticated until the signature over the body checks out,
so the body has to be read first -- and before this, it was read whole, up to
the deployment's upload-sized limit, by anyone. Each route here is driven over
raw ASGI: a declared length past the ceiling must be refused with nothing read,
and a chunked body with no length at the chunk that crosses it, with the
verifier never called either way.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.modules.agent_surfaces.api.controllers.webhook_controller import router
from app.modules.agent_surfaces.api.controllers.webhook_ingest import (
    WEBHOOK_BODY_LIMITS,
)
from app.modules.agent_surfaces.api.controllers.webhook_seams import (
    get_inbound_accept,
    get_pooled_number_lookup,
    get_surface_lookup,
)
from app.modules.agent_surfaces.api.dependencies import (
    get_surface_webhook_security_service,
    get_telegram_manager_service,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceNotFoundError,
    SurfaceWebhookAuthenticationError,
)
from app.modules.agent_surfaces.domain.whatsapp_numbers import WhatsAppNumberEntity
from app.modules.agent_surfaces.services.inbound_acceptor import InboundDelivery
from app.modules.agent_surfaces.services.webhook_security_service import (
    SurfaceWebhookSecurityService,
)
from app.modules.test_support.body_ceiling import (
    app_with,
    chunks_of,
    chunks_to_cross,
    deliver,
    past_ceiling,
    too_large,
)

_MIB = 1024 * 1024
_MANAGER_SECRET = "manager-secret"
_PHONE_NUMBER_ID = "pooled-phone-1"
_APP_SECRET = "pooled-app-secret"


def _surface(platform: SurfacePlatform) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name=platform.value.lower(),
        agent_id=uuid4(),
        surface_type=platform,
        config=SurfaceConfig(),
    )


_TELEGRAM = _surface(SurfacePlatform.TELEGRAM)
_WHATSAPP = _surface(SurfacePlatform.WHATSAPP)


class _Recorded:
    """Every collaborator the routes reach after the read, recording what they
    were handed -- so "never verified" is ``verified == []``."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.refuse = refuse
        self.verified: list[bytes] = []
        self.accepted: list[InboundDelivery] = []
        self.updates: list[dict[str, object]] = []

    async def verify_surface_request(
        self, *, surface: AgentSurfaceEntity, headers: dict[str, str], raw_body: bytes
    ) -> None:
        self.verified.append(raw_body)
        if self.refuse:
            raise SurfaceWebhookAuthenticationError("Invalid webhook signature")

    async def handle_update(self, payload: dict[str, object]) -> None:
        self.updates.append(payload)

    async def accept(self, delivery: InboundDelivery) -> None:
        self.accepted.append(delivery)

    async def surface_by_id(self, surface_id: UUID) -> AgentSurfaceEntity:
        for surface in (_TELEGRAM, _WHATSAPP):
            if surface.id == surface_id:
                return surface
        raise AgentSurfaceNotFoundError(str(surface_id))

    async def pooled_number(self, phone_number_id: str) -> WhatsAppNumberEntity:
        return WhatsAppNumberEntity(
            phone_number_id=phone_number_id,
            display_phone_number="+15551230001",
            waba_id="waba-pooled",
            app_secret=_APP_SECRET,
        )


def _app(recorded: _Recorded, *, real_security: bool = False):
    security = SurfaceWebhookSecurityService() if real_security else recorded
    return app_with(
        router,
        {
            get_surface_webhook_security_service: lambda: security,
            get_telegram_manager_service: lambda: recorded,
            get_inbound_accept: lambda: recorded.accept,
            get_surface_lookup: lambda: recorded.surface_by_id,
            get_pooled_number_lookup: lambda: recorded.pooled_number,
        },
    )


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(
        surface_settings,
        "telegram_manager_webhook_secret",
        SecretStr(_MANAGER_SECRET),
    )


_ROUTES = [
    pytest.param(
        "/surfaces/webhooks/telegram-manager",
        ((b"x-telegram-bot-api-secret-token", _MANAGER_SECRET.encode()),),
        _MIB,
        id="telegram-manager",
    ),
    pytest.param("/surfaces/webhooks/slack", (), _MIB, id="platform-slack"),
    pytest.param("/surfaces/webhooks/WhatsApp", (), 3 * _MIB, id="platform-whatsapp"),
    pytest.param(
        f"/surfaces/webhooks/whatsapp/numbers/{_PHONE_NUMBER_ID}",
        (),
        3 * _MIB,
        id="whatsapp-number",
    ),
    pytest.param(
        f"/surfaces/{_TELEGRAM.id}/webhook", (), 3 * _MIB, id="surface-telegram"
    ),
    pytest.param(
        f"/surfaces/{_WHATSAPP.id}/webhook", (), 3 * _MIB, id="surface-whatsapp"
    ),
]


@pytest.mark.parametrize(("path", "headers", "ceiling"), _ROUTES)
async def test_a_declared_length_past_the_ceiling_is_refused_unread(
    path, headers, ceiling
):
    recorded = _Recorded()

    refused = await deliver(
        _app(recorded),
        path,
        [b"\0" * (ceiling + 1)],
        headers=headers,
        declared_length=ceiling + 1,
    )

    assert refused.status == 413
    assert refused.json() == too_large(ceiling)
    assert refused.chunks_read == 0
    assert (recorded.verified, recorded.updates, recorded.accepted) == ([], [], [])


@pytest.mark.parametrize(("path", "headers", "ceiling"), _ROUTES)
async def test_a_chunked_body_with_no_length_is_refused_as_it_crosses(
    path, headers, ceiling
):
    recorded = _Recorded()

    refused = await deliver(
        _app(recorded), path, past_ceiling(ceiling), headers=headers
    )

    assert refused.status == 413
    assert refused.json() == too_large(ceiling)
    assert refused.chunks_read == chunks_to_cross(ceiling)
    assert (recorded.verified, recorded.updates, recorded.accepted) == ([], [], [])


@pytest.mark.parametrize(
    "path",
    ["/surfaces/webhooks/not-a-platform", f"/surfaces/{uuid4()}/webhook"],
    ids=["unknown-platform", "unknown-surface"],
)
async def test_an_address_nothing_answers_to_is_refused_before_its_body_is_read(
    path,
):
    refused = await deliver(_app(_Recorded()), path, past_ceiling(_MIB))

    assert refused.status == 404
    assert refused.chunks_read == 0


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "chunked"])
async def test_a_surfaces_413_is_the_same_whatever_its_platform(declared):
    """The per-surface path does not name its platform, so nothing it answers
    before the signature may depend on one -- not the status, not the body."""
    answers = []
    for surface in (_TELEGRAM, _WHATSAPP):
        chunks = past_ceiling(3 * _MIB)
        answers.append(
            await deliver(
                _app(_Recorded()),
                f"/surfaces/{surface.id}/webhook",
                chunks,
                declared_length=sum(map(len, chunks)) if declared else None,
            )
        )

    telegram, whatsapp = answers
    assert telegram.status == whatsapp.status == 413
    assert telegram.json() == whatsapp.json()


async def test_an_unsigned_body_too_big_for_telegram_is_answered_as_for_whatsapp():
    """2 MiB is past Telegram's ceiling and inside WhatsApp's. Read under each
    surface's own, it was a 413 for one and a 401 for the other."""
    answers = []
    for surface in (_TELEGRAM, _WHATSAPP):
        answers.append(
            await deliver(
                _app(_Recorded(refuse=True)),
                f"/surfaces/{surface.id}/webhook",
                chunks_of(json.dumps({"padding": "x" * (2 * _MIB)}).encode()),
                headers=((b"content-type", b"application/json"),),
            )
        )

    telegram, whatsapp = answers
    assert telegram.status == whatsapp.status == 401
    assert telegram.json() == whatsapp.json()


def test_every_platform_has_a_ceiling_and_whatsapp_holds_what_meta_sends():
    """A platform added without a ceiling would answer its webhooks with a 500.

    Meta documents WhatsApp webhook payloads of up to 3 MB; one under that must
    reach the signature check rather than be refused as too large.
    """
    assert set(WEBHOOK_BODY_LIMITS) == set(SurfacePlatform)
    assert WEBHOOK_BODY_LIMITS[SurfacePlatform.WHATSAPP] >= 3_000_000


def _whatsapp_message(size: int) -> bytes:
    """A WhatsApp delivery for the pooled number, padded to ``size`` bytes."""
    envelope = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": _PHONE_NUMBER_ID},
                            "messages": [
                                {
                                    "id": "wamid.large-1",
                                    "from": "14155552671",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
        "padding": "",
    }
    unpadded = len(json.dumps(envelope).encode())
    envelope["padding"] = "x" * (size - unpadded)
    return json.dumps(envelope).encode()


async def test_a_whatsapp_delivery_past_one_mib_verifies_over_the_bytes_sent():
    """Larger than any other platform's ceiling, chunked with no length, and
    checked by the real verifier against an HMAC over exactly what was sent."""
    recorded = _Recorded()
    body = _whatsapp_message(2 * _MIB)
    signature = hmac.new(_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()

    answered = await deliver(
        _app(recorded, real_security=True),
        f"/surfaces/webhooks/whatsapp/numbers/{_PHONE_NUMBER_ID}",
        chunks_of(body),
        headers=(
            (b"content-type", b"application/json"),
            (b"x-hub-signature-256", f"sha256={signature}".encode()),
        ),
    )

    assert answered.status == 200, answered.body
    assert answered.json() == {"message": "Webhook received"}
    (delivery,) = recorded.accepted
    assert delivery.request.payload == json.loads(body)


async def test_a_surface_is_verified_over_the_bytes_exactly_as_they_arrived():
    recorded = _Recorded()
    # Two UTF-8 bytes a character after an odd-length prefix, so every chunk
    # boundary falls inside one.
    body = json.dumps(
        {"id": "event-01", "padding": "é" * _MIB}, ensure_ascii=False
    ).encode()

    answered = await deliver(
        _app(recorded),
        f"/surfaces/{_WHATSAPP.id}/webhook",
        chunks_of(body),
        headers=((b"content-type", b"application/json"),),
    )

    assert answered.status == 200, answered.body
    assert recorded.verified == [body]
