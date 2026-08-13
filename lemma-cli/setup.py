"""Build shim that vendors this package's monorepo-local sources at build time.

All project metadata lives in pyproject.toml; this file only adds a build step.
Two things live outside this directory and must travel inside the wheel:

* the agent skills, whose canonical source is the repo-root ``lemma-skills/``
  (the backend and Dockerfiles read it directly), and
* ``lemma_pod_bundle``, the shared bundle-format package. It is not published to
  PyPI — locally and in CI it resolves from the sibling checkout through
  ``[tool.uv.sources]`` — so an installed ``lemma-terminal`` has no way to get it
  except by carrying a copy.

Both are copied for the sdist (run from the source tree, where the sources
exist) and for the wheel (often built from an unpacked sdist, where the sources
are absent but the copies are already in place).

Doing this here rather than in the release workflow is deliberate. The workflow
used to rewrite ``pyproject.toml`` with regexes at publish time, which meant a
local ``uv build`` produced a different — broken — wheel from the published one,
and nothing outside a tagged run ever exercised the packaging. Now a bare
``uv build`` ships exactly what CI ships, and a missing source fails the build
loudly rather than publishing a package that imports nothing (which is exactly
how lemma-terminal 0.4.1 shipped without skills).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist

_HERE = Path(__file__).resolve().parent
_SKILLS_SOURCE = _HERE.parent / "lemma-skills"
_SKILLS_DEST = _HERE / "lemma_cli" / "skills"
_POD_BUNDLE_SOURCE = _HERE.parent / "lemma-pod-bundle" / "lemma_pod_bundle"
_POD_BUNDLE_DEST = _HERE / "lemma_pod_bundle"


def _skill_dirs(root: Path) -> list[Path]:
    return sorted(
        child
        for child in root.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    )


def _vendor_skills() -> None:
    if _SKILLS_SOURCE.is_dir():
        skills = _skill_dirs(_SKILLS_SOURCE)
        if not skills:
            raise SystemExit(f"lemma-skills source has no skills: {_SKILLS_SOURCE}")
        if _SKILLS_DEST.exists():
            shutil.rmtree(_SKILLS_DEST)
        _SKILLS_DEST.mkdir(parents=True, exist_ok=True)
        for skill in skills:
            shutil.copytree(skill, _SKILLS_DEST / skill.name)
        return
    # No source (e.g. building the wheel from an unpacked sdist): the skills must
    # already be vendored, or we'd publish an empty package. Fail loudly.
    if not (_SKILLS_DEST.is_dir() and any(_SKILLS_DEST.glob("*/SKILL.md"))):
        raise SystemExit(
            "Cannot build lemma-terminal: lemma-skills source not found at "
            f"{_SKILLS_SOURCE} and no vendored skills at {_SKILLS_DEST}. "
            "Build from the monorepo so ../lemma-skills exists, or provide an "
            "sdist that already contains the vendored skill directory."
        )


def _vendor_pod_bundle() -> None:
    """Copy ``lemma_pod_bundle`` in, or confirm a previous build already did.

    The package is stdlib-only, so vendoring it adds no dependency of its own.
    """
    if _POD_BUNDLE_SOURCE.is_dir():
        if not (_POD_BUNDLE_SOURCE / "__init__.py").is_file():
            raise SystemExit(
                f"lemma-pod-bundle source is not a package: {_POD_BUNDLE_SOURCE}"
            )
        if _POD_BUNDLE_DEST.exists():
            shutil.rmtree(_POD_BUNDLE_DEST)
        shutil.copytree(
            _POD_BUNDLE_SOURCE,
            _POD_BUNDLE_DEST,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        return
    if not (_POD_BUNDLE_DEST / "__init__.py").is_file():
        raise SystemExit(
            "Cannot build lemma-terminal: lemma-pod-bundle source not found at "
            f"{_POD_BUNDLE_SOURCE} and nothing vendored at {_POD_BUNDLE_DEST}. "
            "Build from the monorepo so ../lemma-pod-bundle exists, or provide "
            "an sdist that already contains the vendored package."
        )


def _vendor_all() -> None:
    _vendor_skills()
    _vendor_pod_bundle()


class _BuildPy(_build_py):
    def run(self) -> None:
        _vendor_all()
        super().run()


class _Sdist(_sdist):
    def run(self) -> None:
        _vendor_all()
        super().run()


setup(cmdclass={"build_py": _BuildPy, "sdist": _Sdist})
