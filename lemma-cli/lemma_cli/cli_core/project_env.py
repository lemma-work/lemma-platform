"""Per-folder Lemma context via project ``.lemma.env`` files.

A user-system feature: on a laptop (humans and local coding agents like Claude
Code / Codex) the CLI auto-discovers a project ``.lemma.env`` family walking up
from the cwd and loads its ``LEMMA_*`` vars into the environment *before* state is
resolved, so a folder can bind to a specific pod/server without mutating the
global ``~/.lemma/config.json`` or exporting env vars by hand.

Because a project's *server and pod change together* (local stack vs. cloud), the
files are keyed by the active server, mirroring Vite's ``.env.<mode>`` layout:

    .lemma.env                      base (shared; may set the default LEMMA_SERVER)
    .lemma.<server>.env             per-server binding (LEMMA_POD_ID, LEMMA_ORG_ID)
    .lemma.env.local                personal base override        (gitignore)
    .lemma.<server>.env.local       personal per-server override  (gitignore)

Precedence (low -> high among files); real process env and CLI flags win over all:

    .lemma.env < .lemma.env.local < .lemma.<server>.env < .lemma.<server>.env.local

Loading is intentionally minimal — the CLI already resolves pod/org/server from
``LEMMA_*`` env vars, so this only populates them (never overwriting a value real
env already set). A real ``LEMMA_TOKEN`` in the environment (an env-driven context
such as a workspace sandbox) skips the files entirely.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lemma_sdk.config import DEFAULT_SERVER_NAME, load_config, normalize_server_name

from .dotenv import read_env_file

LEMMA_ENV_NAME = ".lemma.env"
LEMMA_PREFIX = "LEMMA_"


def _server_env_name(server: str) -> str:
    return f".lemma.{server}.env"


def _has_project_env(directory: Path) -> bool:
    """True if a directory holds a committed project-env anchor — the base
    ``.lemma.env`` or any ``.lemma.<server>.env`` (a lone ``.local`` override or a
    bare ``.env`` never anchors discovery)."""
    if (directory / LEMMA_ENV_NAME).is_file():
        return True
    return any(directory.glob(".lemma.*.env"))


def find_project_dir(start: Path | None = None) -> Path | None:
    """Nearest ancestor of ``start`` (default cwd) that holds a project-env anchor.

    Walks up to a ceiling — the git repo root if inside one, else ``$HOME`` — both
    inclusive, so a stray file above the project can't bind every folder.
    """
    try:
        current = (start or Path.cwd()).resolve()
    except OSError:
        return None
    home = Path.home().resolve()
    while True:
        if _has_project_env(current):
            return current
        # Ceilings (current already checked above): stop at the git repo root and
        # at $HOME so discovery never climbs into unrelated parents.
        if (current / ".git").exists():
            return None
        if current == home or current.parent == current:
            return None
        current = current.parent


def _config_active_server(config_file: Path | None) -> str | None:
    """The stored ``active_server`` (best effort), so the server the files are keyed
    by matches what ``build_state`` will select. Never raises."""
    if config_file is None:
        return None
    try:
        raw = load_config(config_file)
    except Exception:
        return None
    value = raw.get("active_server")
    return str(value) if isinstance(value, str) and value else None


def _apply(values: dict[str, str], info: dict[str, Any]) -> None:
    """Apply ``LEMMA_*`` keys via setdefault (real process env always wins)."""
    for key, value in values.items():
        if not key.startswith(LEMMA_PREFIX):
            continue
        if key in os.environ:
            continue
        os.environ[key] = value
        info["applied"].append(key)


def load_project_env(
    *,
    server_flag: str | None = None,
    config_file: Path | None = None,
    start: Path | None = None,
) -> dict[str, Any]:
    """Discover the project ``.lemma.env`` family and apply its ``LEMMA_*`` keys.

    Two passes because the server keys which file to read: (1) read the base
    ``.lemma.env[.local]`` to learn the folder's default ``LEMMA_SERVER``; (2) resolve
    the active server the way ``build_state`` does — ``--server`` flag > real
    ``LEMMA_SERVER`` > base file > config ``active_server`` > ``default`` — then merge
    base + ``.lemma.<server>.env[.local]`` low->high and apply with setdefault.

    Returns ``{project_dir, server, files, applied, token_in_committed_file,
    skipped_reason}`` for ``config show`` (``project_dir`` is None when no anchor was
    found — a no-op).
    """
    info: dict[str, Any] = {
        "project_dir": None,
        "server": None,
        "files": [],
        "applied": [],
        "token_in_committed_file": False,
        "skipped_reason": None,
    }
    project_dir = find_project_dir(start)
    if project_dir is None:
        return info
    info["project_dir"] = str(project_dir)

    committed_base = read_env_file(project_dir / LEMMA_ENV_NAME)
    info["token_in_committed_file"] = "LEMMA_TOKEN" in committed_base

    # A real LEMMA_TOKEN already in the environment means an env-driven context
    # (e.g. a workspace sandbox, which injects token + pod and has no config.json). The project
    # files must never interfere there — skip them entirely so a committed file
    # can't redirect the server or shadow the injected identity.
    if os.environ.get("LEMMA_TOKEN"):
        info["skipped_reason"] = "real LEMMA_TOKEN present (env-driven context)"
        return info

    # Base layer (shared): .lemma.env then .lemma.env.local (later wins).
    base: dict[str, str] = {}
    base.update(committed_base)
    base.update(read_env_file(project_dir / f"{LEMMA_ENV_NAME}.local"))

    # Resolve the active server (mirrors build_state's precedence).
    server = normalize_server_name(
        server_flag
        or os.getenv("LEMMA_SERVER")
        or base.get("LEMMA_SERVER")
        or _config_active_server(config_file)
        or DEFAULT_SERVER_NAME
    )
    info["server"] = server

    # Merge base + server layer low->high, then apply (real env still wins).
    server_env = read_env_file(project_dir / _server_env_name(server))
    server_local = read_env_file(project_dir / f"{_server_env_name(server)}.local")
    merged: dict[str, str] = {**base, **server_env, **server_local}

    for name in (
        LEMMA_ENV_NAME,
        f"{LEMMA_ENV_NAME}.local",
        _server_env_name(server),
        f"{_server_env_name(server)}.local",
    ):
        if (project_dir / name).is_file():
            info["files"].append(name)

    _apply(merged, info)
    return info


def write_server_env(
    project_dir: Path,
    server: str,
    values: dict[str, str],
    *,
    seed_default_server: bool = True,
) -> Path:
    """Bind ``project_dir`` to ``server`` by writing ``.lemma.<server>.env`` with
    ``values`` (e.g. ``{"LEMMA_POD_ID": ...}``), merging over any existing keys, and
    (optionally) seed ``.lemma.env`` with ``LEMMA_SERVER=<server>`` if it has none —
    so ``cd``-ing into the folder defaults to that server. Returns the written file.

    Only ``LEMMA_*`` keys are written; nothing here is a secret, so the files are
    safe to commit.
    """
    server = normalize_server_name(server)
    target = project_dir / _server_env_name(server)
    existing = read_env_file(target)
    merged = {**existing}
    for key, value in values.items():
        if key.startswith(LEMMA_PREFIX) and value:
            merged[key] = value
    _write_env_file(target, merged, header=f"Lemma binding for server '{server}'. Safe to commit; no secrets.")

    if seed_default_server:
        base_path = project_dir / LEMMA_ENV_NAME
        base = read_env_file(base_path)
        if "LEMMA_SERVER" not in base:
            base["LEMMA_SERVER"] = server
            _write_env_file(
                base_path,
                base,
                header="Lemma project defaults. Safe to commit; no secrets.",
            )
    return target


def _write_env_file(path: Path, values: dict[str, str], *, header: str) -> None:
    lines = [f"# {header}"]
    for key in sorted(values):
        lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
