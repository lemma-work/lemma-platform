"""A plan's templates, narrowed to what the deployment's fabric can build."""

from __future__ import annotations

import pytest

from app.core.ports.plan_limits import MachinePolicy
from app.modules.workspace.domain.machine import (
    MachineFabric,
    MachineSize,
    MachineTemplate,
)
from app.modules.workspace.domain.machine_errors import MachinePolicyRefused
from app.modules.workspace.services.machine_policy import effective


def test_docker_machines_are_offered_on_e2b():
    policy = effective(MachinePolicy(), fabric=MachineFabric.E2B)

    assert MachineTemplate.WORKSPACE_DOCKER in policy.allowed_templates
    assert policy.sizes_for(MachineTemplate.WORKSPACE_DOCKER)


def test_docker_machines_are_not_offered_where_they_cannot_be_built():
    policy = effective(MachinePolicy(), fabric=MachineFabric.DOCKER)

    assert policy.allowed_templates == {MachineTemplate.WORKSPACE}
    assert policy.sizes_for(MachineTemplate.WORKSPACE_DOCKER) == ()
    with pytest.raises(MachinePolicyRefused):
        policy.check(MachineTemplate.WORKSPACE_DOCKER, MachineSize(4, 8))
