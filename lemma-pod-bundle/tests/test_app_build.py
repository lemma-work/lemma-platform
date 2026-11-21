from __future__ import annotations

import os
from pathlib import Path

from lemma_pod_bundle import (
    APP_DIST_SOURCE_DIGEST,
    app_source_digest,
    prebuilt_dist,
    read_dist_source_digest,
    write_dist_source_digest,
)


def _source(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


FILES = {"package.json": "{}", "index.html": "<div></div>", "src/main.tsx": "x"}


def test_the_same_files_give_the_same_digest_whatever_their_order_or_age(tmp_path):
    a = _source(tmp_path / "a", FILES)
    b = _source(tmp_path / "b", dict(reversed(list(FILES.items()))))
    os.utime(b / "index.html", (0, 0))
    assert app_source_digest(a) == app_source_digest(b)


def test_the_digest_is_pinned(tmp_path):
    """Other tools (the plugins' assembler) compute it too: a change here must be
    a deliberate change of format, not an accident."""
    assert app_source_digest(_source(tmp_path, FILES)) == (
        "sha256:21375bd9cff07b5be76e2347a199dd35e81acda1af34a500782d43eb367cc99a"
    )


def test_a_change_of_content_or_of_name_changes_it(tmp_path):
    base = app_source_digest(_source(tmp_path / "a", FILES))
    edited = app_source_digest(_source(tmp_path / "b", {**FILES, "src/main.tsx": "y"}))
    moved = dict(FILES)
    moved["src/app.tsx"] = moved.pop("src/main.tsx")
    renamed = app_source_digest(_source(tmp_path / "c", moved))
    assert len({base, edited, renamed}) == 3


def test_what_a_build_or_install_leaves_behind_is_not_source(tmp_path):
    clean = app_source_digest(_source(tmp_path / "a", FILES))
    messy = _source(
        tmp_path / "b",
        {
            **FILES,
            "node_modules/react/index.js": "1",
            "dist/index.html": "2",
            ".git/HEAD": "3",
            "src/__pycache__/x.pyc": "4",
        },
    )
    assert app_source_digest(messy) == clean


def test_a_build_is_prebuilt_only_while_it_names_this_source(tmp_path):
    resource = tmp_path / "apps" / "deals"
    source = _source(resource / "source", FILES)
    assert prebuilt_dist(resource) is None  # no build at all

    (resource / "dist.zip").write_bytes(b"zip")
    assert prebuilt_dist(resource) is None  # a build, but nobody says of what

    write_dist_source_digest(resource, app_source_digest(source))
    assert prebuilt_dist(resource) == resource / "dist.zip"
    assert read_dist_source_digest(resource) == app_source_digest(source)

    (source / "src" / "main.tsx").write_text("edited")
    assert prebuilt_dist(resource) is None  # source edited after the build


def test_a_stamp_that_is_not_a_sha256_is_no_stamp(tmp_path):
    resource = tmp_path / "apps" / "deals"
    resource.mkdir(parents=True)
    for text in ("", "sha256:abc", "sha256:" + "g" * 64, "sha1:" + "0" * 64):
        (resource / APP_DIST_SOURCE_DIGEST).write_text(text)
        assert read_dist_source_digest(resource) is None
    (resource / APP_DIST_SOURCE_DIGEST).write_text("  sha256:" + "a" * 64 + "\n")
    assert read_dist_source_digest(resource) == "sha256:" + "a" * 64
