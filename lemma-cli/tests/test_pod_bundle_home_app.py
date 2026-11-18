"""The app a bundle's pod opens on, carried by `lemma pods import`.

Set only on a pod with no choice that had no other apps before the import; a
pod that already opens somewhere keeps it, with an advisory.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from lemma_cli.cli_app.pod_bundle_home_app import (
    bundle_home_app,
    carry_home_app,
    plan_home_app,
)


class _Client:
    def __init__(self, *, chosen: str | None, apps: list[str]):
        self.config: dict[str, object] = {"home_app": chosen}
        self.apps = apps
        self.updates: list[dict[str, object]] = []
        self.pods = SimpleNamespace(get=self._get, update=self._update)

    def _get(self, pod_id: str) -> dict[str, object]:
        return {"id": pod_id, "config": dict(self.config)}

    def _update(self, pod_id: str, request) -> dict[str, object]:
        body = request.to_dict()
        self.updates.append(body)
        self.config.update(body.get("config") or {})
        return {"id": pod_id}

    def pod(self, pod_id: str):
        return SimpleNamespace(
            apps=SimpleNamespace(
                list=lambda limit=1000: {"items": [{"name": n} for n in self.apps]}
            )
        )


def test_a_pod_with_nothing_before_opens_on_the_bundles_app():
    client = _Client(chosen=None, apps=["inbox", "today"])

    assert plan_home_app(client, "p", "today", apps_before=set()) == ["set:today"]
    summary, notes = carry_home_app(client, "p", "today", apps_before=set())

    assert summary == ["set:today"] and notes == []
    assert client.updates == [{"config": {"home_app": "today"}}]


def test_a_pods_own_choice_and_its_apps_are_kept():
    chose = _Client(chosen="inbox", apps=["inbox", "today"])
    assert carry_home_app(chose, "p", "today", apps_before={"inbox"})[0] == [
        "kept:inbox"
    ]
    had_apps = _Client(chosen=None, apps=["crm", "today"])
    summary, notes = carry_home_app(had_apps, "p", "today", apps_before={"crm"})
    assert summary == ["kept:first-app"] and "already had apps" in notes[0]
    assert chose.updates == had_apps.updates == []


def test_the_bundles_choice_is_read_from_pod_json(tmp_path: Path):
    (tmp_path / "pod.json").write_text(json.dumps({"home_app": " today "}))
    assert bundle_home_app(tmp_path) == "today"
    (tmp_path / "pod.json").write_text(json.dumps({"name": "x"}))
    assert bundle_home_app(tmp_path) is None
