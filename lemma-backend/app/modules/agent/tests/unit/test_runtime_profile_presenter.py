from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.api.runtime_profile_presenter import _visible_agent_host
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)


class _HostRepository:
    def __init__(self, host):
        self.host = host

    async def get(self, host_id):
        return self.host if self.host.id == host_id else None

    async def get_for_user(self, *, host_id, user_id):
        if self.host.id == host_id and self.host.user_id == user_id:
            return self.host
        return None


def _profile(*, organization_id, owner_user_id, scope):
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=organization_id,
        user_id=owner_user_id,
        host_integration_id=uuid4(),
        scope=scope,
        kind=RuntimeProfileKind.EXTERNAL_AGENT,
        protocol=RuntimeProfileProtocol.AGENT_HOST_V2,
        name="Local Codex",
        default_model_name="default",
        config={"integration_snapshot_revision": "revision-1"},
    )


@pytest.mark.asyncio
async def test_workspace_profile_exposes_owner_host_to_other_member() -> None:
    organization_id = uuid4()
    owner_user_id = uuid4()
    host = SimpleNamespace(
        id=uuid4(),
        user_id=owner_user_id,
        organization_id=organization_id,
    )

    visible = await _visible_agent_host(
        _profile(
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            scope=RuntimeProfileScope.ORGANIZATION,
        ),
        host_id=host.id,
        host_repo=_HostRepository(host),
        user_id=uuid4(),
    )

    assert visible is host


@pytest.mark.asyncio
async def test_personal_profile_hides_owner_host_from_other_member() -> None:
    organization_id = uuid4()
    owner_user_id = uuid4()
    host = SimpleNamespace(
        id=uuid4(),
        user_id=owner_user_id,
        organization_id=organization_id,
    )

    visible = await _visible_agent_host(
        _profile(
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            scope=RuntimeProfileScope.PERSONAL,
        ),
        host_id=host.id,
        host_repo=_HostRepository(host),
        user_id=uuid4(),
    )

    assert visible is None
