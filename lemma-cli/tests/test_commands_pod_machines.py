"""`lemma machine create|update|delete|grants`, and the import's policy check."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import typer

from lemma_cli.cli_app.pod_bundle_machines import (
    machine_policy_issues,
    offered_sizes,
)
from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import machine
from lemma_cli.cli_core.commands.machine import split_pod_ref

POD_ID = "11111111-1111-1111-1111-111111111111"


class FakeMachines:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, verb: str):
        def call(*args: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls.append((verb, args, kwargs))
            return {"verb": verb, "args": list(args), **kwargs}

        return call


def _client(machines: FakeMachines) -> SimpleNamespace:
    return SimpleNamespace(machines=machines)


@pytest.fixture
def machines(patch_run, json_state) -> FakeMachines:
    fake = FakeMachines()
    json_state.config["_runtime"]["pod"] = POD_ID
    patch_run(machine, client=_client(fake), state=json_state)
    return fake


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("pod:ci", (None, "ci")),
        ("pod:research:ci", ("research", "ci")),
        ("pod:research/ci", ("research", "ci")),
        (f"pod:{POD_ID}:ci", (POD_ID, "ci")),
    ],
)
def test_a_pod_ref_splits_into_its_pod_and_name(ref, expected) -> None:
    assert split_pod_ref(ref) == expected


@pytest.mark.parametrize("ref", ["me", "mine:mac", "pod:"])
def test_only_a_pod_machine_can_be_managed_here(ref) -> None:
    with pytest.raises(typer.BadParameter):
        split_pod_ref(ref)


def test_create_in_the_selected_pod(runner, machines) -> None:
    result = runner.invoke(
        app,
        [
            "machine",
            "create",
            "pod:ci",
            "--template",
            "workspace-docker",
            "--size",
            "4x8",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert machines.calls == [
        (
            "create",
            ("ci",),
            {
                "pod_id": POD_ID,
                "template": "workspace-docker",
                "size": "4x8",
                "run_slots": None,
                "display_name": None,
            },
        )
    ]


def test_the_refs_own_pod_wins(runner, machines) -> None:
    other = "22222222-2222-2222-2222-222222222222"

    result = runner.invoke(
        app, ["machine", "update", f"pod:{other}:ci", "--size", "2x4"]
    )

    assert result.exit_code == 0, result.stderr
    assert machines.calls[0][2]["pod_id"] == other


def test_an_unknown_template_is_a_usage_error(runner, machines) -> None:
    result = runner.invoke(app, ["machine", "create", "pod:ci", "--template", "gpu"])

    assert result.exit_code == 2
    assert machines.calls == []


def test_update_needs_something_to_change(runner, machines) -> None:
    assert runner.invoke(app, ["machine", "update", "pod:ci"]).exit_code == 2


def test_delete_and_grants(runner, machines) -> None:
    deleted = runner.invoke(app, ["--json", "machine", "delete", "pod:ci", "-y"])
    listed = runner.invoke(app, ["machine", "grants", "list", "pod:ci"])
    granted = runner.invoke(
        app,
        ["machine", "grants", "set", "pod:ci", "--agent", "builder", "-y"],
    )

    assert [r.exit_code for r in (deleted, listed, granted)] == [0, 0, 0]
    assert json.loads(deleted.stdout) == {
        "pod_id": POD_ID,
        "name": "ci",
        "deleted": True,
    }
    assert machines.calls == [
        ("delete", ("ci",), {"pod_id": POD_ID}),
        ("grants", ("ci",), {"pod_id": POD_ID}),
        (
            "set_grants",
            ("ci",),
            {"pod_id": POD_ID, "agents": ["builder"], "functions": []},
        ),
    ]


@pytest.mark.parametrize(
    "args",
    [
        ["machine", "delete", "pod:ci"],
        ["machine", "grants", "set", "pod:ci", "--agent", "builder"],
    ],
)
def test_destructive_pod_machine_commands_require_yes(runner, args) -> None:
    refused = runner.invoke(app, args)

    assert refused.exit_code == 1
    assert "--yes" in refused.stderr


# --- pod import, before anything is written ----------------------------------

_DOCKER_DEPLOYMENT = {
    "default_size": "1x2",
    "templates": [{"template": "workspace", "sizes": ["1x2", "2x4"]}],
}


def _machine_dir(root: Path, name: str, **payload: Any) -> Path:
    machine_dir = root / "machines" / name
    machine_dir.mkdir(parents=True)
    (machine_dir / f"{name}.json").write_text(json.dumps({"name": name, **payload}))
    return machine_dir


def test_a_template_the_pod_is_not_offered_is_an_issue(tmp_path) -> None:
    ci = _machine_dir(tmp_path, "ci", template="workspace-docker", size="4x8")

    issues = machine_policy_issues(offered_sizes(_DOCKER_DEPLOYMENT), [ci], {})

    assert [issue.message for issue in issues] == [
        "Machine 'ci': this pod's plan does not offer workspace-docker machines "
        "here (it offers workspace)."
    ]


def test_a_size_not_offered_is_an_issue(tmp_path) -> None:
    big = _machine_dir(tmp_path, "big", template="workspace", size="8x16")

    issues = machine_policy_issues(offered_sizes(_DOCKER_DEPLOYMENT), [big], {})

    assert "come in 1x2, 2x4, not 8x16" in issues[0].message


def test_an_existing_machine_keeps_its_template(tmp_path) -> None:
    qa = _machine_dir(tmp_path, "qa", template="workspace-docker")
    existing = {"qa": {"name": "qa", "template": "workspace", "size": "2x4"}}

    assert (
        machine_policy_issues(offered_sizes(_DOCKER_DEPLOYMENT), [qa], existing) == []
    )


def test_what_the_plan_offers_passes(tmp_path) -> None:
    dirs = [
        _machine_dir(tmp_path, "a", template="workspace"),
        _machine_dir(tmp_path, "b", template="workspace", size="2x4"),
    ]

    assert machine_policy_issues(offered_sizes(_DOCKER_DEPLOYMENT), dirs, {}) == []


def test_list_shows_the_selected_pods_machines_too(runner, machines) -> None:
    """QA: with a pod bound, `machine list` showed only the person's own
    machine -- the pod's machines were never asked for."""
    result = runner.invoke(app, ["machine", "list"])

    assert result.exit_code == 0, result.stderr
    assert machines.calls == [("list", (), {"pod_id": POD_ID})]
