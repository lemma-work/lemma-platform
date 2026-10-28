"""A run resolves its runtime with the same resolver the MCP bridge uses.

The bridge asked the harness whether it reads images; the run itself built its
resolver without the host repository and read the catalog copied when the
profile was created -- usually before the harness's probe had said it can see.
One run, two answers: the bridge offered page images the run's own context
said its model could not read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.agent_host.control.repository import AgentHostRepository
from app.modules.agent.infrastructure.harness.registry import HarnessRegistry
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
    runtime_profile_service_for,
)

pytestmark = pytest.mark.unit

_ORG = uuid4()
_USER = uuid4()
_HARNESS = uuid4()

_PROFILE = AgentRuntimeProfile(
    id=str(uuid4()),
    organization_id=_ORG,
    user_id=_USER,
    harness_id=_HARNESS,
    scope=RuntimeProfileScope.PERSONAL,
    kind=RuntimeProfileKind.HARNESS,
    protocol=RuntimeProfileProtocol.AGENT_HOST,
    name="Laptop",
    default_model_name="opus",
    # Copied at creation, before the harness's probe reported images.
    model_catalog=[
        {
            "name": "opus",
            "provider_model_name": "opus",
            "capabilities": ["TEXT", "TOOLS"],
        }
    ],
    config={"harness_snapshot_revision": "rev-1"},
)


class _Profiles:
    """The repository port as far as one resolve reads it."""

    async def get_visible_by_id(
        self, *, profile_id: str, organization_id: UUID, user_id: UUID, **_: object
    ) -> AgentRuntimeProfile | None:
        return _PROFILE if profile_id == _PROFILE.id else None


class _Hosts:
    """The harness, as it reports itself now."""

    async def get_harnesses(self, ids: set[UUID]) -> dict[UUID, SimpleNamespace]:
        return {_HARNESS: SimpleNamespace(capabilities={"images": True})}


@asynccontextmanager
async def _uow_factory() -> AsyncIterator[SimpleNamespace]:
    yield SimpleNamespace(session=None)


def test_every_run_path_gets_a_resolver_that_can_ask_the_harness() -> None:
    service = runtime_profile_service_for(SimpleNamespace(session=None))  # type: ignore[arg-type]  # TST-03: construction reads only .session

    assert isinstance(service.host_repository, AgentHostRepository)


def test_a_run_resolves_through_the_shared_factory_unless_told_otherwise() -> None:
    runner = AgentRunnerService(
        uow_factory=_uow_factory,  # type: ignore[arg-type]  # TST-03: never entered here
        harness_registry=HarnessRegistry([]),
    )

    assert runner.profile_services is runtime_profile_service_for


@pytest.mark.asyncio
async def test_a_runs_runtime_believes_the_harness_over_its_stale_catalog() -> None:
    runner = AgentRunnerService(
        uow_factory=_uow_factory,  # type: ignore[arg-type]  # TST-03: one session-less unit of work
        harness_registry=HarnessRegistry([]),
        profile_services=lambda _uow: AgentRuntimeProfileService(
            _Profiles(),  # type: ignore[arg-type]  # TST-03: the one read a resolve makes
            _Hosts(),  # type: ignore[arg-type]  # TST-03: the one read the vision check makes
        ),
    )

    resolved = await runner._resolve_agent_runtime(
        AgentRuntimeConfig(profile_id=_PROFILE.id),
        user_id=_USER,
        organization_id=_ORG,
    )

    assert RuntimeModelCapability.VISION in resolved.capabilities
