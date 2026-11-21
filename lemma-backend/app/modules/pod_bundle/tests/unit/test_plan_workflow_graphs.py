"""The import plan refuses a workflow graph its create would refuse, before
anything is written -- a LOOP with two outgoing edges planned fine and then
failed its WORKFLOW step with the steps before it already applied."""

import json
from pathlib import Path

import pytest

from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.domain.state import StepKind
from app.modules.pod_bundle.infrastructure.plan_builder import PlanBuilder


class NothingYet:
    """A pod with no resources."""

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

    async def existing_paths(self, paths):
        return set()


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(tmp_path: Path, *, edges: list[dict], variables=None, body="work") -> Path:
    root = tmp_path / "bundle"
    _write(
        root / "pod.json",
        {"name": "Digest", "format_version": 2, "variables": variables or {}},
    )
    _write(root / "tables" / "items" / "items.json", {"name": "items", "columns": []})
    _write(
        root / "workflows" / "digest" / "digest.json",
        {
            "name": "digest",
            "nodes": [
                {
                    "id": "each",
                    "type": "LOOP",
                    "config": {"items_path": "start.items", "child_node_id": body},
                },
                {"id": "work", "type": "FUNCTION", "config": {"function_name": "f"}},
                {"id": "after", "type": "END"},
            ],
            "edges": edges,
        },
    )
    return root


def _edge(source: str, target: str) -> dict:
    return {"id": f"{source}-{target}", "source": source, "target": target}


@pytest.mark.asyncio
async def test_a_loop_with_two_outgoing_edges_is_refused_at_plan_time(tmp_path):
    root = _bundle(tmp_path, edges=[_edge("each", "work"), _edge("each", "after")])

    with pytest.raises(BundleInvalidError) as refused:
        await PlanBuilder(NothingYet()).build_plan(bundle_root=root)

    message = str(refused.value)
    assert "nothing was imported" in message
    assert "Workflow 'digest'" in message
    assert "loop 'each' has 2 outgoing edges" in message


@pytest.mark.asyncio
async def test_a_valid_loop_plans_its_workflow(tmp_path):
    root = _bundle(tmp_path, edges=[_edge("each", "after")])

    plan = await PlanBuilder(NothingYet()).build_plan(bundle_root=root)

    assert [s.name for s in plan.steps if s.kind is StepKind.WORKFLOW] == ["digest"]


@pytest.mark.asyncio
async def test_a_placeholder_is_judged_with_its_default(tmp_path):
    # Filled with its default the body is `work`; left as written it names no
    # node, and the plan would refuse a graph the apply accepts.
    root = _bundle(
        tmp_path,
        edges=[_edge("each", "after")],
        variables={"body": {"default": "work"}},
        body="${body}",
    )

    plan = await PlanBuilder(NothingYet()).build_plan(bundle_root=root)

    assert any(s.kind is StepKind.WORKFLOW for s in plan.steps)
