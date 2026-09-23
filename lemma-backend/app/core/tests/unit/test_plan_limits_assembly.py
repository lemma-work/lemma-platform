"""Which module answers for plans, decided by the module list and nothing else."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

import pytest

from app.core.plan_limits import build_plan_limits, reset_plan_limits
from app.core.ports.plan_limits import PodAllowance, SandboxSize
from app.core.registry.assembly import configure_plan_limits
from app.core.registry.contract import LemmaModule


@dataclass
class SetPlan:
    members: int | None = None
    organizations: int | None = None

    async def pod_allowance(
        self, *, user_id: UUID, organization_id: UUID
    ) -> PodAllowance | None:
        return None

    async def organization_limit(self, *, user_id: UUID) -> int | None:
        return self.organizations

    async def member_limit(self, *, organization_id: UUID) -> int | None:
        return self.members

    async def workspace_size(self, *, user_id: UUID) -> SandboxSize | None:
        return None


@pytest.fixture(autouse=True)
def _no_plan_leaks() -> Iterator[None]:
    reset_plan_limits()
    yield
    reset_plan_limits()


def _selling(name: str, answers: SetPlan) -> LemmaModule:
    return LemmaModule(name=name, plan_limits=lambda: lambda uow: answers)


def test_a_list_that_sells_nothing_limits_nothing():
    configure_plan_limits([LemmaModule(name="pod"), LemmaModule(name="identity")])

    assert build_plan_limits(object()) is None  # type: ignore[arg-type]


def test_the_module_that_sells_plans_answers_for_them():
    answers = SetPlan(members=5)

    configure_plan_limits([LemmaModule(name="pod"), _selling("billing", answers)])

    assert build_plan_limits(object()) is answers  # type: ignore[arg-type]


def test_two_modules_selling_plans_is_refused_rather_than_decided_by_order():
    with pytest.raises(RuntimeError, match="billing, other_billing"):
        configure_plan_limits(
            [_selling("billing", SetPlan()), _selling("other_billing", SetPlan())]
        )


def test_assembling_again_without_a_seller_forgets_the_last_one():
    """The worker and the API each assemble; a list without a seller must not
    inherit one declared by an earlier assembly in the same process."""
    configure_plan_limits([_selling("billing", SetPlan())])

    configure_plan_limits([LemmaModule(name="pod")])

    assert build_plan_limits(object()) is None  # type: ignore[arg-type]
