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


async def _pod_in(session, *, organization_id: UUID, user_id: UUID) -> Pod:
    """A second pod, so an organisation can want two numbers at once."""
    pod = Pod(
        user_id=user_id,
        organization_id=organization_id,
        name=f"pod-{uuid4().hex[:8]}",
        config={},
    )
    session.add(pod)
    await session.flush()
    session.add(
        AgentModel(
            id=pod.id,
            pod_id=pod.id,
            user_id=user_id,
            name="pod_default",
            kind="POD_DEFAULT",
            instruction="",
            toolsets=[],
            visibility="POD",
        )
    )
    await session.commit()
    return pod


async def test_a_surface_is_given_a_number_of_the_organisations_own(
    db_session, test_pod, fixed_test_org
) -> None:
    """Allocation is the step that makes a pool a pool.

    Everything before this reads a number a surface already holds; nothing put
    one there. `resolve_binding` answers `None` for WhatsApp on every path --
    correctly, because a pooled number is allocated rather than derived from a
    connected account -- so without this the column stays NULL forever and the
    pool is inventory nobody draws from.
    """
    from app.modules.agent_surfaces.composition import build_surface_service
    from app.modules.agent_surfaces.services.whatsapp_surface_provisioning import (
        provision_pooled_whatsapp_surface,
    )

    await _number(db_session, phone_number_id="alloc-1", token="t1")
    uow = SqlAlchemyUnitOfWork(db_session)

    surface = await provision_pooled_whatsapp_surface(
        uow,
        service=build_surface_service(uow),
        pod_id=UUID(test_pod["id"]),
        agent_id=UUID(test_pod["id"]),
        organization_id=UUID(fixed_test_org["id"]),
    )

    assert surface.surface_identity_id == "alloc-1", (
        "a surface was created without being given a number, so the pool is "
        "inventory nothing draws from"
    )


async def test_a_second_surface_in_one_organisation_gets_a_different_number(
    db_session, test_pod, fixed_test_org, fixed_test_user
) -> None:
    """Exclusive within an organisation, so the second draw must move on.

    `uq_agent_org_whatsapp_number` is the arbiter and the allocator retries
    against it rather than pre-checking, so this also exercises the retry: the
    candidate list is a snapshot that still contains the number just taken.
    """
    from app.modules.agent_surfaces.composition import build_surface_service
    from app.modules.agent_surfaces.services.whatsapp_surface_provisioning import (
        provision_pooled_whatsapp_surface,
    )

    await _number(db_session, phone_number_id="alloc-a", token="a")
    await _number(db_session, phone_number_id="alloc-b", token="b")
    organization_id = UUID(fixed_test_org["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    second_pod = await _pod_in(
        db_session,
        organization_id=organization_id,
        user_id=UUID(str(fixed_test_user["id"])),
    )

    first = await provision_pooled_whatsapp_surface(
        uow,
        service=build_surface_service(uow),
        pod_id=UUID(test_pod["id"]),
        agent_id=UUID(test_pod["id"]),
        organization_id=organization_id,
    )
    second = await provision_pooled_whatsapp_surface(
        uow,
        service=build_surface_service(uow),
        pod_id=second_pod.id,
        agent_id=second_pod.id,
        organization_id=organization_id,
    )

    assert first.surface_identity_id != second.surface_identity_id, (
        "one organisation was handed the same number twice, so two of its "
        "surfaces answer on one line and an inbound message is ambiguous"
    )
    assert {first.surface_identity_id, second.surface_identity_id} == {
        "alloc-a",
        "alloc-b",
    }


async def test_an_exhausted_pool_says_so_rather_than_sharing(
    db_session, test_pod, fixed_test_org, fixed_test_user
) -> None:
    """Running out is a normal state, and it still has to be said out loud.

    The tempting failure is to fall back to the shared line: the caller gets a
    surface, nothing raises, and the person finds out only when a reply arrives
    from a number they have never seen. They asked for a number of their own.
    """
    from app.modules.agent_surfaces.composition import build_surface_service
    from app.modules.agent_surfaces.domain.errors import (
        AgentSurfaceNumberPoolExhaustedError,
    )
    from app.modules.agent_surfaces.services.whatsapp_surface_provisioning import (
        provision_pooled_whatsapp_surface,
    )

    await _number(db_session, phone_number_id="only-one", token="t")
    organization_id = UUID(fixed_test_org["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    second_pod = await _pod_in(
        db_session,
        organization_id=organization_id,
        user_id=UUID(str(fixed_test_user["id"])),
    )

    await provision_pooled_whatsapp_surface(
        uow,
        service=build_surface_service(uow),
        pod_id=UUID(test_pod["id"]),
        agent_id=UUID(test_pod["id"]),
        organization_id=organization_id,
    )

    with pytest.raises(AgentSurfaceNumberPoolExhaustedError) as refused:
        await provision_pooled_whatsapp_surface(
            uow,
            service=build_surface_service(uow),
            pod_id=second_pod.id,
            agent_id=second_pod.id,
            organization_id=organization_id,
        )

    assert refused.value.status_code == 503, (
        "an exhausted pool answered like a conflict; there is no other party "
        "to take it up with, the deployment simply has none left"
    )


async def test_two_pods_in_one_organisation_may_each_have_a_whatsapp_surface(
    authenticated_client, db_session, test_pod, monkeypatch
) -> None:
    """The refusal the pool exists to remove, asserted through HTTP.

    A second pod asking for WhatsApp used to get 409 "System WHATSAPP
    credentials are already used by another surface in this organization",
    because one number made the credential and the identity the same thing.
    With a pool they are different things and the second pod is entitled to its
    own number, so the organisation-wide claim had to stop applying -- and
    `test_surface_api_e2e` moved its 409 case to Telegram, which still has the
    single shared bot that rule was written for.

    Over HTTP rather than through the service, because the 409 this replaces
    reached people as an API response and a catalog that greyed the option out.
    """
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "system-whatsapp")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "system-phone")

    sibling = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": test_pod["organization_id"],
            "name": f"sibling-{uuid4().hex[:8]}",
        },
    )
    assert sibling.status_code == 201, sibling.text

    first = await authenticated_client.post(
        f"/pods/{test_pod['id']}/surfaces", json={"platform": "WHATSAPP"}
    )
    second = await authenticated_client.post(
        f"/pods/{sibling.json()['id']}/surfaces", json={"platform": "WHATSAPP"}
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, (
        "the second pod in this organisation was refused a WhatsApp surface, "
        f"so the pool cannot hand it a number of its own: {second.text}"
    )

    catalog = await authenticated_client.get(
        f"/pods/{sibling.json()['id']}/available-surfaces"
    )
    row = next(
        item for item in catalog.json()["surfaces"] if item["platform"] == "WHATSAPP"
    )
    assert row["system_claim"]["available"] is True, (
        "the catalog still greys WhatsApp out for an organisation that already "
        "holds one, which is the disagreement with the writer that this rule "
        "change exists to remove"
    )


async def test_deleting_a_pod_gives_its_number_back_to_the_pool(
    db_session, test_pod, fixed_test_org
) -> None:
    """A finite pool leaks unless something hands numbers back.

    Pod deletion is soft: the surface row survives on purpose so an undelete
    restores a working surface. That is right for a Slack app and wrong for
    something scarce -- a deleted pod would hold a number indefinitely and the
    deployment would run out on behalf of pods nobody is using.

    Asserted by allocating twice from a pool of one: the second allocation can
    only succeed if the first was genuinely released, which is stronger than
    reading the column back.
    """
    from app.modules.agent_surfaces.composition import build_surface_service
    from app.modules.agent_surfaces.contracts.email_surfaces import (
        release_pod_scarce_identities,
    )
    from app.modules.agent_surfaces.services.whatsapp_surface_provisioning import (
        provision_pooled_whatsapp_surface,
    )

    await _number(db_session, phone_number_id="recycled", token="t")
    organization_id = UUID(fixed_test_org["id"])
    pod_id = UUID(test_pod["id"])
    uow = SqlAlchemyUnitOfWork(db_session)

    first = await provision_pooled_whatsapp_surface(
        uow,
        service=build_surface_service(uow),
        pod_id=pod_id,
        agent_id=pod_id,
        organization_id=organization_id,
    )
    assert first.surface_identity_id == "recycled"

    await release_pod_scarce_identities(uow, pod_id=pod_id)

    again = await provision_pooled_whatsapp_surface(
        uow,
        service=build_surface_service(uow),
        pod_id=pod_id,
        agent_id=pod_id,
        organization_id=organization_id,
    )

    assert again.surface_identity_id == "recycled", (
        "the only number in the pool was still held by a deleted pod, so a "
        "finite pool drains one deleted pod at a time"
    )
    assert again.id != first.id
