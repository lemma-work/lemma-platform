"""A bundled machine the target cannot build fails the plan, not the apply.

`workspace-docker` is a valid template name that only an E2B deployment can
build; bundle validation checks the name, and the apply used to find out
partway through, with every step before the machine already written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.domain.state import StepAction, StepKind
from app.modules.pod_bundle.infrastructure.plan_builder import PlanBuilder
from app.modules.pod_bundle.tests.unit.test_plan_builder import FakeExisting

pytestmark = pytest.mark.unit


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(tmp_path: Path, machines: dict[str, dict]) -> Path:
    root = tmp_path / "bundle"
    _write(root / "pod.json", {"name": "CI", "format_version": 2})
    _write(root / "agents" / "builder" / "builder.json", {"name": "builder"})
    for name, payload in machines.items():
        _write(root / "machines" / name / f"{name}.json", {"name": name, **payload})
    return root


async def test_a_machine_the_target_refuses_fails_the_plan(tmp_path):
    root = _bundle(
        tmp_path,
        {
            "ci": {"template": "workspace-docker", "size": "4x8"},
            "qa": {"template": "workspace"},
        },
    )
    existing = FakeExisting()
    existing.refused_templates = {"workspace-docker"}

    with pytest.raises(BundleInvalidError) as refused:
        await PlanBuilder(existing).build_plan(bundle_root=root)

    assert "Machine 'ci'" in refused.value.message
    assert "qa" not in refused.value.message
    assert refused.value.details == {
        "machines": ["Machine 'ci': not on this deployment."]
    }


async def test_each_machine_is_checked_as_the_apply_will_ask_for_it(tmp_path):
    root = _bundle(
        tmp_path,
        {"ci": {"template": "workspace-docker", "size": "4x8"}, "qa": {}},
    )
    existing = FakeExisting(machines={"qa"})

    plan = await PlanBuilder(existing).build_plan(bundle_root=root)

    assert existing.machines_checked == [
        ("ci", "workspace-docker", "4x8"),
        ("qa", "workspace", None),
    ]
    steps = {s.name: s.action for s in plan.steps if s.kind is StepKind.MACHINE}
    assert steps == {"ci": StepAction.CREATE, "qa": StepAction.UPDATE}
