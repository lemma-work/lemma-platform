"""A Vite app deployed without `VITE_LEMMA_*` in its project.

A prebuilt `--dist-dir` has already been built, so no build env is asked of it.
A build takes what the project leaves empty from the Lemma it is deployed to --
the case of every app folder imported from a bundle, which carries no
`.env.local`.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lemma_cli.cli_app import app_bundle

_NAMES = ("VITE_LEMMA_API_URL", "VITE_LEMMA_AUTH_URL", "VITE_LEMMA_POD_ID")
_TARGET = {
    "VITE_LEMMA_API_URL": "https://api.target.test",
    "VITE_LEMMA_AUTH_URL": "https://target.test/auth",
}


class _Client:
    def __init__(self) -> None:
        self.uploaded = 0

    def pod(self, pod_id: str) -> SimpleNamespace:
        del pod_id

        def upload_bundle(name, *, source_archive, dist_archive):  # type: ignore[no-untyped-def]
            del name, source_archive, dist_archive
            self.uploaded += 1
            return {"ok": True}

        return SimpleNamespace(
            apps=SimpleNamespace(
                get=lambda name: {"name": name}, upload_bundle=upload_bundle
            )
        )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in _NAMES:
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "desk"
    root.mkdir()
    (root / "package.json").write_text('{"name":"desk"}', encoding="utf-8")
    return root


def test_a_prebuilt_dist_deploys_with_no_build_env(project: Path):
    dist = project / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<built/>", encoding="utf-8")
    client = _Client()

    app_bundle.deploy_app_bundle(
        client, pod_id="pod-1", app_name="desk", source_dir=project, dist_dir=dist
    )

    assert client.uploaded == 1


def test_a_build_takes_the_target_lemmas_values(project: Path):
    # The env the build runs with; checked without running npm.
    env = app_bundle._with_target_values(
        app_bundle.resolve_app_project_env(project), pod_id="pod-1", target_env=_TARGET
    )

    assert {name: env[name] for name in _NAMES} == {
        **_TARGET,
        "VITE_LEMMA_POD_ID": "pod-1",
    }


def test_values_the_project_sets_win_over_the_target(project: Path):
    (project / ".env.local").write_text(
        "VITE_LEMMA_API_URL=https://api.mine.test\nVITE_LEMMA_POD_ID=\n",
        encoding="utf-8",
    )
    env = app_bundle._with_target_values(
        app_bundle.resolve_app_project_env(project), pod_id="pod-1", target_env=_TARGET
    )

    assert env["VITE_LEMMA_API_URL"] == "https://api.mine.test"
    # Set but empty is not set.
    assert env["VITE_LEMMA_POD_ID"] == "pod-1"


def test_a_build_with_no_sign_in_url_anywhere_still_says_what_is_missing(
    project: Path,
):
    with pytest.raises(ValueError, match="VITE_LEMMA_API_URL, VITE_LEMMA_AUTH_URL"):
        app_bundle.deploy_app_bundle(
            _Client(), pod_id="pod-1", app_name="desk", source_dir=project
        )
