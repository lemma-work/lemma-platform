"""What a bundle's pod owns, set on the pod an import lands in.

``carry_outcome`` against a stand-in pod: set when the pod owns nothing, kept
when it owns something, set without a measure the importer cannot read. Then
the import: the apply job carries the block once its steps have landed, and a
template's is left alone, since the template's start sets that one.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.pod.contracts.outcome import OutcomeInput, PodOutcome
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    BundleSourceKind,
    ImportPlan,
    ImportState,
    ImportStatus,
)
from app.modules.pod_bundle.events import handlers
from app.modules.pod_bundle.infrastructure import bundle_outcome
from app.modules.pod_bundle.infrastructure.bundle_outcome import (
    carry_outcome,
    read_bundle_outcome,
)

_BLOCK = {
    "sentence": "Every lead hears back within two days.",
    "kind": "week",
    "target": 90,
    "unit": "%",
    "measure": "SELECT 100.0 * count(*) FILTER (WHERE answered) / count(*) FROM leads",
}


class _Pod:
    """The pod an import lands in: what it owns, and which measures it can read."""

    def __init__(
        self, owned: PodOutcome | None = None, *, refuse: str | None = None
    ) -> None:
        self.outcome = owned
        self.refuse = refuse
        self.asked: list[OutcomeInput] = []

    async def owned(self) -> PodOutcome | None:
        return self.outcome

    async def set(self, outcome: OutcomeInput) -> None:
        self.asked.append(outcome)
        if self.refuse is not None and (
            outcome.measure is not None or self.refuse == "everything"
        ):
            raise DomainError("Table 'leads' not found", status_code=404)
        self.outcome = PodOutcome(**outcome.model_dump(), since=date(2026, 11, 7))


async def test_a_pod_that_owns_nothing_takes_the_bundles_outcome_measure_and_all():
    pod = _Pod()

    assert await carry_outcome(_BLOCK, pod) == []
    assert pod.outcome is not None
    assert pod.outcome.model_dump(include=set(_BLOCK)) == {**_BLOCK, "target": 90.0}


async def test_a_pod_that_owns_something_keeps_it_and_says_so():
    mine = PodOutcome(sentence="The launch ships on time.", kind="week")
    pod = _Pod(mine)

    notes = await carry_outcome(_BLOCK, pod)

    assert pod.outcome is mine
    assert pod.asked == []
    kept = (
        "Kept what this pod already owns ('The launch ships on time.'); the "
        "bundle's ('Every lead hears back within two days.') was not set."
    )
    assert notes == [kept]


async def test_a_measure_that_cannot_be_read_is_left_off_and_the_rest_is_kept():
    pod = _Pod(refuse="measure")

    notes = await carry_outcome(_BLOCK, pod)

    assert [asked.measure for asked in pod.asked] == [_BLOCK["measure"], None]
    assert pod.outcome is not None
    assert pod.outcome.sentence == _BLOCK["sentence"]
    assert pod.outcome.target == 90
    assert pod.outcome.measure is None
    assert len(notes) == 1
    assert "without its measure" in notes[0]
    assert "Table 'leads' not found" in notes[0]


async def test_an_apply_resumed_after_it_was_set_says_nothing_more():
    pod = _Pod(refuse="measure")
    await carry_outcome(_BLOCK, pod)
    pod.asked.clear()

    assert await carry_outcome(_BLOCK, pod) == []
    assert pod.asked == []


async def test_an_outcome_the_importer_cannot_set_at_all_is_reported_not_raised():
    pod = _Pod(refuse="everything")

    notes = await carry_outcome(_BLOCK, pod)

    assert pod.outcome is None
    assert notes == ["What this bundle's pod owns was not set: Table 'leads' not found"]


async def test_a_block_that_is_not_an_outcome_is_reported_not_raised():
    pod = _Pod()

    notes = await carry_outcome({"sentence": "Ships by spring.", "kind": "date"}, pod)

    assert pod.asked == []
    assert len(notes) == 1
    assert notes[0].startswith("What this bundle's pod owns was not set:")
    assert "due date" in notes[0]


def test_read_bundle_outcome(tmp_path: Path):
    assert read_bundle_outcome(tmp_path) is None
    (tmp_path / "pod.json").write_text(json.dumps({"name": "p"}))
    assert read_bundle_outcome(tmp_path) is None
    (tmp_path / "pod.json").write_text(json.dumps({"name": "p", "outcome": "x"}))
    assert read_bundle_outcome(tmp_path) is None
    (tmp_path / "pod.json").write_text(json.dumps({"name": "p", "outcome": _BLOCK}))
    assert read_bundle_outcome(tmp_path) == _BLOCK


# --- the apply job -------------------------------------------------------------


def _import(kind: BundleSourceKind, *, warnings: list[str] | None = None):
    return ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        source=BundleSource(kind=kind, url="https://lemma.test/b.zip"),
        status=ImportStatus.AWAITING_CONFIRMATION,
        plan=ImportPlan(format_version=3),
        warnings=warnings or [],
    )


class _Store:
    def __init__(self, state: ImportState) -> None:
        self.state = state

    async def get_import(self, import_id):
        return self.state

    async def save_import(self, state):
        state.touch()
        self.state = state


async def test_the_apply_job_carries_the_outcome_once_its_steps_have_landed(
    monkeypatch, tmp_path
):
    state = _import(BundleSourceKind.URL)
    staging = AsyncMock()
    staging.get_archive.return_value = b"zip-bytes"
    carried = AsyncMock()

    async def _extracted(archive, tmp):
        return tmp_path

    monkeypatch.setattr(
        handlers, "_worker_ctx", lambda: SimpleNamespace(uow_factory="uows")
    )
    monkeypatch.setattr(handlers, "get_pod_bundle_state_store", lambda: _Store(state))
    monkeypatch.setattr(handlers, "BundleStagingStorage", lambda: staging)
    monkeypatch.setattr(handlers, "extract_bundle_offloaded", _extracted)
    monkeypatch.setattr(handlers, "_resolve_import_replacements", AsyncMock())
    monkeypatch.setattr(handlers, "_record_recipe", AsyncMock())
    monkeypatch.setattr(handlers, "publish_bundle_event", AsyncMock())
    monkeypatch.setattr(
        handlers.bundle_analytics, "record_import_completed", AsyncMock()
    )
    monkeypatch.setattr(handlers.bundle_outcome, "carry", carried)

    await handlers.apply_pod_import(
        {
            "import_id": str(state.import_id),
            "pod_id": str(state.pod_id),
            "user_id": str(state.user_id),
        }
    )

    assert state.status is ImportStatus.COMPLETED, state.error
    carried.assert_awaited_once_with("uows", state, tmp_path)


def _bundle_owning(tmp_path: Path) -> Path:
    (tmp_path / "pod.json").write_text(json.dumps({"name": "p", "outcome": _BLOCK}))
    return tmp_path


@pytest.mark.parametrize("kind", [BundleSourceKind.URL, BundleSourceKind.GITHUB])
async def test_an_imported_bundle_sets_what_its_pod_owned(monkeypatch, tmp_path, kind):
    pod = _Pod()
    monkeypatch.setattr(bundle_outcome, "PodOutcomeTarget", lambda **_: pod)
    state = _import(kind)

    await bundle_outcome.carry("uows", state, _bundle_owning(tmp_path))

    assert pod.outcome is not None
    assert pod.outcome.sentence == _BLOCK["sentence"]
    assert state.warnings == []


async def test_a_template_import_leaves_its_outcome_to_the_start(monkeypatch, tmp_path):
    pod = _Pod()
    monkeypatch.setattr(bundle_outcome, "PodOutcomeTarget", lambda **_: pod)

    await bundle_outcome.carry(
        "uows", _import(BundleSourceKind.TEMPLATE), _bundle_owning(tmp_path)
    )

    assert pod.asked == []


async def test_what_was_kept_instead_joins_the_imports_warnings_once(
    monkeypatch, tmp_path
):
    mine = PodOutcome(sentence="The launch ships on time.", kind="week")
    monkeypatch.setattr(bundle_outcome, "PodOutcomeTarget", lambda **_: _Pod(mine))
    state = _import(BundleSourceKind.URL, warnings=["an app was skipped"])

    for _resumed in range(2):
        await bundle_outcome.carry("uows", state, _bundle_owning(tmp_path))

    assert len(state.warnings) == 2
    assert state.warnings[1].startswith("Kept what this pod already owns")


async def test_a_bundle_whose_pod_owned_nothing_sets_nothing(monkeypatch, tmp_path):
    pod = _Pod()
    monkeypatch.setattr(bundle_outcome, "PodOutcomeTarget", lambda **_: pod)
    (tmp_path / "pod.json").write_text(json.dumps({"name": "p"}))

    await bundle_outcome.carry("uows", _import(BundleSourceKind.URL), tmp_path)

    assert pod.asked == []
