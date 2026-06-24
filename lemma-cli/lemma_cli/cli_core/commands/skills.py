"""Install the bundled Lemma agent skills into a coding agent.

Claude Code, Codex, OpenCode, and Cursor all read the Anthropic SKILL.md format,
so "installing" a skill is just copying its directory into the right per-tool
location. The skills ship inside the wheel (see ``skills_bundle``).

The CLI owns these skills: ``install`` is an UPSERT — it overwrites an existing
copy so a freshly-installed ``lemma-terminal`` always lines the agent up with the
skills it bundles.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import typer

from ..confirm import confirm_destructive
from ..io import emit
from ..skills_bundle import (
    CURATED_SKILLS,
    SkillInfo,
    bundled_skill_map,
    iter_bundled_skills,
)
from ..state import console, fail, state_from_ctx

app = typer.Typer(
    help="Install bundled Lemma agent skills into your coding agent (Claude Code, Codex, OpenCode, Cursor)."
)


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    binary: str | None  # PATH binary used for auto-detection (None = shared dir)
    user_dir: Path | None  # None = the tool has no global/user-level skills dir
    project_subpath: str

    def dir_for(self, scope: str) -> Path | None:
        if scope == "project":
            return Path.cwd() / self.project_subpath
        return self.user_dir  # may be None (e.g. Cursor is project-scoped only)


# OpenCode also reads ~/.claude/skills and ~/.agents/skills, so installing to
# `claude` or `agents` already reaches it; the explicit `opencode` target writes
# to OpenCode's canonical location. Cursor reads ONLY project-level
# .cursor/skills (no global dir), so it is project-scope only.
def _targets() -> dict[str, Target]:
    home = Path.home()
    return {
        "claude": Target("claude", "Claude Code", "claude", home / ".claude" / "skills", ".claude/skills"),
        "codex": Target("codex", "Codex", "codex", home / ".agents" / "skills", ".agents/skills"),
        "opencode": Target(
            "opencode", "OpenCode", "opencode", home / ".config" / "opencode" / "skills", ".opencode/skills"
        ),
        "cursor": Target("cursor", "Cursor", "cursor", None, ".cursor/skills"),
        "agents": Target("agents", "Codex + OpenCode (shared)", None, home / ".agents" / "skills", ".agents/skills"),
    }


_AUTODETECT_ORDER = ("claude", "codex", "opencode", "cursor")

_DRY_RUN_LABELS = {
    "installed": "would install",
    "updated": "would update",
    "unchanged": "unchanged",
}


def _resolve_targets(target: str | None) -> list[Target]:
    """Targets named by ``--target`` (``all`` expands to the known agents)."""
    targets = _targets()
    if target is None:
        return []
    key = target.lower().strip()
    if key == "all":
        return [targets[name] for name in _AUTODETECT_ORDER]
    if key not in targets:
        raise typer.BadParameter(
            f"Unknown target {target!r}. Choose from: {', '.join([*targets, 'all'])}."
        )
    return [targets[key]]


def _detected_targets() -> list[Target]:
    targets = _targets()
    return [
        targets[name]
        for name in _AUTODETECT_ORDER
        if shutil.which(targets[name].binary or "")
    ]


def _select_skills(names: list[str] | None, *, all_skills: bool) -> list[SkillInfo]:
    available = bundled_skill_map()
    if names:
        chosen: list[SkillInfo] = []
        for name in names:
            skill = available.get(name)
            if skill is None:
                raise typer.BadParameter(
                    f"Unknown skill {name!r}. Available: {', '.join(available)}."
                )
            chosen.append(skill)
        return chosen
    wanted = list(available) if all_skills else list(CURATED_SKILLS)
    return [available[name] for name in wanted if name in available]


@app.command("list")
def list_skills(ctx: typer.Context) -> None:
    """List the agent skills bundled with the CLI."""
    state = state_from_ctx(ctx)
    emit(
        state,
        {
            "items": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "files": skill.file_count,
                    "curated": skill.name in CURATED_SKILLS,
                }
                for skill in iter_bundled_skills()
            ]
        },
    )


@app.command("path")
def show_path(
    ctx: typer.Context,
    target: str | None = typer.Option(
        None, "--target", "-t", help="claude, codex, opencode, cursor, agents, or all."
    ),
    scope: str = typer.Option("user", "--scope", help="user or project."),
) -> None:
    """Print the destination skills directory for a target (or all targets)."""
    state = state_from_ctx(ctx)
    _validate_scope(scope)
    targets = _resolve_targets(target) or list(_targets().values())
    emit(
        state,
        {
            "items": [
                {
                    "target": tgt.key,
                    "scope": scope,
                    "path": str(tgt.dir_for(scope)) if tgt.dir_for(scope) else _NO_USER_DIR,
                }
                for tgt in targets
            ]
        },
    )


@app.command("install")
def install_skills(
    ctx: typer.Context,
    names: list[str] | None = typer.Argument(
        None, metavar="[SKILL...]", help="Skills to install. Defaults to the curated set."
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        "-t",
        help="claude, codex, opencode, cursor, agents, or all. Omit to auto-detect installed agents.",
    ),
    scope: str = typer.Option(
        "user", "--scope", help="user (global) or project (current directory)."
    ),
    dir: Path | None = typer.Option(
        None, "--dir", help="Install into an arbitrary directory instead of a known target."
    ),
    all_skills: bool = typer.Option(
        False, "--all-skills", help="Include browser and liteparse-documents (workspace-runtime skills)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be written, write nothing."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt when replacing symlinked skill targets."
    ),
) -> None:
    """Install (upsert) bundled skills into your coding agent.

    The CLI owns these skills, so an existing copy is overwritten to match what
    this lemma-terminal bundles. With no SKILL names, installs the curated set
    (lemma-builder, lemma-user, lemma-widget). With no --target/--dir,
    auto-detects which of Claude Code, Codex, OpenCode, and Cursor are on your
    PATH and installs to each.
    """
    state = state_from_ctx(ctx)
    _validate_scope(scope)
    skills = _select_skills(names, all_skills=all_skills)
    destinations = _resolve_destinations(target=target, scope=scope, dir=dir)

    rows: list[dict[str, object]] = []
    written_labels: set[str] = set()

    # Symlinked skill targets crash shutil.rmtree; confirm before replacing them.
    if not dry_run:
        symlinked = [
            dest_dir / skill.name
            for _label, dest_dir in destinations
            if dest_dir is not None
            for skill in skills
            if (dest_dir / skill.name).is_symlink()
        ]
        if symlinked:
            confirm_destructive(
                f"{len(symlinked)} skill target(s) are symlinks and will be replaced with "
                "real directories (only the links are removed, not their targets).",
                yes,
            )

    for dest_label, dest_dir in destinations:
        if dest_dir is None:
            rows.append(_unsupported_row(dest_label, scope))
            continue
        for skill in skills:
            target_dir = dest_dir / skill.name
            action = _upsert_action(skill.path, target_dir)
            if not dry_run and action != "unchanged":
                _copy_skill(skill.path, target_dir)
                written_labels.add(dest_label)
            rows.append(
                {
                    "skill": skill.name,
                    "target": dest_label,
                    "path": str(target_dir),
                    "action": _DRY_RUN_LABELS[action] if dry_run else action,
                }
            )

    emit(state, {"items": rows})
    if not dry_run and written_labels and state.output != "json":
        _print_followup(written_labels)


@app.command("uninstall")
def uninstall_skills(
    ctx: typer.Context,
    names: list[str] | None = typer.Argument(
        None, metavar="[SKILL...]", help="Skills to remove. Defaults to all bundled skills."
    ),
    target: str | None = typer.Option(
        None, "--target", "-t", help="claude, codex, opencode, cursor, agents, or all."
    ),
    scope: str = typer.Option("user", "--scope", help="user or project."),
    dir: Path | None = typer.Option(None, "--dir", help="Remove from an arbitrary directory."),
) -> None:
    """Remove previously installed bundled skills from a coding agent."""
    state = state_from_ctx(ctx)
    _validate_scope(scope)
    available = bundled_skill_map()
    wanted = names or list(available)
    for name in wanted:
        if name not in available:
            raise typer.BadParameter(f"Unknown skill {name!r}. Available: {', '.join(available)}.")
    destinations = _resolve_destinations(target=target, scope=scope, dir=dir)

    rows: list[dict[str, object]] = []
    for dest_label, dest_dir in destinations:
        if dest_dir is None:
            rows.append(_unsupported_row(dest_label, scope))
            continue
        for name in wanted:
            target_dir = dest_dir / name
            removed = (target_dir / "SKILL.md").is_file()
            if removed:
                _remove_existing(target_dir)
            rows.append(
                {
                    "skill": name,
                    "target": dest_label,
                    "path": str(target_dir),
                    "action": "removed" if removed else "not present",
                }
            )
    emit(state, {"items": rows})


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

_NO_USER_DIR = "(no global skills dir — use --scope project)"


def _validate_scope(scope: str) -> None:
    if scope not in {"user", "project"}:
        raise typer.BadParameter("scope must be 'user' or 'project'.")


def _resolve_destinations(
    *, target: str | None, scope: str, dir: Path | None
) -> list[tuple[str, Path | None]]:
    """Return (label, directory) pairs to install into. A None directory means
    the target does not support this scope (e.g. Cursor at user scope)."""
    if dir is not None:
        return [("dir", dir.expanduser())]
    if target is not None:
        return [(tgt.label, tgt.dir_for(scope)) for tgt in _resolve_targets(target)]
    detected = _detected_targets()
    if not detected:
        fail(
            "No coding agents (claude, codex, opencode, cursor) found on PATH. "
            "Pass --target (claude|codex|opencode|cursor|agents|all) or --dir to choose a destination."
        )
    return [(tgt.label, tgt.dir_for(scope)) for tgt in detected]


def _upsert_action(source: Path, target_dir: Path) -> str:
    """installed (new), unchanged (identical), or updated (differs → overwrite)."""
    if target_dir.is_symlink():
        return "updated"
    if not target_dir.exists():
        return "installed"
    return "unchanged" if _dirs_identical(source, target_dir) else "updated"


def _dirs_identical(a: Path, b: Path) -> bool:
    a_files = sorted(p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file())
    b_files = sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file())
    if a_files != b_files:
        return False
    return all((a / rel).read_bytes() == (b / rel).read_bytes() for rel in a_files)


def _copy_skill(source: Path, target_dir: Path) -> None:
    """Upsert: replace any existing skill dir (or symlink) with the bundled copy."""
    _remove_existing(target_dir)
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target_dir)


def _remove_existing(path: Path) -> None:
    """Remove a path whether it is a symlink, directory, or stray file.

    ``unlink`` on a symlink removes only the pointer — the link's target is
    left untouched. ``is_symlink`` is checked first because ``exists``/
    ``is_dir`` follow the link.
    """
    if path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    if path.exists():
        path.unlink()


def _unsupported_row(label: str, scope: str) -> dict[str, object]:
    return {
        "skill": "—",
        "target": label,
        "path": _NO_USER_DIR,
        "action": f"unsupported at --scope {scope}",
    }


def _print_followup(labels: set[str]) -> None:
    joined = ", ".join(sorted(labels))
    console.print(
        f"[dim]Installed into: {joined}. Restart your coding agent if the skills "
        "don't appear immediately.[/dim]"
    )
