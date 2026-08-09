from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.api.schemas import SurfaceConnectionStatus
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceAccountSummary,
    SurfaceConnectionOwnerInfo,
)
from app.modules.agent_surfaces.services.surface_connection_resolver import (
    SurfaceConnectionResolver,
)

pytestmark = pytest.mark.asyncio


def _surface(**overrides) -> AgentSurfaceEntity:
    payload = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "name": "telegram",
        "agent_id": uuid4(),
        "surface_type": SurfacePlatform.TELEGRAM,
        "mode": SurfaceMode.DM,
        "account_id": uuid4(),
        "config": SurfaceConfig(),
    }
    payload.update(overrides)
    return AgentSurfaceEntity(**payload)


def _resolver(*, accounts: dict, owners: dict) -> SurfaceConnectionResolver:
    account_port = AsyncMock()
    account_port.list_account_summaries.return_value = accounts
    owner_port = AsyncMock()
    owner_port.list_pod_owners.return_value = owners
    return SurfaceConnectionResolver(account_port=account_port, owner_port=owner_port)


async def test_names_the_owner_of_someone_elses_account():
    """The point of the whole block: an editor sees who to ask, never a token."""
    owner_id, viewer_id = uuid4(), uuid4()
    surface = _surface()
    resolver = _resolver(
        accounts={
            surface.account_id: SurfaceAccountSummary(
                id=surface.account_id,
                user_id=owner_id,
                connector_id="telegram",
                display_name="@acme_ops_bot",
                status="CONNECTED",
            )
        },
        owners={
            owner_id: SurfaceConnectionOwnerInfo(
                user_id=owner_id, name="Priya Raman", is_pod_member=True
            )
        },
    )

    connection = await resolver.for_surface(
        surface, pod_id=surface.pod_id, viewer_user_id=viewer_id
    )

    assert connection is not None
    assert connection.display_name == "@acme_ops_bot"
    assert connection.status is SurfaceConnectionStatus.CONNECTED
    assert connection.connected_by is not None
    assert connection.connected_by.name == "Priya Raman"
    assert connection.connected_by.is_pod_member is True
    assert connection.connected_by.is_you is False
    assert "credentials" not in connection.model_dump()


async def test_owner_who_left_the_pod_is_flagged_but_the_account_still_reads_connected():
    """A departed owner's token keeps working until it expires — that is a
    membership fact, not an outage, so it must not masquerade as one."""
    owner_id = uuid4()
    surface = _surface()
    resolver = _resolver(
        accounts={
            surface.account_id: SurfaceAccountSummary(
                id=surface.account_id,
                user_id=owner_id,
                connector_id="telegram",
                display_name="@acme_ops_bot",
                status="CONNECTED",
            )
        },
        owners={
            owner_id: SurfaceConnectionOwnerInfo(
                user_id=owner_id, name="Priya Raman", is_pod_member=False
            )
        },
    )

    connection = await resolver.for_surface(surface, pod_id=surface.pod_id)

    assert connection is not None
    assert connection.status is SurfaceConnectionStatus.CONNECTED
    assert connection.connected_by is not None
    assert connection.connected_by.is_pod_member is False


async def test_expired_account_surfaces_as_reauth_required():
    owner_id = uuid4()
    surface = _surface()
    resolver = _resolver(
        accounts={
            surface.account_id: SurfaceAccountSummary(
                id=surface.account_id,
                user_id=owner_id,
                connector_id="gmail",
                display_name="ops@acme.com",
                status="REAUTH_REQUIRED",
            )
        },
        owners={owner_id: SurfaceConnectionOwnerInfo(user_id=owner_id, name="Priya")},
    )

    connection = await resolver.for_surface(surface, pod_id=surface.pod_id)

    assert connection is not None
    assert connection.status is SurfaceConnectionStatus.REAUTH_REQUIRED


async def test_viewers_own_account_is_marked_is_you():
    viewer_id = uuid4()
    surface = _surface()
    resolver = _resolver(
        accounts={
            surface.account_id: SurfaceAccountSummary(
                id=surface.account_id,
                user_id=viewer_id,
                connector_id="telegram",
                status="CONNECTED",
            )
        },
        owners={
            viewer_id: SurfaceConnectionOwnerInfo(user_id=viewer_id, is_pod_member=True)
        },
    )

    connection = await resolver.for_surface(
        surface, pod_id=surface.pod_id, viewer_user_id=viewer_id
    )

    assert connection is not None
    assert connection.connected_by is not None
    assert connection.connected_by.is_you is True


async def test_account_row_gone_reads_as_missing_rather_than_absent():
    surface = _surface()
    resolver = _resolver(accounts={}, owners={})

    connection = await resolver.for_surface(surface, pod_id=surface.pod_id)

    assert connection is not None
    assert connection.status is SurfaceConnectionStatus.MISSING
    assert connection.connected_by is None


async def test_system_credential_surface_has_no_connection():
    """No account means Lemma's own bot — there is nobody to name."""
    surface = _surface(account_id=None)
    resolver = _resolver(accounts={}, owners={})

    assert await resolver.for_surface(surface, pod_id=surface.pod_id) is None


async def test_a_page_of_surfaces_costs_one_account_lookup():
    owner_id = uuid4()
    pod_id = uuid4()
    surfaces = [_surface(pod_id=pod_id) for _ in range(5)]
    accounts = {
        surface.account_id: SurfaceAccountSummary(
            id=surface.account_id,
            user_id=owner_id,
            connector_id="telegram",
            status="CONNECTED",
        )
        for surface in surfaces
    }
    account_port = AsyncMock()
    account_port.list_account_summaries.return_value = accounts
    owner_port = AsyncMock()
    owner_port.list_pod_owners.return_value = {
        owner_id: SurfaceConnectionOwnerInfo(user_id=owner_id, is_pod_member=True)
    }
    resolver = SurfaceConnectionResolver(
        account_port=account_port, owner_port=owner_port
    )

    connections = await resolver.for_surfaces(surfaces, pod_id=pod_id)

    assert len(connections) == 5
    assert account_port.list_account_summaries.await_count == 1
    assert owner_port.list_pod_owners.await_count == 1


async def test_unknown_account_status_degrades_instead_of_raising():
    owner_id = uuid4()
    surface = _surface()
    resolver = _resolver(
        accounts={
            surface.account_id: SurfaceAccountSummary(
                id=surface.account_id,
                user_id=owner_id,
                connector_id="telegram",
                status="SOMETHING_NEW",
            )
        },
        owners={owner_id: SurfaceConnectionOwnerInfo(user_id=owner_id)},
    )

    connection = await resolver.for_surface(surface, pod_id=surface.pod_id)

    assert connection is not None
    assert connection.status is SurfaceConnectionStatus.CONNECTED
