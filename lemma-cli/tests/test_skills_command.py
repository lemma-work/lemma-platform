from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.skills_bundle import CURATED_SKILLS

runner = CliRunner()


def _invoke(args: list[str], tmp_path: Path):
    # Point at a throwaway config so tests never touch the real ~/.lemma/config.
    cfg = tmp_path / "config.json"
    return runner.invoke(app, ["--config-file", str(cfg), *args])


def _installed_dirs(dest: Path) -> set[str]:
    return {child.name for child in dest.iterdir() if child.is_dir()}


def test_skills_list_includes_all_bundled(tmp_path):
    result = _invoke(["--json", "skills", "list"], tmp_path)
    assert result.exit_code == 0, result.output
    names = {item["name"] for item in json.loads(result.output)["items"]}
    assert {
        "lemma-builder",
        "lemma-user",
        "lemma-widget",
        "browser",
        "liteparse-documents",
    } <= names


def test_install_to_dir_copies_skill_tree(tmp_path):
    dest = tmp_path / "dest"
    result = _invoke(["skills", "install", "--dir", str(dest), "lemma-builder"], tmp_path)
    assert result.exit_code == 0, result.output
    assert (dest / "lemma-builder" / "SKILL.md").is_file()
    assert (dest / "lemma-builder" / "references").is_dir()


def test_default_install_is_curated_trio(tmp_path):
    dest = tmp_path / "dest"
    result = _invoke(["skills", "install", "--dir", str(dest)], tmp_path)
    assert result.exit_code == 0, result.output
    assert _installed_dirs(dest) == set(CURATED_SKILLS)


def test_all_skills_flag_includes_workspace_skills(tmp_path):
    dest = tmp_path / "dest"
    result = _invoke(["skills", "install", "--dir", str(dest), "--all-skills"], tmp_path)
    assert result.exit_code == 0, result.output
    assert {"browser", "liteparse-documents"} <= _installed_dirs(dest)


def test_reinstall_of_identical_is_unchanged(tmp_path):
    dest = tmp_path / "dest"
    _invoke(["skills", "install", "--dir", str(dest), "lemma-user"], tmp_path)
    result = _invoke(["--json", "skills", "install", "--dir", str(dest), "lemma-user"], tmp_path)
    assert json.loads(result.output)["items"][0]["action"] == "unchanged"


def test_install_upserts_modified_skill(tmp_path):
    # The CLI owns the skills: a drifted/old copy is overwritten to match the bundle.
    dest = tmp_path / "dest"
    _invoke(["skills", "install", "--dir", str(dest), "lemma-user"], tmp_path)
    stray = dest / "lemma-user" / "STALE"
    stray.write_text("old")
    (dest / "lemma-user" / "SKILL.md").write_text("hand-edited")
    result = _invoke(["--json", "skills", "install", "--dir", str(dest), "lemma-user"], tmp_path)
    assert json.loads(result.output)["items"][0]["action"] == "updated"
    assert not stray.exists()  # clean overwrite removed the drift
    assert "hand-edited" not in (dest / "lemma-user" / "SKILL.md").read_text()


def test_dry_run_writes_nothing(tmp_path):
    dest = tmp_path / "dest"
    result = _invoke(["skills", "install", "--dir", str(dest), "lemma-widget", "--dry-run"], tmp_path)
    assert result.exit_code == 0, result.output
    assert not dest.exists()


def test_unknown_skill_exits_2(tmp_path):
    dest = tmp_path / "dest"
    result = _invoke(["skills", "install", "--dir", str(dest), "does-not-exist"], tmp_path)
    assert result.exit_code == 2


def test_unknown_target_exits_2(tmp_path):
    result = _invoke(["skills", "install", "--target", "vim", "lemma-user"], tmp_path)
    assert result.exit_code == 2


def test_path_reports_codex_dir(tmp_path):
    result = _invoke(["--json", "skills", "path", "--target", "codex"], tmp_path)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["items"][0]["path"].endswith("/.agents/skills")


def test_project_scope_uses_relative_dir(tmp_path):
    result = _invoke(["--json", "skills", "path", "--target", "claude", "--scope", "project"], tmp_path)
    path = json.loads(result.output)["items"][0]["path"]
    assert path.endswith("/.claude/skills")


def test_cursor_target_is_project_scoped(tmp_path):
    result = _invoke(
        ["--json", "skills", "path", "--target", "cursor", "--scope", "project"], tmp_path
    )
    assert json.loads(result.output)["items"][0]["path"].endswith("/.cursor/skills")


def test_cursor_has_no_global_dir(tmp_path):
    # Cursor is project-only; at user scope install reports it as unsupported.
    result = _invoke(
        ["--json", "skills", "install", "--target", "cursor", "lemma-user"], tmp_path
    )
    assert result.exit_code == 0, result.output
    row = json.loads(result.output)["items"][0]
    assert "unsupported" in row["action"]


def test_uninstall_removes_installed_skill(tmp_path):
    dest = tmp_path / "dest"
    _invoke(["skills", "install", "--dir", str(dest), "lemma-user"], tmp_path)
    assert (dest / "lemma-user").is_dir()
    result = _invoke(["--json", "skills", "uninstall", "--dir", str(dest), "lemma-user"], tmp_path)
    assert json.loads(result.output)["items"][0]["action"] == "removed"
    assert not (dest / "lemma-user").exists()


# --------------------------------------------------------------------------- #
# Symlink target handling                                                      #
# --------------------------------------------------------------------------- #
# os.symlink needs privilege on Windows; skip there. The install/uninstall
# logic must handle symlinks without crashing shutil.rmtree.


_skip_windows = pytest.mark.skipif(
    sys.platform == "win32", reason="os.symlink requires privilege on Windows"
)


@_skip_windows
def test_install_overwrites_symlinked_target(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    real_skill = tmp_path / "real-lemma-user"
    real_skill.mkdir()
    (real_skill / "SKILL.md").write_text("stale")
    link = dest / "lemma-user"
    os.symlink(real_skill, link)

    result = _invoke(
        ["--json", "skills", "install", "--dir", str(dest), "lemma-user", "--yes"], tmp_path
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["items"][0]["action"] == "updated"
    # The symlink is gone, replaced by a real directory matching the bundle.
    assert not link.is_symlink()
    assert (link / "SKILL.md").is_file()
    assert "stale" not in (link / "SKILL.md").read_text()
    # The symlink's original target is untouched.
    assert (real_skill / "SKILL.md").read_text() == "stale"


@_skip_windows
def test_install_symlink_noninteractive_without_yes_fails(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    real_skill = tmp_path / "real-lemma-user"
    real_skill.mkdir()
    (real_skill / "SKILL.md").write_text("stale")
    os.symlink(real_skill, dest / "lemma-user")

    # CliRunner is non-interactive (stdin not a TTY), so confirm_destructive
    # must fail with a non-zero exit instead of hanging or proceeding.
    result = _invoke(
        ["--json", "skills", "install", "--dir", str(dest), "lemma-user"], tmp_path
    )
    assert result.exit_code != 0
    assert "--yes" in result.stdout or "non-interactive" in result.stdout
    # The symlink is untouched — nothing was written.
    assert (dest / "lemma-user").is_symlink()


@_skip_windows
def test_install_symlink_dry_run_no_prompt(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    real_skill = tmp_path / "real-lemma-user"
    real_skill.mkdir()
    (real_skill / "SKILL.md").write_text("stale")
    os.symlink(real_skill, dest / "lemma-user")

    result = _invoke(
        ["--json", "skills", "install", "--dir", str(dest), "lemma-user", "--dry-run"],
        tmp_path,
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["items"][0]["action"] == "would update"
    # Dry run must not touch the symlink.
    assert (dest / "lemma-user").is_symlink()


@_skip_windows
def test_install_symlink_reported_as_updated_even_if_identical(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    # Install the real bundle first, then symlink to it.
    _invoke(["skills", "install", "--dir", str(dest), "lemma-user"], tmp_path)
    real_dir = dest / "lemma-user"
    _invoke(["skills", "uninstall", "--dir", str(dest), "lemma-user"], tmp_path)
    os.symlink(real_dir, dest / "lemma-user")
    # Can't easily make identical content without copying, so just verify a
    # symlink always reports "updated" regardless of content.
    result = _invoke(
        ["--json", "skills", "install", "--dir", str(dest), "lemma-user", "--dry-run"],
        tmp_path,
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["items"][0]["action"] == "would update"


@_skip_windows
def test_install_overwrites_broken_symlink(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    # A dangling symlink (target does not exist).
    os.symlink(tmp_path / "nonexistent", dest / "lemma-user")

    result = _invoke(
        ["--json", "skills", "install", "--dir", str(dest), "lemma-user", "--yes"], tmp_path
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["items"][0]["action"] == "updated"
    assert not (dest / "lemma-user").is_symlink()
    assert (dest / "lemma-user" / "SKILL.md").is_file()


@_skip_windows
def test_uninstall_removes_symlinked_skill(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    real_skill = tmp_path / "real-lemma-user"
    real_skill.mkdir()
    (real_skill / "SKILL.md").write_text("stale")
    os.symlink(real_skill, dest / "lemma-user")

    result = _invoke(
        ["--json", "skills", "uninstall", "--dir", str(dest), "lemma-user"], tmp_path
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["items"][0]["action"] == "removed"
    # Only the link is removed; the target is untouched.
    assert not (dest / "lemma-user").exists()
    assert (real_skill / "SKILL.md").read_text() == "stale"
