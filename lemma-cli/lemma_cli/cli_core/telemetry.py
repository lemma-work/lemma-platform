"""Anonymous CLI usage telemetry: which commands are run, and whether they work.

Scoped tightly on purpose. The backend already records everything a command
*did* — a pod created from the CLI lands on the same domain event as one
created from the web app, and is attributed by origin. What the server cannot
see is the shape of CLI use itself: which command groups people reach for,
which ones fail, and on what version. That is all this sends.

What it never sends: arguments, flag values, paths, pod or resource names, ids,
or anything typed at the prompt. The command name is matched against a fixed
list of known groups and dropped if it is not one of them, so an unrecognised
first token cannot become a dimension.

Off by every switch that should turn it off: ``LEMMA_TELEMETRY=0``,
``lemma telemetry off``, and — the default — no ingestion key compiled
in, which is the case for every self-hosted and locally built CLI.
"""

from __future__ import annotations

import os
import platform
import threading
import uuid
from pathlib import Path
from typing import Any

TELEMETRY_KEY_ENV = "LEMMA_TELEMETRY_KEY"
TELEMETRY_HOST_ENV = "LEMMA_TELEMETRY_HOST"
TELEMETRY_DISABLE_ENV = "LEMMA_TELEMETRY"
DEFAULT_HOST = "https://eu.i.posthog.com"

#: Two seconds, once, and never on the foreground thread. A CLI that pauses
#: because an analytics endpoint is slow is a bug report, and a deserved one.
_TIMEOUT_SECONDS = 2.0

_CONFIG_KEY = "telemetry"
_INSTALL_ID_KEY = "install_id"
_ENABLED_KEY = "enabled"


def _config_path() -> Path:
    override = os.getenv("LEMMA_CONFIG_FILE")
    if override:
        return Path(override)
    return Path.home() / ".lemma" / "config.json"


def _read_config() -> dict[str, Any]:
    try:
        import json

        return json.loads(_config_path().read_text())
    except Exception:
        return {}


def _write_telemetry_block(block: dict[str, Any]) -> None:
    """Merge ``block`` into the ``telemetry`` key of ``~/.lemma/config.json``.

    Through the SDK's ``config_lock`` + ``save_config``, like every other writer
    of this file. The previous unlocked ``write_text`` of a whole re-read config
    raced the token-refresh path in ``state.py``: a refresh landing between this
    read and this write was silently lost, and a crash mid-write truncated the
    file, after which every command failed with "Invalid JSON". Losing that file
    means losing the login session — far more than telemetry is worth.
    """
    from lemma_sdk.config import config_lock, load_config, save_config

    path = _config_path()
    try:
        with config_lock(path):
            # load_config, not the raw read: it raises on a corrupt file, so a
            # file we cannot parse is left alone rather than replaced by a stub
            # holding nothing but this block.
            config = load_config(path)
            config[_CONFIG_KEY] = {**(config.get(_CONFIG_KEY) or {}), **block}
            save_config(path, config)
    except Exception:
        # Telemetry must never be the reason a CLI invocation fails, including
        # on a read-only home directory.
        return


def install_id() -> str:
    """A random per-installation id, minted once.

    Random — never derived from hostname, MAC or machine id, which are
    fingerprints rather than identifiers.
    """
    block = _read_config().get(_CONFIG_KEY) or {}
    existing = block.get(_INSTALL_ID_KEY)
    if isinstance(existing, str) and existing:
        return existing
    minted = str(uuid.uuid4())
    _write_telemetry_block({_INSTALL_ID_KEY: minted})
    return minted


def is_enabled() -> bool:
    if (os.getenv(TELEMETRY_DISABLE_ENV) or "").strip() in {"0", "false", "off", "no"}:
        return False
    if not (os.getenv(TELEMETRY_KEY_ENV) or "").strip():
        # No key: a locally built or self-hosted CLI reports nothing, and there
        # is no flag that turns that into reporting.
        return False
    block = _read_config().get(_CONFIG_KEY) or {}
    return block.get(_ENABLED_KEY, True) is not False


def set_enabled(enabled: bool) -> None:
    _write_telemetry_block({_ENABLED_KEY: enabled})


def status() -> dict[str, Any]:
    block = _read_config().get(_CONFIG_KEY) or {}
    return {
        "enabled": is_enabled(),
        "opted_out": block.get(_ENABLED_KEY) is False,
        "key_configured": bool((os.getenv(TELEMETRY_KEY_ENV) or "").strip()),
        "install_id": block.get(_INSTALL_ID_KEY),
        "config_path": str(_config_path()),
    }


def _cli_version() -> str:
    # versions.cli_version(), not importlib.metadata: the import package is
    # `lemma_cli` but the published *distribution* is `lemma-terminal`, so
    # version("lemma-cli") raised PackageNotFoundError on every install and the
    # one dimension this telemetry exists to record was always "unknown".
    from .versions import cli_version

    return cli_version()


def _post(event: dict[str, Any]) -> None:
    try:
        import httpx

        host = (os.getenv(TELEMETRY_HOST_ENV) or DEFAULT_HOST).rstrip("/")
        httpx.post(
            f"{host}/batch/",
            json={
                "api_key": os.getenv(TELEMETRY_KEY_ENV, ""),
                "batch": [event],
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except Exception:
        return


def record_command(command: str | None, *, exit_status: str) -> None:
    """Fire and forget. Returns immediately; delivery happens on a daemon
    thread that the interpreter will not wait for at exit."""
    if not is_enabled():
        return
    if command is None:
        return
    payload = {
        "event": "cli.command_invoked",
        "distinct_id": install_id(),
        "properties": {
            "command": command,
            "exit_status": exit_status,
            "cli_version": _cli_version(),
            "os": platform.system().lower(),
            "arch": platform.machine().lower(),
        },
    }
    thread = threading.Thread(target=_post, args=(payload,), daemon=True)
    thread.start()
