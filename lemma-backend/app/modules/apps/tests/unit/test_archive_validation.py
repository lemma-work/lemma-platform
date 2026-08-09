from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from app.modules.apps.domain.errors import AppValidationError
from app.modules.apps.config import apps_settings
from app.modules.apps.services.archive_validation import (
    _normalized_path,
    inspect_app_archive,
)


def _archive(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path, value in entries.items():
            archive.writestr(path, value)
    return output.getvalue()


def test_archive_inspection_accepts_bounded_regular_zip() -> None:
    result = inspect_app_archive(
        _archive({"index.html": b"ok", "assets/app.js": b"js"}),
        label="Dist archive",
    )
    assert result.entry_count == 2
    assert result.uncompressed_bytes == 4


@pytest.mark.parametrize("path", ["../secret", "/absolute", "C:/windows", "a\\b"])
def test_archive_inspection_rejects_traversal_and_platform_paths(path: str) -> None:
    with pytest.raises(AppValidationError):
        inspect_app_archive(_archive({path: b"x"}), label="Source archive")


def test_archive_inspection_rejects_symlink() -> None:
    output = BytesIO()
    info = ZipInfo("link")
    info.create_system = 3
    info.external_attr = 0o120777 << 16
    with ZipFile(output, "w") as archive:
        archive.writestr(info, "target")
    with pytest.raises(AppValidationError, match="symbolic link"):
        inspect_app_archive(output.getvalue(), label="Source archive")


def test_archive_inspection_rejects_invalid_zip_and_ambiguous_paths() -> None:
    with pytest.raises(AppValidationError, match="valid zip"):
        inspect_app_archive(b"not-a-zip", label="Dist archive")

    for path in ("", ".", "folder/../secret"):
        with pytest.raises(AppValidationError, match="invalid path"):
            _normalized_path(path, label="Source archive")


def test_archive_inspection_rejects_duplicate_paths() -> None:
    output = BytesIO()
    with pytest.warns(UserWarning, match="Duplicate name"):
        with ZipFile(output, "w") as archive:
            archive.writestr("duplicate.txt", b"one")
            archive.writestr("duplicate.txt", b"two")

    with pytest.raises(AppValidationError, match="duplicate paths"):
        inspect_app_archive(output.getvalue(), label="Source archive")


def test_archive_inspection_enforces_entry_size_and_ratio_limits(monkeypatch) -> None:
    monkeypatch.setattr(apps_settings, "app_archive_max_entries", 1)
    with pytest.raises(AppValidationError, match="too many entries"):
        inspect_app_archive(_archive({"a": b"a", "b": b"b"}), label="Archive")

    monkeypatch.setattr(apps_settings, "app_archive_max_entries", 100)
    monkeypatch.setattr(apps_settings, "app_archive_max_uncompressed_bytes", 1)
    with pytest.raises(AppValidationError, match="configured limit"):
        inspect_app_archive(_archive({"large": b"ab"}), label="Archive")

    monkeypatch.setattr(apps_settings, "app_archive_max_uncompressed_bytes", 10_000)
    monkeypatch.setattr(apps_settings, "app_archive_max_compression_ratio", 1)
    with pytest.raises(AppValidationError, match="compression ratio"):
        inspect_app_archive(_archive({"compressed": b"x" * 1_000}), label="Archive")


def test_zip_bomb_is_refused_at_production_limits() -> None:
    """A tiny upload that expands enormously must not reach the reader.

    The other limit tests monkeypatch the caps down to prove the mechanism
    fires; this one uses the shipped defaults, because what matters is that a
    realistic bomb is refused by the configuration operators actually run.
    `load_app_dist_bundle` is the sink under test: it reads every entry fully
    into memory, so the refusal has to happen before it.
    """
    from app.modules.apps.services.app_dist_bundle import load_app_dist_bundle

    # ~50 MB of zeros compresses to a few dozen KB: a ratio far above the
    # allowed 200:1, and the shape nginx's compressed-body cap cannot see.
    bomb = _archive({"index.html": b"<html></html>", "payload.bin": b"\0" * 50_000_000})
    assert len(bomb) < 1024 * 1024

    with pytest.raises(AppValidationError, match="compression ratio"):
        load_app_dist_bundle(bomb)


def test_archive_inspection_accepts_directory_entries() -> None:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("assets/", b"")
        archive.writestr("assets/app.js", b"ok")

    result = inspect_app_archive(output.getvalue(), label="Archive")
    assert result.entry_count == 2
    assert result.uncompressed_bytes == 2
