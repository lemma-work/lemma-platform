"""A repository archive narrowed to the folder that holds one of its bundles."""

from __future__ import annotations

import io
import json
import stat
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, ZipInfo

import pytest

from app.modules.pod_bundle.domain.errors import GithubImportError
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    BundleSourceKind,
    ImportState,
    ImportStatus,
)
from app.modules.pod_bundle.events.github_import import fetch_bundle_archive
from app.modules.pod_bundle.infrastructure.github_folder import bundle_in_folder
from lemma_pod_bundle import extract_bundle

_CAP = 10 * 1024 * 1024
_WRAPPER = "acme-plugins-1a2b3c4"


def _archive(files: dict[str, bytes], *, wrapper: str | None = _WRAPPER) -> bytes:
    """A repository as GitHub zips it: every file under one wrapping folder."""
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        if wrapper:
            archive.writestr(f"{wrapper}/", b"")
        for name, content in files.items():
            archive.writestr(f"{wrapper}/{name}" if wrapper else name, content)
    return buffer.getvalue()


def _manifest(name: str) -> bytes:
    return json.dumps({"name": name, "format_version": 3}).encode()


_REPOSITORY = {
    "README.md": b"# Plugins",
    "install/mail/pod.json": _manifest("Mail"),
    "install/mail/tables/threads/threads.json": b'{"name": "threads"}',
    "install/crm/pod.json": _manifest("CRM"),
    "docs/guide.md": b"How to write one",
}


def _names(archive: bytes) -> set[str]:
    with ZipFile(io.BytesIO(archive)) as opened:
        return set(opened.namelist())


def test_the_folder_becomes_the_root_of_the_archive():
    narrowed = bundle_in_folder(
        _archive(_REPOSITORY),
        "install/mail",
        repository="acme/plugins",
        max_uncompressed_bytes=_CAP,
    )

    assert _names(narrowed) == {"pod.json", "tables/threads/threads.json"}
    with ZipFile(io.BytesIO(narrowed)) as opened:
        assert json.loads(opened.read("pod.json"))["name"] == "Mail"


def test_the_narrowed_archive_extracts_to_that_bundle_alone(tmp_path: Path):
    """Without a folder, extraction takes the shallowest `pod.json` -- here a
    sibling's. Narrowed, it can only find the one asked for."""
    narrowed = bundle_in_folder(
        _archive(_REPOSITORY),
        "install/mail",
        repository="acme/plugins",
        max_uncompressed_bytes=_CAP,
    )

    root = extract_bundle(narrowed, tmp_path)

    assert json.loads((root / "pod.json").read_text())["name"] == "Mail"
    assert not (tmp_path / "README.md").exists()
    assert not list(tmp_path.rglob("guide.md"))


def test_an_archive_without_a_wrapping_folder_is_read_from_its_root():
    narrowed = bundle_in_folder(
        _archive(_REPOSITORY, wrapper=None),
        "install/crm",
        repository="acme/plugins",
        max_uncompressed_bytes=_CAP,
    )

    assert _names(narrowed) == {"pod.json"}


@pytest.mark.parametrize("path", ["install/calendar", "install/mai", "mail"])
def test_a_folder_that_is_not_there_is_a_404(path):
    with pytest.raises(GithubImportError) as raised:
        bundle_in_folder(
            _archive(_REPOSITORY),
            path,
            repository="acme/plugins",
            max_uncompressed_bytes=_CAP,
        )

    assert raised.value.code == "GITHUB_PATH_NOT_FOUND"
    assert raised.value.status_code == 404
    assert path in str(raised.value) and "acme/plugins" in str(raised.value)


@pytest.mark.parametrize("path", ["docs", "install"])
def test_a_folder_without_a_manifest_is_not_a_bundle(path):
    """`install` holds bundles but is not one: the manifest must be in the
    folder itself, so a folder names exactly one bundle."""
    with pytest.raises(GithubImportError) as raised:
        bundle_in_folder(
            _archive(_REPOSITORY),
            path,
            repository="acme/plugins",
            max_uncompressed_bytes=_CAP,
        )

    assert raised.value.code == "GITHUB_PATH_NOT_A_BUNDLE"
    assert raised.value.status_code == 422


def test_a_symlink_in_the_folder_is_still_refused_on_extraction(tmp_path: Path):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(f"{_WRAPPER}/install/mail/pod.json", _manifest("Mail"))
        link = ZipInfo(f"{_WRAPPER}/install/mail/escape")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../../etc/passwd")

    narrowed = bundle_in_folder(
        buffer.getvalue(),
        "install/mail",
        repository="acme/plugins",
        max_uncompressed_bytes=_CAP,
    )

    with pytest.raises(ValueError, match="Symlink"):
        extract_bundle(narrowed, tmp_path)


def test_a_folder_past_the_size_cap_is_refused():
    with pytest.raises(GithubImportError) as raised:
        bundle_in_folder(
            _archive({"install/mail/pod.json": _manifest("Mail") + b" " * 4096}),
            "install/mail",
            repository="acme/plugins",
            max_uncompressed_bytes=1024,
        )

    assert raised.value.code == "GITHUB_ARCHIVE_TOO_LARGE"
    assert raised.value.status_code == 413


def test_an_unreadable_archive_is_invalid():
    with pytest.raises(GithubImportError) as raised:
        bundle_in_folder(
            b"PK\x03\x04 not really a zip",
            "install/mail",
            repository="acme/plugins",
            max_uncompressed_bytes=_CAP,
        )

    assert raised.value.code == "GITHUB_ARCHIVE_INVALID"


# --- the import job's fetch ----------------------------------------------------


class _Fetcher:
    def __init__(self, archive: bytes):
        self.archive = archive
        self.asked: list[dict[str, str | None]] = []

    async def fetch_zipball(self, *, owner: str, repo: str, ref: str | None = None):
        self.asked.append({"owner": owner, "repo": repo, "ref": ref})
        return self.archive


def _github_import(path: str | None) -> ImportState:
    return ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        status=ImportStatus.FETCHING,
        source=BundleSource(
            kind=BundleSourceKind.GITHUB,
            repo_url="https://github.com/acme/plugins",
            path=path,
            ref="main",
        ),
    )


async def test_an_import_naming_a_folder_stages_only_that_folder():
    fetcher = _Fetcher(_archive(_REPOSITORY))

    staged = await fetch_bundle_archive(
        fetcher, _github_import("install/crm"), owner="acme", repo="plugins"
    )

    assert _names(staged) == {"pod.json"}
    assert fetcher.asked == [{"owner": "acme", "repo": "plugins", "ref": "main"}]


async def test_an_import_naming_no_folder_stages_the_repository_as_fetched():
    archive = _archive(_REPOSITORY)

    staged = await fetch_bundle_archive(
        _Fetcher(archive), _github_import(None), owner="acme", repo="plugins"
    )

    assert staged == archive
