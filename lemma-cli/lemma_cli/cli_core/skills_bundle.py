"""Locate and describe the agent skills bundled with the CLI.

The published wheel ships a copy of the repo-root ``lemma-skills/`` under
``lemma_cli/skills/`` (vendored by the ``setup.py`` build hooks). At runtime we
prefer that packaged copy; in a dev checkout where a build has not populated it
we fall back to the canonical repo source so ``lemma skills`` still works.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import lemma_cli

# Lemma's namespaced product skills are portable across supported coding agents
# and form the default install. Environment helpers without the ``lemma-``
# namespace assume a provisioned workspace runtime, so they remain opt-in through
# an explicit name or ``--all-skills``.
CURATED_SKILLS: tuple[str, ...] = (
    "lemma-app-design",
    "lemma-app-qa",
    "lemma-artifact-author",
    "lemma-builder",
    "lemma-data-analysis",
    "lemma-evals",
    "lemma-research",
    "lemma-skill-creator",
    "lemma-user",
    "lemma-widget",
)


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    path: Path
    file_count: int


def _packaged_skills_dir() -> Path | None:
    """The copy vendored into the installed package, if present and populated."""
    candidate = Path(lemma_cli.__file__).resolve().parent / "skills"
    if _has_skills(candidate):
        return candidate
    return None


def _repo_skills_dir() -> Path | None:
    """Dev fallback: the canonical lemma-skills/ next to a repo checkout."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "lemma-skills"
        if _has_skills(candidate):
            return candidate
    return None


def _has_skills(directory: Path) -> bool:
    return directory.is_dir() and any(directory.glob("*/SKILL.md"))


def bundled_skills_dir() -> Path:
    """Resolve the directory holding the bundled skills.

    Prefers the packaged copy; falls back to the repo source for dev checkouts.
    """
    resolved = _packaged_skills_dir() or _repo_skills_dir()
    if resolved is None:
        raise RuntimeError(
            "No bundled skills found. Expected a packaged lemma_cli/skills/ "
            "directory or a repo-root lemma-skills/ checkout."
        )
    return resolved


def parse_frontmatter(text: str) -> dict[str, str]:
    """Extract top-level ``key: value`` pairs from a SKILL.md YAML frontmatter
    block. Only simple single-line scalars are needed (name, description), so we
    avoid a yaml dependency. Quoted values have their surrounding quotes removed.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line or line[0] in " \t" or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = _unquote(value.strip())
    return fields


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _skill_info(skill_dir: Path) -> SkillInfo:
    front = parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    file_count = sum(1 for path in skill_dir.rglob("*") if path.is_file())
    return SkillInfo(
        name=front.get("name") or skill_dir.name,
        description=front.get("description", ""),
        path=skill_dir,
        file_count=file_count,
    )


def iter_bundled_skills() -> list[SkillInfo]:
    """All bundled skills, sorted by name."""
    root = bundled_skills_dir()
    skills = [
        _skill_info(child)
        for child in root.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    ]
    return sorted(skills, key=lambda skill: skill.name)


def bundled_skill_map() -> dict[str, SkillInfo]:
    return {skill.name: skill for skill in iter_bundled_skills()}
