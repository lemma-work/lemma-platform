"""Is this CLI out of date, and can it upgrade itself?

**What it checks against.** The server, not PyPI. Releases are mono-version
(``RELEASING.md``): one tag publishes ``lemma-terminal``, ``lemma-sdk`` and the
API together, so the ``info.version`` in the server's ``/openapi.json`` — the
same number ``lemma doctor`` already reads for skew — *is* the released version.
Checking it adds no host the CLI was not already talking to, and no second
opinion that can disagree with `doctor`.

**When it runs.** Never in front of a command. The check happens after the
command has finished, on a daemon thread with a short timeout (telemetry's
precedent, ``telemetry.py``), and all it does is record what it saw. The notice
is printed on a *later* invocation from that stored result, so no command ever
waits on the network to tell the user about an upgrade.

**How loud it is.** One dim line on stderr — stderr because ``--output json``
must stay pipeable — printed once per newly-released version, not once per day
and not once per command. It is suppressed where `lemma update` could not act
anyway (see ``install_kind``), because a hint that names a command that cannot
work is worse than silence.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple

#: Top-level key in ``~/.lemma/config.json``, beside ``telemetry``. No leading
#: underscore: ``save_config`` strips those as in-memory-only state.
CONFIG_KEY = "update_check"
_LAST_CHECKED = "last_checked"
_LATEST_VERSION = "latest_version"
_NOTIFIED_VERSION = "notified_version"

#: Check at most once a day. The version can only change when a release ships.
CHECK_INTERVAL_SECONDS = 24 * 60 * 60

#: Same budget as telemetry: a CLI that pauses for a background HTTP call is a
#: bug report, and this one runs after the command has already printed.
_TIMEOUT_SECONDS = 2.0

DISABLE_ENV = "LEMMA_UPDATE_CHECK"
DISTRIBUTION = "lemma-terminal"


def _config_path() -> Path:
    override = os.getenv("LEMMA_CONFIG_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".lemma" / "config.json"


def _read_block() -> dict[str, Any]:
    try:
        import json

        data = json.loads(_config_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    block = data.get(CONFIG_KEY) if isinstance(data, dict) else None
    return block if isinstance(block, dict) else {}


def _write_block(block: dict[str, Any]) -> None:
    """Merge ``block`` into the ``update_check`` key, through the SDK's lock.

    Same rule as telemetry: this file holds the login session, so any writer of
    it takes ``config_lock`` and goes through ``save_config``'s atomic replace.
    """
    from lemma_sdk.config import config_lock, load_config, save_config

    path = _config_path()
    try:
        with config_lock(path):
            # load_config raises on a corrupt file, so an unparseable config is
            # left alone rather than replaced by a stub holding only this block.
            config = load_config(path)
            config[CONFIG_KEY] = {**(config.get(CONFIG_KEY) or {}), **block}
            save_config(path, config)
    except Exception:
        # An update check must never be why a command failed.
        return


def is_enabled() -> bool:
    if (os.getenv(DISABLE_ENV) or "").strip() in {"0", "false", "off", "no"}:
        return False
    # Where `lemma update` cannot act, the notice would name a command that does
    # nothing. Stay quiet instead.
    return install_kind().can_update


def fetch_server_api_version(
    base_url: str, *, verify_ssl: bool = True, timeout: float = _TIMEOUT_SECONDS
) -> tuple[str | None, str | None]:
    """Return ``(api_version, error)`` from a server's ``/openapi.json``.

    stdlib only: this runs on a daemon thread after the command has finished,
    and `lemma doctor` calls it too (``commands/system.py``) — one fetch, one
    definition of what "the server's version" means.
    """
    import json
    import ssl
    import urllib.request

    url = base_url.rstrip("/") + "/openapi.json"
    context = None
    if not verify_ssl:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("info", {}).get("version") or "") or None, None
    except Exception as exc:  # network/parse errors are diagnostics, not fatal
        return None, str(exc)


def _release_parts(version: str) -> tuple[int, ...] | None:
    """``"0.7.3"`` -> ``(0, 7, 3)``; anything else (a pre-release, "unknown") is
    None, and an uncomparable pair never produces a notice."""
    pieces = version.strip().split(".")
    if len(pieces) != 3:
        return None
    try:
        return tuple(int(piece) for piece in pieces)
    except ValueError:
        return None


def is_newer(candidate: str | None, current: str | None) -> bool:
    left = _release_parts(candidate or "")
    right = _release_parts(current or "")
    if left is None or right is None:
        return False
    return left > right


def check_now(base_url: str, *, verify_ssl: bool = True) -> None:
    """Record what the server reports. Swallows everything; never raises."""
    version, _error = fetch_server_api_version(base_url, verify_ssl=verify_ssl)
    block: dict[str, Any] = {_LAST_CHECKED: time.time()}
    if version:
        block[_LATEST_VERSION] = version
    # The timestamp is written even when the fetch failed: an unreachable server
    # should cost one attempt a day, not one per command.
    _write_block(block)


# Both entry points run from `main()`'s finally block, where an exception would
# replace the command's own exit status with a traceback — a closed stderr
# (`lemma … | head`) or a thread the OS will not start is enough. Nothing here is
# worth that, so both swallow, exactly as telemetry does at the same call site.
def maybe_check_in_background() -> None:
    """Start the once-a-day check, if a server was actually dialed.

    Keyed off the base URL the command already used (``errors.dialed_base_url``)
    so this only ever touches a server the invocation was talking to anyway —
    `lemma --help` and every offline command check nothing.
    """
    try:
        if not is_enabled():
            return
        from .errors import dialed_base_url

        base_url = dialed_base_url()
        if not base_url:
            return
        block = _read_block()
        last = block.get(_LAST_CHECKED)
        if (
            isinstance(last, (int, float))
            and (time.time() - last) < CHECK_INTERVAL_SECONDS
        ):
            return
        import threading

        threading.Thread(target=check_now, args=(base_url,), daemon=True).start()
    except Exception:
        return


def notify_if_available() -> None:
    """Print the one-line notice, at most once per released version."""
    try:
        if not is_enabled():
            return
        from .versions import cli_version

        block = _read_block()
        latest = block.get(_LATEST_VERSION)
        if not isinstance(latest, str) or not is_newer(latest, cli_version()):
            return
        if block.get(_NOTIFIED_VERSION) == latest:
            return
        from .state import err_console

        err_console.print(
            f"[dim]lemma {latest} is available (you have {cli_version()}). "
            "Run [/dim][bold]lemma update[/bold][dim] to upgrade.[/dim]"
        )
        _write_block({_NOTIFIED_VERSION: latest})
    except Exception:
        return


# --------------------------------------------------------------------------- #
# Self-upgrade                                                                 #
# --------------------------------------------------------------------------- #


class InstallKind(NamedTuple):
    """Where this CLI lives, and whether `lemma update` can replace it."""

    kind: str
    can_update: bool
    reason: str


def install_kind() -> InstallKind:
    """Classify this installation.

    The two cases that must refuse rather than "succeed":

    * ``PIP_PREFIX`` is set — the workspace sandbox image installs the CLI into a
      read-only layer and overlays ``PIP_PREFIX`` onto ``sys.path``. An install
      there writes a *second* copy that shadows the first for imports while the
      ``lemma`` on PATH still runs the old one, so an "upgrade" would report
      success and change nothing.
    * a source checkout — an editable/local install belongs to git, not to us.
    """
    if os.environ.get("PIP_PREFIX"):
        return InstallKind(
            "overlay",
            False,
            "this CLI is installed in a read-only image layer and PIP_PREFIX "
            "overlays it, so an upgrade would shadow it rather than replace it. "
            "Rebuild or update the image instead.",
        )
    try:
        import lemma_cli

        location = Path(lemma_cli.__file__).resolve()
    except Exception:  # pragma: no cover - lemma_cli is importing us
        location = Path(sys.argv[0]).resolve()
    if "site-packages" not in location.parts:
        return InstallKind(
            "checkout",
            False,
            f"lemma is running from a source checkout ({location.parent}); "
            "update it with git, not with this command.",
        )
    return InstallKind("installed", True, "")


def _find_uv() -> str | None:
    import shutil

    # Mirrors lemma-stack's register.py: a Finder-launched macOS app does not
    # inherit the shell PATH, so look where uv actually installs itself too.
    for directory in (
        Path(sys.executable).resolve().parent,
        Path.home() / ".local" / "bin",
        Path.home() / ".cargo" / "bin",
    ):
        candidate = directory / "uv"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("uv")


def manual_command(version: str | None) -> str:
    spec = f"{DISTRIBUTION}=={version}" if version else DISTRIBUTION
    return f"uv tool install --force {spec}"


def run_upgrade(version: str | None) -> dict[str, Any]:
    """Replace this installation with ``version`` (or the newest release).

    Returns a result payload. Never raises: a failure is a payload with
    ``ok: False`` and the exact command to run by hand, because the one thing an
    upgrade tool must not do is leave the user with no way forward.
    """
    from .versions import cli_version

    current = cli_version()
    kind = install_kind()
    if not kind.can_update:
        # No manual_command here: `uv tool install` is not the answer to either
        # of these — the reason already names the one that is (git, or the
        # image), and offering a command that would make things worse is worse
        # than offering none.
        return {
            "ok": False,
            "current": current,
            "install": kind.kind,
            "error": kind.reason,
        }
    if version and version == current:
        return {
            "ok": True,
            "current": current,
            "target": version,
            "install": kind.kind,
            "action": "already_current",
        }

    uv = _find_uv()
    if uv is None:
        return {
            "ok": False,
            "current": current,
            "install": kind.kind,
            "error": "uv is not installed (https://docs.astral.sh/uv/).",
            "manual_command": manual_command(version),
        }

    import subprocess

    spec = f"{DISTRIBUTION}=={version}" if version else DISTRIBUTION
    command = [uv, "tool", "install", "--force", spec]
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        return {
            "ok": False,
            "current": current,
            "install": kind.kind,
            "error": detail or f"`{' '.join(command)}` exited {proc.returncode}.",
            "manual_command": manual_command(version),
        }
    return {
        "ok": True,
        "current": current,
        "target": version or "latest",
        "install": kind.kind,
        "action": "upgraded",
        "command": " ".join(command),
    }
