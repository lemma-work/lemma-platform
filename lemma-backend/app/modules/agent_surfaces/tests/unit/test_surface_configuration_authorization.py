from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization.permissions import Permissions
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.services.surface_configuration import (
    SurfaceConfigurationMixin,
)

pytestmark = pytest.mark.asyncio


class _Harness(SurfaceConfigurationMixin):
    pass


def _surface(*, pod_id, surface_id=None):
    return SimpleNamespace(
        id=surface_id or uuid4(),
        pod_id=pod_id,
        surface_type=SurfacePlatform.SLACK,
        external_workspace_id="T1",
        agent_id=None,
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
    auth_data = SimpleNamespace(
        build_user_context=AsyncMock(return_value=context)
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_configuration_authorization.create_authorization_data_service",
        lambda _uow: auth_data,
    )
    request = SurfacePlatformWebhookIngress(
        source="slack",
        payload={},
        receiver_surface_ids=[allowed_surface.id, other_pod_surface.id],
    )

    candidates, resolved_user_id, authorized = (
        await harness._authorized_configuration_surfaces(
            request,
            tenant_id="T1",
            platform=SurfacePlatform.SLACK,
            actor_external_user_id="U1",
            adapter=SimpleNamespace(),
            action=Permissions.AGENT_UPDATE,
        )
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
