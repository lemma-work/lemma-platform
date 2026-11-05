"""The person whose computer a workspace runs on can always stop sharing it.

Archiving a workspace-wide Agent Host profile took ORG_UPDATE and nothing else,
so an owner who lost that role could not take their own laptop back: it kept
taking the organization's runs until an admin noticed.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.modules.agent.api.controllers.runtime_config_controller import (
    _require_profile_archiver,
)
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)

pytestmark = pytest.mark.unit

_ORG = uuid4()
_OWNER = uuid4()
_HOST = uuid4()
_HARNESS = uuid4()


def _shared_machine() -> AgentRuntimeProfile:
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=_ORG,
        harness_id=_HARNESS,
        scope=RuntimeProfileScope.ORGANIZATION,
        kind=RuntimeProfileKind.HARNESS,
        protocol=RuntimeProfileProtocol.AGENT_HOST,
        name="Shared laptop",
        config={"harness_snapshot_revision": "rev-1"},
    )


class _Hosts:
    def __init__(self, *, revoked: bool = False) -> None:
        self.revoked = revoked

    async def get_harness(self, *, harness_id: UUID) -> SimpleNamespace | None:
        return SimpleNamespace(host_id=_HOST) if harness_id == _HARNESS else None

    async def get_for_user(
        self, *, host_id: UUID, user_id: UUID
    ) -> SimpleNamespace | None:
        if host_id != _HOST or user_id != _OWNER:
            return None
        return SimpleNamespace(revoked_at=object() if self.revoked else None)


class _NotAnEditor:
    async def require(self, *_: object) -> None:
        raise HTTPException(status_code=403, detail="ORG_UPDATE required")


async def _archive_as(user_id: UUID, *, hosts: _Hosts) -> None:
    await _require_profile_archiver(
        service=SimpleNamespace(host_repository=hosts),  # type: ignore[arg-type]  # TST-03: only the host reads
        profile=_shared_machine(),
        organization_id=_ORG,
        user=SimpleNamespace(id=user_id),  # type: ignore[arg-type]  # TST-03: only .id is read
        ctx=_NotAnEditor(),  # type: ignore[arg-type]  # TST-03: only .require is called
    )


@pytest.mark.asyncio
async def test_the_machines_owner_may_archive_without_org_update() -> None:
    await _archive_as(_OWNER, hosts=_Hosts())


@pytest.mark.asyncio
async def test_anyone_else_still_needs_org_update() -> None:
    with pytest.raises(HTTPException) as refused:
        await _archive_as(uuid4(), hosts=_Hosts())

    assert refused.value.status_code == 403


@pytest.mark.asyncio
async def test_an_unpaired_owner_is_no_longer_the_owner() -> None:
    with pytest.raises(HTTPException):
        await _archive_as(_OWNER, hosts=_Hosts(revoked=True))
