"""A pool of numbers, and the two answers it has to change.

One WhatsApp number was a deployment-wide constant: every surface sent from it,
every message arrived on it, and "the WhatsApp credential" and "the WhatsApp
number" were the same sentence. Splitting them changes exactly two answers, and
everything else in the module is supposed to be untouched.

**What a surface sends as.** `native_credentials` reads settings and takes a
`surface` it ignores for WhatsApp, so every surface answered with the same
token. A surface holding a pooled number must answer with *that* number's.

**Which surface a message is for.** The arriving number now narrows candidates
-- beside the sender's pods, never instead of them, because a pooled number may
be held by several organisations and so names a number rather than a customer.

The third thing these pin is the half that must *not* change: a deployment with
no pool rows, and a surface holding no number, both behave exactly as before.
That is what lets this ship without a migration of behaviour.
"""

from __future__ import annotations

from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.agent import AgentModel
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceStatus,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.whatsapp_numbers import (
    WhatsAppNumberEntity,
    WhatsAppNumberRole,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.whatsapp_number_repository import (
    WhatsAppNumberRepository,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.pod.infrastructure.models.pod_models import Pod

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _number(session, *, phone_number_id: str, token: str) -> None:
    """One allocatable number carrying its own credentials."""
    await WhatsAppNumberRepository(SqlAlchemyUnitOfWork(session)).create(
        WhatsAppNumberEntity(
            id=uuid7(),
            phone_number_id=phone_number_id,
            display_phone_number=f"+1555{uuid4().int % 10_000_000:07d}",
            waba_id=f"waba-{phone_number_id}",
            access_token=token,
            role=WhatsAppNumberRole.ALLOCATABLE,
        )
    )
    await session.commit()


async def _whatsapp_surface(session, *, pod_id: UUID, holding: str | None):
    """A WhatsApp surface, optionally holding a pooled number."""
    organization_id = await session.scalar(
        select(Pod.organization_id).where(Pod.id == pod_id)
    )
    agent = AgentModel(
        id=uuid7(),
        pod_id=pod_id,
        user_id=await session.scalar(select(Pod.user_id).where(Pod.id == pod_id)),
        name=f"holder-{uuid4().hex[:6]}",
        kind="USER",
        instruction="",
        toolsets=[],
        visibility="POD",
    )
    session.add(agent)
    await session.flush()
    surface = AgentSurface(
        id=uuid7(),
        pod_id=pod_id,
        organization_id=organization_id,
        agent_id=agent.id,
        name=f"whatsapp-{uuid4().hex[:8]}",
        surface_type=SurfacePlatform.WHATSAPP.value,
        event_mode="WEBHOOK",
        credential_mode="SYSTEM",
        config={},
        surface_identity_id=holding,
        status=AgentSurfaceStatus.ACTIVE.value,
    )
    session.add(surface)
    await session.commit()
    return surface


async def test_a_surface_answers_as_the_number_it_holds(
    db_session, test_pod, monkeypatch
) -> None:
    """The token comes from the number, not from the deployment.

    This is the whole point of the pool and the single change everything
    outbound depends on. `native_credentials` takes a `surface` argument and
    ignored it for WhatsApp, so before this every surface in the deployment
    answered with one token -- which is correct for one number and silently
    wrong for several, in the direction where a message goes out from a number
    the recipient has never seen.
    """
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "deployment-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "deployment-pn")

    await _number(db_session, phone_number_id="pool-a", token="the-pools-token")
    surface = await _whatsapp_surface(
        db_session, pod_id=UUID(test_pod["id"]), holding="pool-a"
    )

    credentials = await SurfaceCredentialResolver(
        uow=SqlAlchemyUnitOfWork(db_session)
    ).for_surface(surface.to_entity())

    assert credentials["access_token"] == "the-pools-token", (
        "a surface holding a pooled number sent with the deployment's token, so "
        "every organisation would reply from the same number"
    )
    assert credentials["phone_number_id"] == "pool-a"
    assert credentials["waba_id"] == "waba-pool-a"


async def test_a_surface_holding_nothing_still_answers_from_settings(
    db_session, test_pod, monkeypatch
) -> None:
    """The half that must not change.

    Every WhatsApp surface alive holds no number -- `resolve_binding` has never
    populated `surface_identity_id` for the platform -- so if this answer moved,
    the pool would have broken every existing deployment on the way in.
    """
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "deployment-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "deployment-pn")

    surface = await _whatsapp_surface(
        db_session, pod_id=UUID(test_pod["id"]), holding=None
    )

    credentials = await SurfaceCredentialResolver(
        uow=SqlAlchemyUnitOfWork(db_session)
    ).for_surface(surface.to_entity())

    assert credentials["access_token"] == "deployment-token"
    assert credentials["phone_number_id"] == "deployment-pn"


async def test_a_number_whose_row_was_removed_keeps_the_surface_on_the_air(
    db_session, test_pod, monkeypatch
) -> None:
    """Inventory was edited under a live surface; that is not an outage.

    Refusing here would take a surface off the air over an operator deleting a
    row. The settings number is the wrong answer but it is a working one, and
    the mismatch is logged rather than raised.
    """
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "deployment-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "deployment-pn")

    surface = await _whatsapp_surface(
        db_session, pod_id=UUID(test_pod["id"]), holding="never-existed"
    )

    credentials = await SurfaceCredentialResolver(
        uow=SqlAlchemyUnitOfWork(db_session)
    ).for_surface(surface.to_entity())

    assert credentials["access_token"] == "deployment-token"


async def test_a_message_reaches_the_surface_holding_the_number_it_arrived_on(
    db_session, test_pod
) -> None:
    """Two surfaces of one sender, two numbers, and the message picks one.

    Both surfaces are in pods this sender belongs to, so the pod narrowing alone
    leaves both standing -- which is exactly the ambiguity the number resolves.
    Asserting on both directions rather than one, because a predicate that
    always returned the first row would pass a single-direction test.
    """
    pod_id = UUID(test_pod["id"])
    await _number(db_session, phone_number_id="pool-x", token="x")
    await _number(db_session, phone_number_id="pool-y", token="y")
    holds_x = await _whatsapp_surface(db_session, pod_id=pod_id, holding="pool-x")
    holds_y = await _whatsapp_surface(db_session, pod_id=pod_id, holding="pool-y")

    repository = SurfaceRepository(SqlAlchemyUnitOfWork(db_session))

    for arriving, expected, other in (
        ("pool-x", holds_x, holds_y),
        ("pool-y", holds_y, holds_x),
    ):
        found = await repository.list_active_for_routing(
            SurfacePlatform.WHATSAPP.value,
            pod_ids={pod_id},
            system_credentials_only=True,
            surface_identity_id=arriving,
        )
        ids = {surface.id for surface in found}
        assert expected.id in ids, (
            f"a message on {arriving} did not reach the surface holding it"
        )
        assert other.id not in ids, (
            f"a message on {arriving} also reached the surface holding "
            f"{other.surface_identity_id}, so the number narrowed nothing"
        )


async def test_a_surface_holding_no_number_is_still_a_candidate(
    db_session, test_pod
) -> None:
    """ "This number, or no number yet" -- and the second half is why.

    Every WhatsApp surface in an existing deployment holds NULL. A strict
    equality would have taken every one of them out of routing the moment the
    predicate was passed, which is an outage dressed as a narrowing. The NULL
    half retires on its own as allocation fills the column in.
    """
    pod_id = UUID(test_pod["id"])
    await _number(db_session, phone_number_id="pool-z", token="z")
    unallocated = await _whatsapp_surface(db_session, pod_id=pod_id, holding=None)

    found = await SurfaceRepository(
        SqlAlchemyUnitOfWork(db_session)
    ).list_active_for_routing(
        SurfacePlatform.WHATSAPP.value,
        pod_ids={pod_id},
        system_credentials_only=True,
        surface_identity_id="pool-z",
    )

    assert unallocated.id in {surface.id for surface in found}, (
        "a surface that has not been allocated a number stopped being routable "
        "as soon as the number predicate was applied"
    )
