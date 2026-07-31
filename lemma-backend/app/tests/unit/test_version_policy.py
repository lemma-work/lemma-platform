from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]


def _compatibility_line(version: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        raise ValueError(f"Invalid semantic version: {version}")
    return int(match.group(1)), int(match.group(2))


def _api_version() -> str:
    source = (REPO_ROOT / "lemma-backend/app/version.py").read_text()
    match = re.search(r'^API_VERSION = "([^"]+)"$', source, re.MULTILINE)
    assert match is not None
    return match.group(1)


def _python_sdk_version() -> str:
    with (REPO_ROOT / "lemma-python/pyproject.toml").open("rb") as pyproject:
        return str(tomllib.load(pyproject)["project"]["version"])


def _typescript_sdk_version() -> str:
    package = json.loads((REPO_ROOT / "lemma-typescript/package.json").read_text())
    return str(package["version"])


@pytest.mark.parametrize(
    ("versions", "expected"),
    [
        (("0.6.3", "0.6.4", "0.6.9"), True),
        (("0.6.3", "0.7.0", "0.6.3"), False),
        (("1.2.0", "2.2.0", "1.2.1"), False),
    ],
)
def test_compatibility_line_allows_patch_drift_only(
    versions: tuple[str, str, str],
    expected: bool,
) -> None:
    lines = {_compatibility_line(version) for version in versions}
    assert (len(lines) == 1) is expected


def test_api_and_sdk_packages_share_compatibility_line() -> None:
    versions = (_api_version(), _python_sdk_version(), _typescript_sdk_version())
    assert len({_compatibility_line(version) for version in versions}) == 1, versions
