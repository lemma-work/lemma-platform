"""What a deployment's plans allow: how many organizations, pods and members,
and which machines.

Three modules enforce these -- `pod` when a pod is made, `identity` when an
organization is made or someone joins or is invited, `workspace` when a machine
is created, started or built -- and none of them
knows what a plan is. The open-source build has no plans, so it declares no
provider and nothing is limited. A deployment that sells plans (lemma.work)
declares one through `LemmaModule.plan_limits`, and answers each question from
whatever the person or organization is paying for.

The provider only *answers*. Counting and refusing stay with the module that
owns the rows, under its own lock, so the rule is enforced the same way whoever
supplies the numbers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


@dataclass(frozen=True, slots=True)
class PodAllowance:
    """How many pods a person may own, and where their pods count.

    Counted per person, not per organization: every member of an organization
    is given a pod of their own when they join, so a per-organization cap would
    be exceeded by the people in it rather than by anything they chose to make.

    ``excluded_organization_ids`` are organizations whose pods are paid for some
    other way -- a team plan, say -- and so do not count against this person.
    """

    limit: int
    excluded_organization_ids: frozenset[UUID] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class SandboxSize:
    """The compute a workspace sandbox is built with."""

    cpu_count: int
    memory_mb: int

    def __post_init__(self) -> None:
        if self.cpu_count < 1 or self.memory_mb < 1:
            raise ValueError("a sandbox needs at least one CPU and some memory")

    @property
    def label(self) -> str:
        """Stable, human-readable, and what an E2B template is keyed by."""
        return f"{self.cpu_count}x{self.memory_mb}"


@dataclass(frozen=True, slots=True)
class MachinePolicy:
    """Which machines a person or organization may have, and how big.

    Sizes are ``<vcpu>x<memory GB>`` labels (``2x4``) and templates are names
    (``workspace``, ``workspace-docker``), so core does not have to know the
    workspace module's vocabulary to state a plan. ``None`` on a count is
    unlimited. How much compute they may *use* is a usage limit, not this:
    see ``UsageLimitValues``.
    """

    default_size: str = "2x4"
    max_size: str = "8x16"
    allowed_templates: frozenset[str] = frozenset({"workspace", "workspace-docker"})
    max_pod_machines: int | None = None
    max_connected_machines: int | None = None
    #: Machines running at once for this person (default and connected) or,
    #: for an organization, across its pods.
    max_running: int | None = None


class PlanLimits(Protocol):
    """Answers what a person or organization is allowed. ``None`` is unlimited,
    or for machines, the deployment's own default policy."""

    async def pod_allowance(
        self, *, user_id: UUID, organization_id: UUID
    ) -> PodAllowance | None:
        """For ``user_id`` making a pod in ``organization_id``."""
        ...

    async def organization_limit(self, *, user_id: UUID) -> int | None:
        """The most organizations ``user_id`` may own, counting every one."""
        ...

    async def member_limit(self, *, organization_id: UUID) -> int | None:
        """The most people ``organization_id`` may hold, invitations included."""
        ...

    async def machine_policy(
        self, *, user_id: UUID | None, organization_id: UUID | None
    ) -> MachinePolicy | None:
        """For ``user_id``'s own machines, or ``organization_id``'s pod
        machines when ``user_id`` is None. ``None`` is the deployment default."""
        ...


#: Builds the answerer for one unit of work, so a provider that reads plans
#: from the database reads them in the caller's transaction.
type PlanLimitsFactory = Callable[[SqlAlchemyUnitOfWork], PlanLimits]
