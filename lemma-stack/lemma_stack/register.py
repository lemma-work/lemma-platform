"""Register the installed stack as the "local" server in lemma-cli's config.

Writes ~/.lemma/config.json directly (lemma-cli may not be installed yet),
mirroring the JSON shape lemma-cli's `servers` commands produce: servers are
keyed by name with base_url/auth_url, and active_server selects one.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from lemma_stack.output import info, ok, warn

SERVER_NAME = "local"
CLI_DISTRIBUTION = "lemma-terminal"


def cli_config_path() -> Path:
    override = os.environ.get("LEMMA_CONFIG_FILE")
    return Path(override).expanduser() if override else Path.home() / ".lemma" / "config.json"


def register_local_server(
    *,
    base_url: str,
    auth_url: str,
    make_active: bool = False,
) -> Path:
    path = cli_config_path()
    config: dict = {}
    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {}
    if not isinstance(config, dict):
        config = {}

    servers = config.setdefault("servers", {})
    existing = servers.get(SERVER_NAME)
    server = existing if isinstance(existing, dict) else {}
    server["base_url"] = base_url.rstrip("/")
    server["auth_url"] = auth_url.rstrip("/")
    server.setdefault("defaults", {})
    servers[SERVER_NAME] = server

    if make_active or not config.get("active_server"):
        config["active_server"] = SERVER_NAME

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    info(f"registered lemma-cli server '{SERVER_NAME}' -> {base_url}")
    return path


def _detect_cli_source() -> str | None:
    """A pip/uv-installable spec for the lemma CLI.

    Order: explicit LEMMA_CLI_SOURCE, then a sibling checkout (when lemma-stack
    runs from the monorepo), else None (let the caller fall back to PyPI).
    """
    explicit = os.environ.get("LEMMA_CLI_SOURCE")
    if explicit:
        return explicit
    here = Path(__file__).resolve()
    for parent in here.parents:
        cli = parent / "lemma-cli"
        if (cli / "pyproject.toml").exists() and (parent / "lemma-python").exists():
            return str(cli)
    return None


def _find_user_binary(name: str) -> str | None:
    # Finder-launched macOS apps do not inherit the user's shell PATH. The
    # signed desktop bundle includes uv next to the supervisor sidecar. Prefer
    # uv's user tool directory over a stale system-wide CLI on PATH.
    for directory in (
        Path(sys.executable).resolve().parent,
        Path.home() / ".local" / "bin",
        Path.home() / ".cargo" / "bin",
    ):
        candidate = directory / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        check=False,
    )


def _installed_cli_version(lemma: str) -> str | None:
    proc = _run([lemma, "--version"])
    if proc.returncode != 0:
        return None
    first_line = (proc.stdout or proc.stderr or "").splitlines()[0:1]
    if not first_line:
        return None
    parts = first_line[0].strip().split()
    return parts[1] if len(parts) >= 2 and parts[0] == "lemma" else None


def install_lemma_cli(*, version: str | None = None) -> bool:
    """Best-effort install of the `lemma` CLI as a uv tool. Never fatal — the
    server is registered regardless, so the CLI works once present."""
    uv = _find_user_binary("uv")
    if uv is None:
        warn("uv not found; install the Lemma terminal yourself: uv tool install lemma-terminal")
        return False
    source = _detect_cli_source()
    if source is None:
        source = f"{CLI_DISTRIBUTION}=={version}" if version else CLI_DISTRIBUTION
        lemma = _find_user_binary("lemma")
        if version and lemma and _installed_cli_version(lemma) == version:
            return True
    elif _find_user_binary("lemma") is not None:
        # Dev environment with a local checkout already on PATH — leave it alone.
        return True
    info(f"installing the lemma CLI from {source}")
    proc = _run([uv, "tool", "install", "--force", source])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        warn(
            "could not auto-install the lemma CLI; install it with "
            f"`uv tool install {source}`.\n  {detail}"
        )
        return False
    ok("lemma CLI installed")
    return True


def install_lemma_skills() -> bool:
    """Upsert curated skills from lemma-terminal into user-level agent dirs."""
    lemma = _find_user_binary("lemma")
    if lemma is None:
        warn("lemma CLI not found; skipping bundled skill installation")
        return False
    info("installing bundled Lemma skills")
    proc = _run([lemma, "skills", "install", "--target", "all", "--yes"])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        warn(f"could not install bundled Lemma skills.\n  {detail}")
        return False
    ok("Lemma skills installed")
    return True


def install_lemma_cli_and_skills(*, version: str | None = None) -> bool:
    if not install_lemma_cli(version=version):
        return False
    return install_lemma_skills()
