from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization.permissions import Permissions
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.services.surface_configuration import (
    SurfaceConfigurationMixin,
)
from app.modules.agent_surfaces.services.surface_consent import SurfaceConsentMixin
from app.modules.agent_surfaces.services.surface_setup_read import SurfaceSetupReadMixin

pytestmark = pytest.mark.asyncio


class _Harness(SurfaceConfigurationMixin):
    pass


def _surface(*, pod_id, surface_id=None):
    return SimpleNamespace(
        id=surface_id or uuid4(),
        pod_id=pod_id,
        surface_type=SurfacePlatform.SLACK,
        external_workspace_id="T1",
        agent_id=uuid4(),
        name="slack",
    )


async def test_configuration_candidates_are_scoped_to_verified_receiver_and_membership(
    monkeypatch,
):
    user_id = uuid4()
    member_pod = uuid4()
    other_pod = uuid4()
    allowed_surface = _surface(pod_id=member_pod)
    cross_app_surface = _surface(pod_id=member_pod)
    other_pod_surface = _surface(pod_id=other_pod)

    harness = _Harness()
    harness.surface_repository = SimpleNamespace(
        list_active_by_type=AsyncMock(
            return_value=[allowed_surface, cross_app_surface, other_pod_surface]
        )
    )
    harness.external_user_repository = SimpleNamespace(
        get_by_identity=AsyncMock(
            return_value=SimpleNamespace(resolved_user_id=user_id)
        )
    )
    harness.identity_service = SimpleNamespace()
    harness.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[member_pod])
    )
    harness.uow = SimpleNamespace()
    context = SimpleNamespace(can=AsyncMock(return_value=True))
    auth_data = SimpleNamespace(build_user_context=AsyncMock(return_value=context))
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_configuration_authorization.create_authorization_data_service",
        lambda _uow: auth_data,
    )
    request = SurfacePlatformWebhookIngress(
        source="slack",
        payload={},
        receiver_surface_ids=[allowed_surface.id, other_pod_surface.id],
    )

    (
        candidates,
        resolved_user_id,
        authorized,
    ) = await harness._authorized_configuration_surfaces(
        request,
        tenant_id="T1",
        platform=SurfacePlatform.SLACK,
        actor_external_user_id="U1",
        adapter=SimpleNamespace(),
        action=Permissions.AGENT_UPDATE,
    )

    assert [surface.id for surface in candidates] == [
        allowed_surface.id,
        other_pod_surface.id,
    ]
    assert resolved_user_id == user_id
    assert [surface.id for surface, _ in authorized] == [allowed_surface.id]


async def test_configuration_requires_the_same_agent_permission_as_http(
    monkeypatch,
):
    user_id = uuid4()
    pod_id = uuid4()
    surface = _surface(pod_id=pod_id)
    harness = _Harness()
    harness.surface_repository = SimpleNamespace(
        list_active_by_type=AsyncMock(return_value=[surface])
    )
    harness.external_user_repository = SimpleNamespace(
        get_by_identity=AsyncMock(
            return_value=SimpleNamespace(resolved_user_id=user_id)
        )
    )
    harness.identity_service = SimpleNamespace()
    harness.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[pod_id])
    )
    harness.uow = SimpleNamespace()
    context = SimpleNamespace(can=AsyncMock(return_value=False))
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_configuration_authorization.create_authorization_data_service",
        lambda _uow: SimpleNamespace(
            build_user_context=AsyncMock(return_value=context)
        ),
    )

    _, _, authorized = await harness._authorized_configuration_surfaces(
        SurfacePlatformWebhookIngress(
            source="slack", payload={}, receiver_surface_ids=[surface.id]
        ),
        tenant_id="T1",
        platform=SurfacePlatform.SLACK,
        actor_external_user_id="U1",
        adapter=SimpleNamespace(),
        action=Permissions.AGENT_UPDATE,
    )

    assert authorized == []
    context.can.assert_awaited_once_with(Permissions.AGENT_UPDATE)


async def test_explicit_or_default_surface_replaces_first_row_selection():
    first = _surface(pod_id=uuid4())
    second = _surface(pod_id=uuid4())
    context = SimpleNamespace()
    membership = SimpleNamespace(
        get_user_default_surface_id=AsyncMock(return_value=second.id)
    )
    harness = _Harness()
    harness.pod_membership_port = membership
    authorized = [(first, context), (second, context)]

    explicit = await harness._pick_configuration_surface(
        authorized,
        explicit_surface_id=str(first.id),
        user_id=uuid4(),
        platform=SurfacePlatform.SLACK,
    )
    default = await harness._pick_configuration_surface(
        authorized,
        explicit_surface_id=None,
        user_id=uuid4(),
        platform=SurfacePlatform.SLACK,
    )

    assert explicit[0].id == first.id
    assert default[0].id == second.id


def _whatsapp_surface(*, pod_id):
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id,
        name="whatsapp",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.WHATSAPP,
        mode=SurfaceMode.DM,
        account_id=uuid4(),
        config=SurfaceConfig(),
        is_active=True,
    )


class _SetupHarness(SurfaceSetupReadMixin, SurfaceConsentMixin):
    pass


def _setup_harness(surface):
    harness = _SetupHarness()
    harness.get_surface_by_name_in_pod = AsyncMock(return_value=surface)
    harness.get_platform_setup_guide = lambda platform: None
    harness._surface_admin_consent = AsyncMock(return_value=None)
    harness._surface_uses_org_custom_app = AsyncMock(return_value=True)
    harness._credential_resolver = SimpleNamespace(
        for_account=AsyncMock(return_value={"verify_token": _A_VERIFY_TOKEN})
    )
    return harness


_A_VERIFY_TOKEN = "verify-token-under-test"


def _copyable_values(setup) -> list[str]:
    return [field.value for action in setup["actions"] for field in action.fields]


async def test_a_reader_who_cannot_change_the_surface_is_not_given_its_verify_token(
    monkeypatch,
):
    """``secret=True`` on a setup field is a rendering hint, not a gate.

    The WhatsApp verify token gates the subscription handshake, so anyone
    holding it can re-point the org's webhook somewhere else. It was returned in
    the setup body to every holder of ``AGENT_READ`` on the pod.
    """
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.common.public_https_api_url_available",
        lambda: True,
    )
    pod_id = uuid4()
    harness = _setup_harness(_whatsapp_surface(pod_id=pod_id))

    setup = await harness.get_surface_setup_by_name(
        pod_id=pod_id, name="whatsapp", reveal_secrets=False
    )

    assert _A_VERIFY_TOKEN not in _copyable_values(setup)
    # The rest of the checklist still arrives: this withholds one value, it does
    # not refuse the request.
    assert setup["actions"]


async def test_a_reader_who_can_change_the_surface_still_gets_the_verify_token(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.common.public_https_api_url_available",
        lambda: True,
    )
    pod_id = uuid4()
    harness = _setup_harness(_whatsapp_surface(pod_id=pod_id))

    setup = await harness.get_surface_setup_by_name(
        pod_id=pod_id, name="whatsapp", reveal_secrets=True
    )

    assert _A_VERIFY_TOKEN in _copyable_values(setup)
