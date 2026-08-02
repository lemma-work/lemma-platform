"""Build shim that vendors the agent skills into the package at build time.

All project metadata lives in pyproject.toml; this file only adds a build step.
The canonical skills source is the repo-root ``lemma-skills/`` (the backend and
Dockerfiles read it directly), so we copy it into ``lemma_cli/skills/`` whenever
we build — for both the sdist (run from the source tree, where the source is
available) and the wheel (often built from the unpacked sdist, where the source
is absent but the files are already vendored in).

This makes a bare ``uv build`` / ``python -m build`` ship the skills with no
separate sync step, and fails the build loudly rather than ever publishing an
empty ``lemma_cli/skills/`` (which is exactly how lemma-terminal 0.4.1 shipped
without skills).
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


class _BuildPy(_build_py):
    def run(self) -> None:
        _vendor_skills()
        super().run()


class _Sdist(_sdist):
    def run(self) -> None:
        _vendor_skills()
        super().run()


setup(cmdclass={"build_py": _BuildPy, "sdist": _Sdist})
