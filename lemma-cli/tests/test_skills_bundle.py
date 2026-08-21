from __future__ import annotations

import re

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
    repo_names = {
        child.name for child in repo.iterdir() if (child / "SKILL.md").is_file()
    }
    assert set(skills_bundle.bundled_skill_map()) == repo_names


# --------------------------------------------------------------------------- #
# skill docs vs. the enums they document
# --------------------------------------------------------------------------- #

# Never persisted on ``Agent.toolsets`` — appended at run time for any agent whose
# resolved model declares VISION capability. Documenting it as something you grant
# would be wrong, so it is exempt from the coverage assertion below rather than
# silently absent from the table.
_UNGRANTABLE_TOOLSETS = {"VIEW_IMAGE"}

_TOOLSET_ROW = re.compile(r"^\|\s*`([A-Z_]+)`\s*\|")


def _documented_toolsets(agents_md: str) -> set[str]:
    """Toolset names in the first column of the ``## Toolsets`` table."""
    section = agents_md.split("\n## Toolsets\n", 1)
    assert len(section) == 2, "agents.md has no '## Toolsets' section"
    body = section[1].split("\n## ", 1)[0]
    return {m.group(1) for line in body.splitlines() if (m := _TOOLSET_ROW.match(line))}


def test_builder_skill_documents_every_grantable_toolset():
    """The toolset table is model-facing tool documentation, so hold it to the
    same freshness bar as ``agent_tool_schemas.json``: adding a toolset without
    documenting it should fail CI, not wait for a reviewer to notice.

    ``lemma_cli.cli_app.enums.TOOLSETS`` is *derived* from the generated SDK enum
    and so cannot drift; this hand-written markdown table is the only copy that
    can, which is why it is the one under test.
    """
    from lemma_cli.cli_app import enums

    repo = _repo_skills_dir()
    if repo is None:
        pytest.skip("repo-root lemma-skills/ not available")
    agents_md = repo / "lemma-builder" / "references" / "agents.md"
    if not agents_md.is_file():
        pytest.skip("lemma-builder/references/agents.md not available")

    grantable = set(enums.TOOLSETS) - _UNGRANTABLE_TOOLSETS
    documented = _documented_toolsets(agents_md.read_text(encoding="utf-8"))

    assert not (grantable - documented), (
        f"undocumented toolsets in agents.md: {sorted(grantable - documented)}"
    )
    assert not (documented - grantable - _UNGRANTABLE_TOOLSETS), (
        f"agents.md documents unknown toolsets: "
        f"{sorted(documented - grantable - _UNGRANTABLE_TOOLSETS)}"
    )
