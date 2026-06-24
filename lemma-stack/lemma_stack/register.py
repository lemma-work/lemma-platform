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
from pathlib import Path

from lemma_stack.output import info, ok, warn

SERVER_NAME = "local"


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


def install_lemma_cli() -> bool:
    """Best-effort install of the `lemma` CLI as a uv tool. Never fatal — the
    server is registered regardless, so the CLI works once present."""
    if shutil.which("uv") is None:
        warn("uv not found; install the lemma CLI yourself: uv tool install lemma-cli")
        return False
    source = _detect_cli_source()
    if source is None:
        # End-user install: always force the latest PyPI release, replacing any
        # previous installation (including one from a git source).
        source = "lemma-cli"
    elif shutil.which("lemma") is not None:
        # Dev environment with a local checkout already on PATH — leave it alone.
        return True
    info(f"installing the lemma CLI from {source}")
    proc = subprocess.run(
        ["uv", "tool", "install", "--force", source],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        warn(
            "could not auto-install the lemma CLI; install it with "
            f"`uv tool install lemma-cli`.\n  {detail}"
        )
        return False
    ok("lemma CLI installed")
    return True
