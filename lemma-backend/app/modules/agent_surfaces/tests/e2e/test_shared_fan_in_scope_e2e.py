"""The shared bot's fan-in, and what it costs to answer one message.

A platform-wide webhook arrives for the whole deployment at once: WhatsApp Cloud
API and the Telegram shared bot deliver every sender's message to one endpoint,
and the surface it belongs to is decided per sender. The candidate query for
that is every system-credential surface of the platform there is -- one per
provisioned person -- read and hydrated on the way to picking the one or two the
sender can actually use. Measured on this harness at 200 extra provisioned
users, it went from 1 row to 201: strictly linear in how many people have signed
up, on the path every message takes.

These pin the narrowing that fixed it *and* the answers it must not change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.agent import AgentModel
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _whatsapp_payload
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.pod.infrastructure.models.pod_models import Pod

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

#: Enough to tell a constant from a line without making the fixture slow.
_OTHER_PEOPLE = 25


async def _provision_other_people(sessions, *, organization_id, owner_id) -> None:
    """Other people's personal pods, each with its own shared-bot surface.

    They are nobody's concern on this message and every one of them used to be
    read for it.
    """
    async with sessions() as session:
        for index in range(_OTHER_PEOPLE):
            pod = Pod(
                user_id=owner_id,
                organization_id=organization_id,
                name=f"somebody else {index} {uuid4().hex[:6]}",
                config={},
            )
            session.add(pod)
            await session.flush()
            session.add(
                AgentModel(
                    id=pod.id,
                    pod_id=pod.id,
                    user_id=owner_id,
                    name="pod_default",
                    kind="POD_DEFAULT",
                    instruction="",
                    toolsets=[],
                    visibility="POD",
                )
            )
            await session.flush()
            session.add(_shared_surface(pod.id))
        await session.commit()


def _shared_surface(pod_id: UUID) -> AgentSurface:
    return AgentSurface(
        pod_id=pod_id,
        agent_id=pod_id,
        name=f"lemma-whatsapp-{uuid4().hex[:8]}",
        surface_type="WHATSAPP",
        mode="DM",
        event_mode="WEBHOOK",
        credential_mode="SYSTEM",
        config={},
    )


@pytest.fixture
async def shared_bot(db_session, fake_whatsapp, monkeypatch):
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.service._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-fan-in")
    return async_sessionmaker(db_session.bind, expire_on_commit=False)


async def _ingest(sessions, *, sender_phone: str):
    from app.modules.agent_surfaces.api.dependencies import get_surface_event_handler

    async with sessions() as session:
        handler = get_surface_event_handler(SqlAlchemyUnitOfWork(session))
        return await handler.prepare_ingress(
            SurfacePlatformWebhookIngress(
                source="whatsapp",
                payload=_whatsapp_payload(
                    text="what happened this week",
                    message_id=uuid4().hex,
                    phone_number_id="1234567890",
                    waba_id="waba-fan-in",
                    sender_phone=sender_phone,
                ),
            )
        )


async def _known_sender(sessions, *, user_id, pod_id: UUID) -> str:
    """Somebody the deployment already knows, with a surface in their own pod."""
    phone = "1555" + str(uuid4().int)[:7]
    async with sessions() as session:
        user = await session.get(User, user_id)
        user.mobile_number = "+" + phone
        user.mobile_verified_at = datetime.now(timezone.utc)
        session.add(_shared_surface(pod_id))
        await session.commit()
    return phone


async def test_a_known_sender_routes_past_everybody_elses_surfaces(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    shared_bot,
) -> None:
    """The answer, with the crowd present and without it, is the same answer."""
    sessions = shared_bot
    phone = await _known_sender(
        sessions, user_id=fixed_test_user["id"], pod_id=UUID(test_pod["id"])
    )
    await _provision_other_people(
        sessions,
        organization_id=UUID(fixed_test_org["id"]),
        owner_id=UUID(str(fixed_test_user["id"])),
    )

    context = await _ingest(sessions, sender_phone=phone)

    assert isinstance(context, SurfaceChatContext), (
        "a known sender no longer reaches their own conversation"
    )
    assert context.pod_id == UUID(test_pod["id"])


async def test_the_read_is_the_senders_pods_not_the_deployments(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    shared_bot,
) -> None:
    """The cost, stated as rows rather than as a statement count.

    A statement count would have stayed flat through the whole regression -- it
    was always one query. What grew was what the query returned, and what it
    returns is the thing to hold still.
    """
    sessions = shared_bot
    await _known_sender(
        sessions, user_id=fixed_test_user["id"], pod_id=UUID(test_pod["id"])
    )

    async def rows(pod_ids) -> int:
        async with sessions() as session:
            found = await SurfaceRepository(
                SqlAlchemyUnitOfWork(session)
            ).list_active_for_routing(
                "WHATSAPP", pod_ids=pod_ids, system_credentials_only=True
            )
            return len(found)

    async with sessions() as session:
        mine = set(
            await SqlAlchemySurfaceRoutingResolutionAdapter(
                SqlAlchemyUnitOfWork(session)
            ).get_user_pod_ids(UUID(str(fixed_test_user["id"])))
        )

    unscoped_before, scoped_before = await rows(None), await rows(mine)
    await _provision_other_people(
        sessions,
        organization_id=UUID(fixed_test_org["id"]),
        owner_id=UUID(str(fixed_test_user["id"])),
    )
    unscoped_after, scoped_after = await rows(None), await rows(mine)

    # The shape of the problem: unscoped grows with everybody who signs up.
    assert unscoped_after - unscoped_before == _OTHER_PEOPLE
    # The shape of the fix: scoped grows with the sender's own workspaces.
    assert scoped_after == scoped_before


async def test_a_stranger_is_still_routed_into_signup(
    authenticated_client, db_session, test_pod, shared_bot
) -> None:
    """Nobody's pods are not no pods.

    The narrowing is only available once the sender is known, and someone the
    deployment has never seen belongs to no pod -- so a scoped read answers "no
    candidates" for every new person there will ever be. That is why the
    unnarrowed read stays as the fallback, and this is the case that proves it:
    with one surface on the platform, a single unambiguous DM target is routed
    to *as* the target, so the signup flow runs on it. Skip the fallback and
    there are no candidates, the shortcut cannot fire, and the first message
    from every new person takes the no-surface path instead.
    """
    sessions = shared_bot
    async with sessions() as session:
        only_surface = _shared_surface(UUID(test_pod["id"]))
        session.add(only_surface)
        await session.commit()
        surface_id = only_surface.id

    context = await _ingest(sessions, sender_phone="1555" + str(uuid4().int)[:7])

    assert context is not None, "a stranger's first message was dropped"
    assert context.surface_id == surface_id, (
        "a stranger no longer reaches the surface their signup runs on"
    )
    assert not isinstance(context, SurfaceChatContext), (
        "a stranger was routed into somebody's conversation"
    )
