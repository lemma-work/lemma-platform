#!/usr/bin/env python3
"""The lemma-terminal distributions must carry what PyPI cannot supply.

``lemma-terminal`` imports two things that live outside ``lemma-cli/`` and are
published nowhere: the agent skills (canonical source ``lemma-skills/``) and
``lemma_pod_bundle`` (canonical source ``lemma-pod-bundle/``). ``setup.py``
copies both into the package at build time, so the only thing standing between a
user and ``ModuleNotFoundError: lemma_pod_bundle`` is that the copies actually
reach the archive.

That guarantee has now failed twice in different ways — once by shipping a wheel
with no skills at all (0.4.1), once by vendoring from a build command that runs
*after* ``packages.find`` has already fixed the package list, which produced a
complete wheel on any tree where a previous build had left the copy behind and a
broken one from a fresh clone. Both were invisible to every test that reads
source files, because both were properties of the built artifact.

So this reads the artifact. Run it against a ``dist/`` directory holding a freshly
built wheel (and, if present, the sdist the release also publishes)::

    python scripts/check_cli_wheel.py lemma-cli/dist

It exits non-zero listing everything missing. Build the distributions from a
clean checkout — a dirty tree is exactly what hides this class of bug.
"""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_POD_BUNDLE_SOURCE = _REPO_ROOT / "lemma-pod-bundle" / "lemma_pod_bundle"
_SKILLS_SOURCE = _REPO_ROOT / "lemma-skills"

#: Distributions that exist only inside this monorepo. Any of these in
#: Requires-Dist makes every `pip install lemma-terminal` fail on a package the
#: index has never heard of.
UNPUBLISHED_DISTRIBUTIONS = ("lemma-pod-bundle",)


def _expected_pod_bundle_modules() -> set[str]:
    return {
        f"lemma_pod_bundle/{path.relative_to(_POD_BUNDLE_SOURCE).as_posix()}"
        for path in _POD_BUNDLE_SOURCE.rglob("*.py")
        if "__pycache__" not in path.parts
    }


def _expected_skills() -> set[str]:
    return {
        child.name
        for child in _SKILLS_SOURCE.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    }


def _check_vendored(names: set[str], archive: str, strip: str = "") -> list[str]:
    """Both vendored trees must be present in full, not merely non-empty."""
    problems: list[str] = []
    present = {name[len(strip) :] if strip and name.startswith(strip) else name for name in names}

    missing_modules = sorted(_expected_pod_bundle_modules() - present)
    if missing_modules:
        problems.append(
            f"{archive}: lemma_pod_bundle is not fully vendored — missing "
            f"{missing_modules}. setup.py must vendor before setup() is called, "
            "or packages.find never sees the directory."
        )

    shipped_skills = {
        name.split("/")[2]
        for name in present
        if name.startswith("lemma_cli/skills/") and name.endswith("/SKILL.md")
    }
    missing_skills = sorted(_expected_skills() - shipped_skills)
    if missing_skills:
        problems.append(f"{archive}: skills missing from the package: {missing_skills}")

    return problems


def _check_wheel(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        problems = _check_vendored(names, wheel.name)

        metadata_name = next(
            (name for name in names if name.endswith(".dist-info/METADATA")), None
        )
        if metadata_name is None:
            return [*problems, f"{wheel.name}: no METADATA in the wheel"]
        metadata = archive.read(metadata_name).decode("utf-8")

    leaked = [
        line
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist:")
        and any(name in line.replace("_", "-").lower() for name in UNPUBLISHED_DISTRIBUTIONS)
    ]
    if leaked:
        problems.append(
            f"{wheel.name}: {leaked} names a distribution that is not on PyPI, so "
            "every install would fail resolving it. Vendor it instead."
        )
    return problems


def _check_sdist(sdist: Path) -> list[str]:
    """The published wheel is built *from* the sdist, so the sdist must carry it too."""
    with tarfile.open(sdist) as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}
    root = f"{sdist.name.removesuffix('.tar.gz')}/"
    return _check_vendored(names, sdist.name, strip=root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dist",
        nargs="?",
        default=str(_REPO_ROOT / "lemma-cli" / "dist"),
        help="directory holding the built wheel (and sdist). Default: lemma-cli/dist",
    )
    arguments = parser.parse_args()

    dist = Path(arguments.dist)
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        print(f"No wheel found in {dist} — build it first (`python -m build`).")
        return 1

    problems: list[str] = []
    for wheel in wheels:
        problems.extend(_check_wheel(wheel))
    for sdist in sorted(dist.glob("*.tar.gz")):
        problems.extend(_check_sdist(sdist))

    if problems:
        for problem in problems:
            print(f"✗ {problem}")
        return 1

    checked = ", ".join(path.name for path in sorted(dist.iterdir()) if path.is_file())
    print(f"✓ skills and lemma_pod_bundle vendored, no unpublished dependency: {checked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
