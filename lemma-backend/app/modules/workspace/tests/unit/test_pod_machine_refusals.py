"""Why a bundle's machines would be refused, asked before the import applies."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.ports.plan_limits import MachinePolicy
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.contracts.pod_machines import (
    BundledMachine,
    pod_machine_refusals,
)
from app.modules.workspace.domain.machine import (
    DEFAULT_MACHINE_SIZE,
    Capabilities,
    Machine,
    MachineBillingScope,
    MachineFabric,
    MachineKind,
    MachineSize,
    MachineTemplate,
    MachineVisibility,
)
from app.modules.workspace.infrastructure import machine_repository
from app.modules.workspace.services import pod_machines
from app.modules.workspace.services.machine_policy import effective

pytestmark = pytest.mark.unit

POD = uuid4()

_EXISTING = Machine(
    id=uuid4(),
    kind=MachineKind.POD,
    name="qa",
    display_name="qa",
    template=MachineTemplate.WORKSPACE_DOCKER,
    size=MachineSize(4, 8),
    fabric=MachineFabric.E2B,
    capabilities=Capabilities(docker=True),
    visibility=MachineVisibility.POD,
    billing_scope=MachineBillingScope.ORG,
    owner_id=POD,
    pod_id=POD,
)


@pytest.fixture
def on_docker(monkeypatch):
    """A Docker deployment whose plan allows everything it can build."""

    async def policy(_uow, _pod_id):
        return effective(MachinePolicy(max_size="2x4"), fabric=MachineFabric.DOCKER)

    class _Repository:
        def __init__(self, _uow) -> None:
            pass

        async def pod_machine(self, _pod_id, name):
            return _EXISTING if name == "qa" else None

    monkeypatch.setattr(pod_machines, "pod_policy", policy)
    monkeypatch.setattr(machine_repository, "MachineRepository", _Repository)
    monkeypatch.setattr(workspace_settings, "pod_machines_enabled", True)


async def _refusals(*machines: BundledMachine) -> list[str]:
    return await pod_machine_refusals(None, pod_id=POD, machines=list(machines))  # type: ignore[arg-type]


async def test_a_template_the_deployment_cannot_build_is_refused(on_docker):
    refusals = await _refusals(BundledMachine("ci", "workspace-docker", "4x8"))

    assert len(refusals) == 1
    assert refusals[0].startswith("Machine 'ci':")
    assert "workspace-docker" in refusals[0]


async def test_with_no_size_it_is_still_refused(on_docker):
    assert await _refusals(BundledMachine("ci", "workspace-docker", None))


async def test_a_size_over_the_plan_is_refused(on_docker):
    assert await _refusals(BundledMachine("big", "workspace", "4x8"))


async def test_what_the_plan_allows_passes(on_docker):
    assert (
        await _refusals(
            BundledMachine("small", "workspace", None),
            BundledMachine("medium", "workspace", "2x4"),
        )
        == []
    )


async def test_an_existing_machine_keeps_its_template(on_docker):
    """The apply never changes an existing machine's template, so a bundle
    naming one is not refused for it -- only a size it would change to."""
    assert await _refusals(BundledMachine("qa", "workspace", None)) == []
    assert await _refusals(BundledMachine("qa", "workspace", "4x8")) == []
    assert await _refusals(BundledMachine("qa", "workspace", "8x16"))


async def test_a_template_that_is_not_one_is_refused(on_docker):
    assert await _refusals(BundledMachine("odd", "gpu", None))


async def test_pod_machines_switched_off_refuses_them_all(on_docker, monkeypatch):
    monkeypatch.setattr(workspace_settings, "pod_machines_enabled", False)

    assert await _refusals(BundledMachine("small", "workspace", None))


def test_the_default_size_is_what_a_new_plain_machine_gets():
    policy = effective(MachinePolicy(), fabric=MachineFabric.E2B)

    assert (
        pod_machines.size_to_create(policy, MachineTemplate.WORKSPACE, None)
        == DEFAULT_MACHINE_SIZE
    )
    assert pod_machines.size_to_create(
        policy, MachineTemplate.WORKSPACE_DOCKER, None
    ) == MachineSize(4, 8)
