"""The CLI's own import carries what a bundle's pod owns, as the server's does."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from lemma_sdk.errors import LemmaAPIError

from lemma_cli.cli_app.pod_bundle_outcome import (
    bundle_outcome,
    carry_outcome,
    plan_outcome,
)

BLOCK = {
    "sentence": "Every customer hears back within a day.",
    "kind": "week",
    "target": 90,
    "unit": "%",
    "measure": "SELECT 90",
}


class FakePods:
    def __init__(
        self,
        owned: dict[str, Any] | None = None,
        refuse: set[str] | None = None,
        status: int = 400,
    ):
        self.owned = owned
        self.refuse = refuse or set()
        self.status = status
        self.sets: list[dict[str, Any]] = []

    def outcome_standing(self, pod_id: str) -> dict[str, Any]:
        return {"outcome": self.owned, "value": None, "problem": None}

    def set_outcome(self, pod_id: str, request: Any) -> dict[str, Any]:
        body = request.to_dict()
        if "measure" in self.refuse and body.get("measure"):
            raise LemmaAPIError(
                status_code=self.status, message="the measure could not be read"
            )
        if "all" in self.refuse:
            raise LemmaAPIError(status_code=self.status, message="refused")
        self.sets.append(body)
        self.owned = body
        return {"outcome": body}


def _client(pods: FakePods) -> Any:
    return SimpleNamespace(pods=pods)


def _bundle(tmp_path: Path, outcome: dict[str, Any] | None) -> Path:
    manifest: dict[str, Any] = {"format_version": 3, "name": "Support"}
    if outcome is not None:
        manifest["outcome"] = outcome
    (tmp_path / "pod.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def test_a_bundle_without_one_carries_nothing(tmp_path: Path) -> None:
    assert bundle_outcome(_bundle(tmp_path, None)) is None
    pods = FakePods()
    assert carry_outcome(_client(pods), "pod", None) == ([], [])
    assert pods.sets == []


def test_a_pod_that_owns_nothing_takes_the_bundles(tmp_path: Path) -> None:
    block = bundle_outcome(_bundle(tmp_path, BLOCK))
    pods = FakePods()
    assert plan_outcome(_client(pods), "pod", block) == [f"set:{BLOCK['sentence']}"]
    lines, notes = carry_outcome(_client(pods), "pod", block)
    assert lines == [f"set:{BLOCK['sentence']}"] and notes == []
    assert pods.sets[0]["measure"] == "SELECT 90"


def test_a_pod_that_owns_something_else_keeps_it(tmp_path: Path) -> None:
    block = bundle_outcome(_bundle(tmp_path, BLOCK))
    pods = FakePods(
        owned={"sentence": "Every lead is followed up in two days.", "kind": "week"}
    )
    assert plan_outcome(_client(pods), "pod", block)[0].startswith("kept:")
    lines, notes = carry_outcome(_client(pods), "pod", block)
    assert lines[0].startswith("kept:") and "was not set" in notes[0]
    assert pods.sets == []


def test_the_same_promise_is_left_alone_and_said_nothing_about(tmp_path: Path) -> None:
    block = bundle_outcome(_bundle(tmp_path, BLOCK))
    pods = FakePods(owned={**BLOCK, "target": 90.0, "measure": None})
    assert carry_outcome(_client(pods), "pod", block) == (
        [f"same:{BLOCK['sentence']}"],
        [],
    )


def test_an_unreadable_measure_keeps_the_rest(tmp_path: Path) -> None:
    block = bundle_outcome(_bundle(tmp_path, BLOCK))
    pods = FakePods(refuse={"measure"})
    lines, notes = carry_outcome(_client(pods), "pod", block)
    assert lines == [f"set:{BLOCK['sentence']}"]
    assert "without its measure" in notes[0]
    assert "measure" not in pods.sets[0]


def test_a_server_that_keeps_nothing_says_so(tmp_path: Path) -> None:
    block = bundle_outcome(_bundle(tmp_path, BLOCK))
    pods = FakePods(refuse={"all"}, status=404)
    lines, notes = carry_outcome(_client(pods), "pod", block)
    assert lines == [] and "does not keep" in notes[0]
