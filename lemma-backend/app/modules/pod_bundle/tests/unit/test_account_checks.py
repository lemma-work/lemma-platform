"""A schedule naming an account the importer cannot use is planned as skipped.

Exported before pod connectors, such a schedule names an account rather than
a pod connector. Applying it used to fail at the schedule's own step, after
every step before it had landed. The plan builder and the apply request now
ask first, over an :class:`AccountUsability` port (a fake here; production
asks the connectors module), and plan the schedule as skipped with a warning.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

from app.modules.pod_bundle.domain.state import (
    PlanStep,
    StepAction,
    StepKind,
    StepStatus,
)
from app.modules.pod_bundle.infrastructure.account_checks import (
    DEPRECATED_ACCOUNT_DETAIL,
    skip_unusable_accounts,
    steps_naming_an_account,
)
from app.modules.pod_bundle.infrastructure.plan_builder import PlanBuilder


class _Usable:
    """Refuses every account except ``usable``; records what it was asked."""

    def __init__(self, *usable: UUID):
        self._usable = set(usable)
        self.asked: list[UUID] = []

    async def refusal(self, account_id: UUID) -> str | None:
        self.asked.append(account_id)
        return None if account_id in self._usable else "it is not yours"


class _NoResources:
    async def table_names(self):
        return set()

    async def table_manifest(self, name):
        return None

    async def function_names(self):
        return set()

    async def agent_names(self):
        return set()

    async def workflow_names(self):
        return set()

    async def schedule_names(self):
        return set()

    async def app_names(self):
        return set()

    async def surface_names(self):
        return set()

    async def machine_names(self):
        return set()


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(tmp_path: Path, account_id: str) -> Path:
    root = tmp_path / "bundle"
    _write(root / "pod.json", {"name": "x", "variables": {"acct": {"type": "account"}}})
    _write(root / "tables" / "tickets" / "tickets.json", {"name": "tickets"})
    _write(
        root / "schedules" / "on_ticket" / "on_ticket.json",
        {
            "name": "on_ticket",
            "schedule_type": "WEBHOOK",
            "account_id": account_id,
            "connector_trigger_id": "jira_new_issue",
        },
    )
    return root


def _schedule(plan) -> PlanStep:
    [step] = [s for s in plan.steps if s.kind is StepKind.SCHEDULE]
    return step


async def test_an_account_written_into_the_bundle_that_cannot_be_used_is_skipped(
    tmp_path,
):
    theirs = uuid4()
    check = _Usable()
    plan = await PlanBuilder(_NoResources(), accounts=check).build_plan(
        bundle_root=_bundle(tmp_path, str(theirs))
    )

    step = _schedule(plan)
    assert (step.action, step.status) == (StepAction.SKIP, StepStatus.SKIPPED)
    assert "it is not yours" in (step.error or "")
    assert any("on_ticket" in w and str(theirs) in w for w in plan.warnings)
    assert check.asked == [theirs]
    # Everything else still imports.
    [table] = [s for s in plan.steps if s.kind is StepKind.TABLE]
    assert table.status is StepStatus.PENDING


async def test_an_account_the_importer_may_use_is_planned_as_before(tmp_path):
    mine = uuid4()
    plan = await PlanBuilder(_NoResources(), accounts=_Usable(mine)).build_plan(
        bundle_root=_bundle(tmp_path, str(mine))
    )
    step = _schedule(plan)
    assert (step.action, step.status) == (StepAction.CREATE, StepStatus.PENDING)
    assert step.detail == {DEPRECATED_ACCOUNT_DETAIL: str(mine)}


async def test_an_account_given_as_a_variable_is_checked_once_it_is_known(tmp_path):
    """Planning cannot know the variable; the apply request can, and plans the
    schedule as skipped then, before anything is queued."""
    check = _Usable()
    plan = await PlanBuilder(_NoResources(), accounts=check).build_plan(
        bundle_root=_bundle(tmp_path, "${acct}")
    )
    step = _schedule(plan)
    assert step.status is StepStatus.PENDING
    assert check.asked == []
    assert steps_naming_an_account(plan.steps) == [step]

    theirs = uuid4()
    warnings: list[str] = []
    await skip_unusable_accounts(
        plan.steps, warnings, replacements={"acct": str(theirs)}, check=check
    )
    assert (step.action, step.status) == (StepAction.SKIP, StepStatus.SKIPPED)
    assert check.asked == [theirs]
    assert len(warnings) == 1


async def test_an_unset_variable_asks_nothing():
    """Left empty, the schedule step itself leaves the schedule out and says so."""
    step = PlanStep(
        index=0,
        kind=StepKind.SCHEDULE,
        name="on_ticket",
        action=StepAction.CREATE,
        detail={DEPRECATED_ACCOUNT_DETAIL: "${acct}"},
    )
    check = _Usable()
    await skip_unusable_accounts([step], [], replacements={}, check=check)
    assert check.asked == []
    assert step.status is StepStatus.PENDING
