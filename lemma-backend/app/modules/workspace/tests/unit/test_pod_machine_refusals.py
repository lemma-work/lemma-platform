"""Why a bundle's machines would be refused, asked before the import applies."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.ports.plan_limits import MachinePolicy
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
from app.modules.workspace.services.machine_policy import effective
from app.modules.workspace.services.pod_machines import (
    bundle_refusals,
    size_to_create,
)

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

#: A Docker deployment whose plan allows machines up to 2x4.
_ON_DOCKER = effective(MachinePolicy(max_size="2x4"), fabric=MachineFabric.DOCKER)


def _refusals(*machines: tuple[str, str, str | None]) -> list[str]:
    return bundle_refusals(_ON_DOCKER, list(machines), {"qa": _EXISTING})


def test_a_template_the_deployment_cannot_build_is_refused():
    refusals = _refusals(("ci", "workspace-docker", "4x8"))

    assert len(refusals) == 1
    assert refusals[0].startswith("Machine 'ci':")
    assert "workspace-docker" in refusals[0]


def test_with_no_size_it_is_still_refused():
    assert _refusals(("ci", "workspace-docker", None))


def test_a_size_over_the_plan_is_refused():
    assert _refusals(("big", "workspace", "4x8"))


def test_what_the_plan_allows_passes():
    assert _refusals(("small", "workspace", None), ("medium", "workspace", "2x4")) == []


def test_an_existing_machine_keeps_its_template():
    """The apply never changes an existing machine's template, so a bundle
    naming one is not refused for it -- only a size it would change to."""
    assert _refusals(("qa", "workspace", None)) == []
    assert _refusals(("qa", "workspace", "4x8")) == []
    assert _refusals(("qa", "workspace", "8x16"))


@pytest.mark.parametrize("bad", [("odd", "gpu", None), ("odd", "workspace", "huge")])
def test_what_is_not_a_template_or_a_size_is_refused(bad):
    assert _refusals(bad)


def test_the_size_a_new_machine_gets():
    policy = effective(MachinePolicy(), fabric=MachineFabric.E2B)

    assert (
        size_to_create(policy, MachineTemplate.WORKSPACE, None) == DEFAULT_MACHINE_SIZE
    )
    assert size_to_create(
        policy, MachineTemplate.WORKSPACE_DOCKER, None
    ) == MachineSize(4, 8)
