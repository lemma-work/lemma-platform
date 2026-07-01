"""Unit tests for BundleStaging — finding the bundle root inside an extracted
archive, at whatever nesting depth it actually shows up at."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from uuid import uuid4

from app.modules.pod_import.infrastructure.staging import BundleStaging


def _zip_with(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def test_bundle_root_is_the_extraction_root_when_pod_json_is_at_the_top(tmp_path: Path):
    archive = _zip_with({"pod.json": b"{}", "tables/widgets/widgets.json": b"{}"})
    staging = BundleStaging(root=tmp_path)
    root = staging.stage(uuid4(), archive, "bundle.zip")
    assert (root / "pod.json").is_file()


def test_bundle_root_unwraps_a_single_export_wrapper_folder(tmp_path: Path):
    # What a downloaded/uploaded bundle looks like: one wrapper folder.
    archive = _zip_with({"trumpet/pod.json": b"{}", "trumpet/tables/widgets/widgets.json": b"{}"})
    staging = BundleStaging(root=tmp_path)
    root = staging.stage(uuid4(), archive, "bundle.zip")
    assert root.name == "trumpet"
    assert (root / "pod.json").is_file()


def test_bundle_root_unwraps_two_levels_of_nesting(tmp_path: Path):
    # What a GitHub codeload zipball of a published repo looks like: GitHub's
    # own "<repo>-<ref>/" wrapper around the bundle's "<pod_name>/" wrapper.
    # This is exactly the shape that used to make a re-imported GitHub pod
    # come back with an empty plan (bundle_root pointed at the outer folder,
    # which has no pod.json and no tables/agents/etc. directly inside it).
    archive = _zip_with(
        {
            "repo-main/README.md": b"# hi",
            "repo-main/trumpet/pod.json": b"{}",
            "repo-main/trumpet/tables/widgets/widgets.json": b"{}",
        }
    )
    staging = BundleStaging(root=tmp_path)
    root = staging.stage(uuid4(), archive, "repo.zip")
    assert (root / "pod.json").is_file()
    assert (root / "tables" / "widgets" / "widgets.json").is_file()


def test_bundle_root_falls_back_to_extraction_root_when_no_pod_json_exists(tmp_path: Path):
    archive = _zip_with({"readme.txt": b"hello"})
    staging = BundleStaging(root=tmp_path)
    root = staging.stage(uuid4(), archive, "bundle.zip")
    assert not (root / "pod.json").is_file()


def test_path_for_returns_none_for_an_unstaged_import(tmp_path: Path):
    staging = BundleStaging(root=tmp_path)
    assert staging.path_for(uuid4()) is None
