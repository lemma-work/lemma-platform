"""A plan's machine policy, in the workspace module's own terms, and its checks.

The plan hook (`PlanLimits.machine_policy`) states a policy in strings so core
need not know what a machine size is. This parses it once, and is where every
"may this machine exist / be this size / start now" question is asked. The
numbers all come from the hook; a deployment without plans gets the defaults
below, which limit nothing but the sizes templates are built at.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.plan_limits import build_plan_limits
from app.core.ports.plan_limits import MachinePolicy
from app.modules.workspace.domain.machine import (
    DEFAULT_MACHINE_SIZE,
    KNOWN_SIZES,
    TEMPLATE_SIZES,
    MachineFabric,
    MachineSize,
    MachineTemplate,
    template_builds_on,
)
from app.modules.workspace.domain.machine_errors import MachinePolicyRefused


@dataclass(frozen=True, slots=True)
class EffectivePolicy:
    default_size: MachineSize
    max_size: MachineSize
    allowed_templates: frozenset[MachineTemplate]
    max_pod_machines: int | None
    max_connected_machines: int | None
    max_running: int | None

    def sizes_for(self, template: MachineTemplate) -> tuple[MachineSize, ...]:
        """What the screen offers: the template's sizes within the plan's max."""
        if template not in self.allowed_templates:
            return ()
        return tuple(
            size
            for size in TEMPLATE_SIZES[template]
            if size.cpu_count <= self.max_size.cpu_count
        )

    def check(self, template: MachineTemplate, size: MachineSize) -> None:
        if template not in self.allowed_templates:
            raise MachinePolicyRefused(
                f"Your plan does not include {template.value} machines.",
                code="MACHINE_TEMPLATE_NOT_ALLOWED",
            )
        if size not in TEMPLATE_SIZES[template]:
            offered = ", ".join(s.label for s in TEMPLATE_SIZES[template])
            raise MachinePolicyRefused(
                f"{template.value} machines come in {offered}.",
                code="MACHINE_SIZE_NOT_OFFERED",
            )
        if size.cpu_count > self.max_size.cpu_count:
            raise MachinePolicyRefused(
                f"Your plan allows machines up to {self.max_size.label}.",
                code="MACHINE_SIZE_NOT_ALLOWED",
            )


def effective(
    policy: MachinePolicy | None, *, fabric: MachineFabric | None = None
) -> EffectivePolicy:
    """The plan's policy, narrowed to what this deployment's fabric can build.

    A plan may sell `workspace-docker`; a deployment on Docker still cannot
    make one, and offering it there is a machine that says it has Docker and
    has none.
    """
    stated = policy or MachinePolicy()
    on = fabric or _deployment_fabric()
    return EffectivePolicy(
        default_size=_size(stated.default_size, DEFAULT_MACHINE_SIZE),
        max_size=_size(stated.max_size, KNOWN_SIZES[-1]),
        allowed_templates=frozenset(
            template
            for template in MachineTemplate
            if template.value in stated.allowed_templates
            and template_builds_on(template, on)
        ),
        max_pod_machines=stated.max_pod_machines,
        max_connected_machines=stated.max_connected_machines,
        max_running=stated.max_running,
    )


def _deployment_fabric() -> MachineFabric:
    from app.modules.workspace.config import workspace_settings

    try:
        return MachineFabric(workspace_settings.provider)
    except ValueError:
        return MachineFabric.DOCKER


def _size(label: str, fallback: MachineSize) -> MachineSize:
    try:
        return MachineSize.parse(label)
    except ValueError:
        return fallback


async def policy_for_user(uow: SqlAlchemyUnitOfWork, user_id: UUID) -> EffectivePolicy:
    limits = build_plan_limits(uow)
    if limits is None:
        return effective(None)
    return effective(await limits.machine_policy(user_id=user_id, organization_id=None))


async def policy_for_org(
    uow: SqlAlchemyUnitOfWork, organization_id: UUID | None
) -> EffectivePolicy:
    limits = build_plan_limits(uow)
    if limits is None or organization_id is None:
        return effective(None)
    return effective(
        await limits.machine_policy(user_id=None, organization_id=organization_id)
    )
