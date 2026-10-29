"""Planning a bundle's agent and function grants against what the importer holds.

The plan builder runs over a fake :class:`GrantConferral`, so what is tested is
the plan's own decisions: which steps are asked about, as whom, what is kept on
the step and repeated as a warning, and that applying such a plan is refused.
The conferral bound itself is the authorization module's, and its e2e covers
the real answer (``test_import_grant_bounds_e2e``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.pod_bundle.domain.errors import BundleGrantsRefusedError
from app.modules.pod_bundle.domain.state import StepKind
from app.modules.pod_bundle.infrastructure.grant_planning import (
    refuse_unconferrable_grants,
)
from app.modules.pod_bundle.infrastructure.plan_builder import PlanBuilder


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


class _Conferral:
    """Refuses every grant carrying a permission in ``refuse``."""

    def __init__(self, refuse: set[str]):
        self._refuse = refuse
        self.asked: list[tuple[str, str]] = []

    async def refusals(self, grants, *, grantee_type, grantee_name):
        self.asked.append((grantee_type, grantee_name))
        return [
            f"{permission} on {grant.resource_type.value} '{grant.resource_name}'"
            for grant in grants
            for permission in grant.permission_ids
            if permission in self._refuse
        ]


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _grant(permission: str) -> dict:
    return {
        "permissions": {
            "grants": [
                {
                    "resource_type": "datastore_table",
                    "resource_name": "ledger",
                    "permission_ids": [permission],
                }
            ]
        }
    }


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    _write(root / "pod.json", {"name": "grants", "format_version": 4})
    _write(
        root / "agents" / "cleaner" / "cleaner.json",
        {"name": "cleaner", "instruction": "Tidy.", **_grant("datastore.table.delete")},
    )
    _write(
        root / "functions" / "reader" / "reader.json",
        {"name": "reader", **_grant("datastore.record.read")},
    )
    return root


async def test_agent_and_function_grants_are_asked_about_while_planning(tmp_path):
    conferral = _Conferral(refuse={"datastore.table.delete"})

    plan = await PlanBuilder(_NoResources(), grants=conferral).build_plan(
        bundle_root=_bundle(tmp_path)
    )

    assert sorted(conferral.asked) == [("AGENT", "cleaner"), ("FUNCTION", "reader")]
    steps = {(s.kind, s.name): s for s in plan.steps}
    refused = steps[(StepKind.AGENT_GRANTS, "cleaner")]
    assert refused.detail["refused"] == [
        "datastore.table.delete on datastore_table 'ledger'"
    ]
    assert "datastore.table.delete" in (refused.error or "")
    assert "refused" not in steps[(StepKind.FUNCTION_GRANTS, "reader")].detail
    assert any("cleaner" in w and "refused" in w for w in plan.warnings)

    with pytest.raises(BundleGrantsRefusedError) as raised:
        refuse_unconferrable_grants(plan)
    assert raised.value.details == {
        "refused": [
            "agent 'cleaner': datastore.table.delete on datastore_table 'ledger'"
        ]
    }


async def test_a_plan_with_nothing_refused_applies(tmp_path):
    plan = await PlanBuilder(_NoResources(), grants=_Conferral(set())).build_plan(
        bundle_root=_bundle(tmp_path)
    )

    refuse_unconferrable_grants(plan)
    assert plan.grant_refusals == []
