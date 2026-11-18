"""The app a bundle's pod opens on, carried to the pod it is imported into.

``pod.json -> home_app`` is set only on a pod that has no choice and had no
apps before the import; anything else keeps what the pod has and says so.
The rule runs over a fake pod, so what is tested is the rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from lemma_pod_bundle.normalize import _normalize_pod_payload

from app.modules.pod.domain.pod_entities import PodConfig
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    BundleSourceKind,
    ImportPlan,
    ImportState,
    PlanStep,
    StepAction,
    StepKind,
    StepStatus,
)
from app.modules.pod_bundle.infrastructure.bundle_home_app import (
    carry_to_pod,
    carry_home_app,
    created_apps,
    read_bundle_home_app,
)

pytestmark = pytest.mark.unit


class _Pod:
    """A pod as the importer reaches it: its choice, and its apps."""

    def __init__(self, *, chosen: str | None = None, apps: set[str]):
        self.home: str | None = chosen
        self.apps = apps

    async def chosen(self) -> str | None:
        return self.home

    async def app_names(self) -> set[str]:
        return set(self.apps)

    async def choose(self, app_name: str) -> None:
        self.home = app_name


async def test_a_pod_with_nothing_before_opens_on_the_bundles_app() -> None:
    pod = _Pod(apps={"inbox", "meetings", "today"})

    notes = await carry_home_app("today", pod, created={"inbox", "meetings", "today"})

    assert notes == []
    assert pod.home == "today"


async def test_a_pods_own_choice_is_kept() -> None:
    pod = _Pod(chosen="inbox", apps={"inbox", "today"})

    notes = await carry_home_app("today", pod, created={"today"})

    assert pod.home == "inbox"
    assert len(notes) == 1 and "Kept the app this Lemma opens on ('inbox')" in notes[0]


async def test_a_pod_that_had_apps_keeps_opening_on_its_first() -> None:
    pod = _Pod(apps={"crm", "today"})

    notes = await carry_home_app("today", pod, created={"today"})

    assert pod.home is None
    assert len(notes) == 1 and "already had apps" in notes[0]


async def test_reimporting_into_the_pod_it_came_from_sets_it() -> None:
    """The bundle's own app, there before, is not another app."""
    pod = _Pod(apps={"today"})

    assert await carry_home_app("today", pod, created=set()) == []
    assert pod.home == "today"


async def test_an_app_the_bundle_does_not_bring_is_not_chosen() -> None:
    pod = _Pod(apps={"inbox"})

    notes = await carry_home_app("today", pod, created={"inbox"})

    assert pod.home is None
    assert len(notes) == 1 and "does not bring" in notes[0]


def test_only_apps_this_import_made_count_as_created() -> None:
    def step(index: int, name: str, action: StepAction, status: StepStatus):
        return PlanStep(
            index=index, kind=StepKind.APP, name=name, action=action, status=status
        )

    state = ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        source=BundleSource(kind=BundleSourceKind.URL),
        plan=ImportPlan(
            format_version=4,
            steps=[
                step(0, "today", StepAction.CREATE, StepStatus.DONE),
                step(1, "inbox", StepAction.UPDATE, StepStatus.DONE),
                step(2, "meetings", StepAction.CREATE, StepStatus.FAILED),
            ],
        ),
    )

    assert created_apps(state) == {"today"}


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        ({"home_app": " today "}, "today"),
        ({"home_app": ""}, None),
        ({"home_app": 3}, None),
        ({}, None),
    ],
)
def test_the_bundles_choice_is_read_from_pod_json(
    tmp_path: Path, manifest: dict[str, object], expected: str | None
) -> None:
    (tmp_path / "pod.json").write_text(
        json.dumps({"name": "x", **manifest}), encoding="utf-8"
    )
    assert read_bundle_home_app(tmp_path) == expected


async def test_a_bundle_naming_no_app_changes_nothing(tmp_path: Path) -> None:
    (tmp_path / "pod.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")
    pod = _Pod(apps={"inbox"})
    state = ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        source=BundleSource(kind=BundleSourceKind.URL),
    )

    await carry_to_pod(object(), state, tmp_path, target=pod)  # type: ignore[arg-type]

    assert pod.home is None and state.warnings == []


def test_an_export_carries_the_app_its_pod_opens_on() -> None:
    payload = _normalize_pod_payload(
        {"name": "Today", "config": {"home_app": "today", "join_policy": "ORG"}}
    )
    assert payload["home_app"] == "today"
    assert "home_app" not in _normalize_pod_payload({"name": "x", "config": {}})


def test_the_pod_config_keeps_a_cleared_choice_and_never_fails_on_one() -> None:
    # A null somebody sent is kept: an update merges key by key, and null is
    # how a person hands the choice back to the apps' own order. A config
    # nobody chose for reads as it did before there was a choice.
    assert PodConfig(home_app=None).model_dump()["home_app"] is None
    assert "home_app" not in PodConfig().model_dump()
    assert (
        "home_app"
        not in PodConfig.from_raw({"join_policy": "ORG_MEMBERS"}).model_dump()
    )
    assert PodConfig(home_app="  ").home_app is None
    # A stored value of the wrong shape costs the choice, not the whole config.
    stored = PodConfig.from_raw({"join_policy": "ORG_MEMBERS", "home_app": 7})
    assert stored.home_app is None
    assert stored.join_policy.value == "ORG_MEMBERS"
