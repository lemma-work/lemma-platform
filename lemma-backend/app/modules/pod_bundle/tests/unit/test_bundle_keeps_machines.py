"""An import adds to a pod and never replaces what is there: machines and the
agents that name them, at apply as well as in the plan (``kept_parts``)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.pod_bundle.domain.state import PlanStep, StepAction, StepKind
from app.modules.pod_bundle.infrastructure.agent_apply import (
    apply_agent,
    apply_machine,
)

pytestmark = pytest.mark.asyncio

_MACHINES = "app.modules.workspace.contracts.pod_machines"
_AGENTS = "app.modules.agent.contracts.provisioning"


def _step(kind: StepKind, name: str) -> PlanStep:
    return PlanStep(index=0, kind=kind, name=name, action=StepAction.CREATE)


async def test_a_machine_the_pod_already_has_is_left_as_it_is(monkeypatch) -> None:
    """Its size, template and run slots were someone's choice since it arrived."""
    upserted: list[dict] = []

    async def names(uow, *, pod_id):
        return {"builder"}

    async def upsert(uow, **kwargs):
        upserted.append(kwargs)

    monkeypatch.setattr(f"{_MACHINES}.list_pod_machine_names", names)
    monkeypatch.setattr(f"{_MACHINES}.upsert_pod_machine", upsert)

    await apply_machine(
        _step(StepKind.MACHINE, "builder"),
        uow=object(),
        pod_id=uuid4(),
        user_id=uuid4(),
        load=lambda kind, name: {"template": "workspace", "size": "8x16"},
    )

    assert upserted == []


async def test_an_agent_the_pod_already_has_keeps_its_machine(monkeypatch) -> None:
    written: list[str] = []

    async def existing(uow, *, pod_id, name, ctx=None):
        return SimpleNamespace(id=uuid4(), name=name, machine="pod:theirs")

    async def create(uow, **kwargs):
        written.append("create")

    monkeypatch.setattr(f"{_AGENTS}.get_agent", existing)
    monkeypatch.setattr(f"{_AGENTS}.create_agent", create)

    await apply_agent(
        _step(StepKind.AGENT, "assistant"),
        uow=object(),
        ctx=object(),
        pod_id=uuid4(),
        user_id=uuid4(),
        load=lambda kind, name: {"instruction": "new", "machine": "pod:ours"},
    )

    assert written == []
