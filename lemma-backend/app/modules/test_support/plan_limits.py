"""A plan whose answers a test sets, declared the way a deployment declares one.

Implements `PlanLimits` rather than patching anything: the code under test asks
the provider it was assembled with, exactly as it does in production, and gets
whatever the test wrote here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import pytest_asyncio

from app.core.plan_limits import declare_plan_limits, reset_plan_limits
from app.core.ports.plan_limits import MachinePolicy, PodAllowance


@dataclass
class SetPlan:
    """Answers with whatever the test last set. ``None`` is unlimited."""

    pods: PodAllowance | None = None
    organizations: int | None = None
    members: int | None = None
    machines: MachinePolicy | None = None

    async def pod_allowance(
        self, *, user_id: UUID, organization_id: UUID
    ) -> PodAllowance | None:
        return self.pods

    async def organization_limit(self, *, user_id: UUID) -> int | None:
        return self.organizations

    async def member_limit(self, *, organization_id: UUID) -> int | None:
        return self.members

    async def machine_policy(
        self, *, user_id: UUID | None, organization_id: UUID | None
    ) -> MachinePolicy | None:
        return self.machines


@pytest_asyncio.fixture
async def plan(test_app: object) -> AsyncIterator[SetPlan]:
    """A plan in force for one test.

    Depends on `test_app` because building the app runs assembly, which
    declares whatever the module list declares -- nothing, in the open-source
    list -- and would overwrite a plan declared before it.
    """
    del test_app
    answers = SetPlan()
    declare_plan_limits(lambda uow: answers)
    try:
        yield answers
    finally:
        reset_plan_limits()
