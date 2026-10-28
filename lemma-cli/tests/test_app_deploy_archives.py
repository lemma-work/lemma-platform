"""What `lemma apps deploy` uploads as an app's bundle and as its source.

The backend stores both per release: the bundle is what the host serves, the
source is what `lemma apps pull` hands back to edit. For a no-build app the two
are the same files, and an upload where they differ -- or where the source is
missing -- breaks pull → edit → deploy on the next round.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from lemma_cli.cli_app import app_bundle
from lemma_cli.cli_core.commands.apps import deploy_asks_first


def _read_zip(path: str) -> dict[str, bytes]:
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


class _Uploads:
    """A pod client that keeps the archives' contents before they are deleted."""

    def __init__(self) -> None:
        self.source: dict[str, bytes] | None = None
        self.bundle: dict[str, bytes] | None = None

    def pod(self, pod_id: str) -> SimpleNamespace:
        del pod_id

        def upload_bundle(name, *, source_archive, dist_archive):  # type: ignore[no-untyped-def]
            del name
            self.source = _read_zip(source_archive) if source_archive else None
            self.bundle = _read_zip(dist_archive) if dist_archive else None
            return {"ok": True}

        return SimpleNamespace(
            apps=SimpleNamespace(
                get=lambda name: {"name": name},
                upload_bundle=upload_bundle,
            )
        )


def _deploy(source: Path, dist_dir: Path | None = None) -> _Uploads:
    uploads = _Uploads()
    app_bundle.deploy_app_bundle(
        uploads, pod_id="pod-1", app_name="desk", source_dir=source, dist_dir=dist_dir
    )
    return uploads


def test_deploy_asks_only_someone_who_can_answer():
    """An agent, CI or a pipe has nobody to answer [y/N]; it deploys instead."""
    assert deploy_asks_first(yes=False, interactive=True)
    assert not deploy_asks_first(yes=True, interactive=True)
    assert not deploy_asks_first(yes=False, interactive=False)


def test_a_single_html_app_uploads_one_file_as_both_source_and_bundle(tmp_path):
    page = tmp_path / "desk.html"
    page.write_text("<h1>desk</h1>", encoding="utf-8")

    uploads = _deploy(page)

    assert uploads.bundle == {"index.html": b"<h1>desk</h1>"}
    assert uploads.source == uploads.bundle


def test_a_static_app_uploads_the_same_files_as_source_and_bundle(tmp_path):
    site = tmp_path / "site"
    (site / "build").mkdir(parents=True)
    (site / "node_modules" / "left-pad").mkdir(parents=True)
    (site / "index.html").write_text(
        '<script src="build/app.js"></script>', encoding="utf-8"
    )
    # Assets the page loads, in a directory named like build output. A static
    # app is its own output, so they ship.
    (site / "build" / "app.js").write_text("run()", encoding="utf-8")
    (site / "node_modules" / "left-pad" / "index.js").write_text(
        "pad()", encoding="utf-8"
    )
    (site / ".DS_Store").write_bytes(b"\0")

    uploads = _deploy(site)

    assert uploads.bundle == {
        "index.html": b'<script src="build/app.js"></script>',
        "build/app.js": b"run()",
    }
    assert uploads.source == uploads.bundle


def test_a_built_app_uploads_its_output_as_bundle_and_its_tree_as_source(
    tmp_path, monkeypatch
):
    project = tmp_path / "vite-app"
    (project / "src" / "build").mkdir(parents=True)
    (project / "node_modules" / "vite").mkdir(parents=True)
    (project / "dist" / "assets").mkdir(parents=True)
    (project / "package.json").write_text('{"name":"desk"}', encoding="utf-8")
    (project / "src" / "main.ts").write_text("main()", encoding="utf-8")
    # Source somebody wrote, in a folder named like build output but not at
    # the project root.
    (project / "src" / "build" / "steps.ts").write_text("steps()", encoding="utf-8")
    (project / "node_modules" / "vite" / "index.js").write_text("", encoding="utf-8")
    # The build output, at the root where the project keeps it (`--dist-dir`).
    (project / "dist" / "index.html").write_text("<built/>", encoding="utf-8")
    (project / "dist" / "assets" / "main.js").write_text("built()", encoding="utf-8")
    for name in ("VITE_LEMMA_API_URL", "VITE_LEMMA_AUTH_URL", "VITE_LEMMA_POD_ID"):
        monkeypatch.setenv(name, "x")

    uploads = _deploy(project, dist_dir=project / "dist")

    assert uploads.bundle == {
        "index.html": b"<built/>",
        "assets/main.js": b"built()",
    }
    assert uploads.source == {
        "package.json": b'{"name":"desk"}',
        "src/main.ts": b"main()",
        "src/build/steps.ts": b"steps()",
    }
