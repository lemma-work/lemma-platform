"""A private message has one routing authority, whichever path answers it.

A verified personal route answers a private message before ordinary selection
runs, and the saved default (`/surfaces/me`) was only ever read by selection. So
for anyone holding both, the default -- which the docs call authoritative -- did
nothing. These tests drive the coordinator the way the worker does, first with
only the route and then with a default saved.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.contracts.provisioning import ensure_pod_default_agent
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.events.handlers import _context_for_delivery
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.services.chat_onboarding import (
    ChatOnboardingCoordinator,
)
from app.modules.agent_surfaces.services.onboarding_transport import (
    resolve_onboarding_transport,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _ensure_connector_account,
    _load_slack_dm_fixture,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_a_saved_default_outranks_a_personal_route(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    fake_slack._test_user_email = fixed_test_user["email"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-routing-authority",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "team_id": "T0123456",
                "bot_user_id": "U0AGSSTQZLH",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    _, surface = await _create_agent_surface(
        authenticated_client,
        test_pod["id"],
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    factory = SessionUnitOfWorkFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    user_id = UUID(fixed_test_user["id"])
    actor = "U" + uuid4().hex[:10]

    def delivery(text: str) -> SurfacePlatformWebhookIngress:
        payload = _load_slack_dm_fixture(text=text, ts=uuid4().hex)
        payload["event"].update(
            {"user": actor, "channel": f"D{actor}", "channel_type": "im"}
        )
        return SurfacePlatformWebhookIngress(source="slack", payload=payload)

    # The person's private chat already routes to a pod of their own choosing.
    # The route is written directly: how it got there is onboarding's story, and
    # this is about which of two standing choices answers.
    transport = await resolve_onboarding_transport(
        delivery("hello"),
        uow_factory=factory,
        adapters=SurfacePlatformAdapterRegistry(),
    )
    assert transport is not None and transport.event.tenant_id
    async with factory() as uow:
        await ensure_pod_default_agent(
            uow, pod_id=UUID(test_pod["id"]), user_id=user_id
        )
        uow.session.add(
            VerifiedSurfaceIdentity(
                binding_key=transport.binding_key,
                platform="SLACK",
                tenant_id=transport.event.tenant_id,
                external_user_id=actor,
                user_id=user_id,
                installation_surface_id=UUID(surface["id"]),
                pod_id=UUID(test_pod["id"]),
            )
        )
    coordinator = ChatOnboardingCoordinator(factory)

    by_route = await coordinator.handle(delivery("with only a route"))
    assert by_route.handled and by_route.context is not None
    assert by_route.context.personal_dm_route_id is not None

    put = await authenticated_client.put(
        "/surfaces/me/default",
        json={"platform": "SLACK", "surface_id": surface["id"]},
    )
    assert put.status_code == 200, put.text

    # With a default saved the route steps aside, so the message is answered by
    # the ordinary path -- selection, which honours the default.
    by_default = await _context_for_delivery(
        delivery("with a default too"),
        onboarding_handler=coordinator.handle,
        uow_factory=factory,
    )
    assert isinstance(by_default, SurfaceChatContext)
    assert by_default.personal_dm_route_id is None
    assert by_default.surface_id == UUID(surface["id"])
