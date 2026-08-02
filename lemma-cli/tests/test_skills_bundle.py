from __future__ import annotations

from pathlib import Path

import pytest

from lemma_cli.cli_core import skills_bundle


EXPECTED_SKILLS = {
    "browser",
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
    "liteparse-documents",
}


def _repo_skills_dir() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "lemma-skills"
        if candidate.is_dir() and any(candidate.glob("*/SKILL.md")):
            return candidate
    return None


def test_bundled_skills_dir_resolves():
    directory = skills_bundle.bundled_skills_dir()
    assert directory.is_dir()
    assert any(directory.glob("*/SKILL.md"))


def test_iter_bundled_skills_have_name_and_description():
    skills = skills_bundle.iter_bundled_skills()
    names = {skill.name for skill in skills}
    assert names == EXPECTED_SKILLS
    for skill in skills:
        assert skill.name
        assert skill.description
        assert skill.file_count >= 1
        assert "TODO" not in skill.description


def test_default_set_is_every_namespaced_lemma_skill():
    assert set(skills_bundle.CURATED_SKILLS) == {
        name for name in EXPECTED_SKILLS if name.startswith("lemma-")
    }


def test_curated_skills_are_bundled():
    available = skills_bundle.bundled_skill_map()
    for name in skills_bundle.CURATED_SKILLS:
        assert name in available


def test_parse_frontmatter_handles_quoted_colon_value():
    text = '---\nname: demo\ndescription: "Do X: then Y."\n---\nbody\n'
    front = skills_bundle.parse_frontmatter(text)
    assert front["name"] == "demo"
    assert front["description"] == "Do X: then Y."


def test_parse_frontmatter_without_block_returns_empty():
    assert skills_bundle.parse_frontmatter("# not frontmatter\n") == {}


def test_bundled_set_matches_repo_source():
    """Guard against the vendored copy drifting from the canonical source."""
    repo = _repo_skills_dir()
    if repo is None:
        pytest.skip("repo-root lemma-skills/ not available")
    repo_names = {child.name for child in repo.iterdir() if (child / "SKILL.md").is_file()}
    assert set(skills_bundle.bundled_skill_map()) == repo_names
