"""Every launcher of the API caps a websocket message at ``WS_MAX_BYTES``.

The cap is uvicorn's (``ws_max_size``), so it is whatever the command that
started the server said -- the Dockerfile, Railway, compose, ``make dev``,
Desktop, the local stack, the test harnesses. One left out serves 16 MiB
messages again, and nothing else would notice. This reads every file in the
repository that starts uvicorn on the API (``app.app:app``, ``local_app:app``,
``standalone_app:app``, a harness's ``spec.app``) and holds each start to the
same number.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.core.server_limits import WS_MAX_BYTES

_ROOT = Path(__file__).resolve().parents[5]

#: uvicorn, then the API's app as its next word or argument -- on a command
#: line, in an argv list (one item per line or not), or ``uvicorn.run(...)``.
#: A mention in backticks is prose, not a start.
_START = re.compile(
    r"(?<!`)\buvicorn\b(?:\.run\()?[\s\"',\-\[\]]*"
    r"(?:\"|')?(?:app\.app:app|local_app:app|standalone_app:app|spec\.app)\b"
)
#: How far after the start its flags may be: the longest argv here carries a
#: comment of twenty lines in the middle of it.
_WINDOW = 2_500
_CAPPED = re.compile(
    rf"--ws-max-size[\"',\s\-]*\"?{WS_MAX_BYTES}\b|ws_max_size=WS_MAX_BYTES\b"
)
_UNDEFLATED = re.compile(
    r"--ws-per-message-deflate[\"',\s\-]*\"?false\b"
    r"|ws_per_message_deflate=WS_PER_MESSAGE_DEFLATE\b"
)
_SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "target",
    "dist",
    "build",
    "out",
    ".next",
    "__pycache__",
    ".claude",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}
_SUFFIXES = {".py", ".rs", ".json", ".yml", ".yaml", ".sh", ".toml", ".mk"}


def _launch_files() -> list[Path]:
    found: list[Path] = []
    for directory, subdirs, files in os.walk(_ROOT):
        subdirs[:] = [name for name in subdirs if name not in _SKIP_DIRS]
        for name in files:
            path = Path(directory) / name
            if name.startswith("test_") or name.endswith(".md"):
                continue
            if path.suffix in _SUFFIXES or name in {"Dockerfile", "Makefile"}:
                found.append(path)
    return found


def _starts(flag: re.Pattern[str] = _CAPPED) -> list[tuple[Path, int, bool]]:
    starts: list[tuple[Path, int, bool]] = []
    for path in _launch_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError, OSError:
            continue
        if "uvicorn" not in text:
            continue
        for match in _START.finditer(text):
            window = text[match.start() : match.end() + _WINDOW]
            # Only up to the next start: one capped start does not cover the
            # one after it.
            following = _START.search(window, match.end() - match.start())
            if following is not None:
                window = window[: following.start()]
            line = text.count("\n", 0, match.start()) + 1
            starts.append((path, line, bool(flag.search(window))))
    return starts


def test_every_start_of_the_api_caps_websocket_messages():
    starts = _starts()
    uncapped = [
        f"{path.relative_to(_ROOT)}:{line}"
        for path, line, capped in starts
        if not capped
    ]
    assert not uncapped, (
        f"uvicorn starts the API without --ws-max-size {WS_MAX_BYTES} "
        f"(or ws_max_size=WS_MAX_BYTES): {uncapped}"
    )


def test_every_start_of_the_api_turns_off_permessage_deflate():
    """See `WS_PER_MESSAGE_DEFLATE`: one launcher left out deflates VNC again."""
    deflating = [
        f"{path.relative_to(_ROOT)}:{line}"
        for path, line, off in _starts(_UNDEFLATED)
        if not off
    ]
    assert not deflating, (
        "uvicorn starts the API without --ws-per-message-deflate false "
        f"(or ws_per_message_deflate=WS_PER_MESSAGE_DEFLATE): {deflating}"
    )


def test_the_launchers_are_found():
    """The scan sees the launchers it guards: a pattern that matched nothing
    would pass the test above by finding nothing."""
    where = {str(path.relative_to(_ROOT)) for path, _, _ in _starts()}
    for launcher in (
        "lemma-backend/Dockerfile",
        "deploy/railway/railway.json",
        "deploy/compose/docker-compose.yml",
        "Makefile",
        "desktop/locald/src/native_host_pack/build.rs",
        "lemma-stack/lemma_stack/stack/specs.py",
        "lemma-stack/lemma_stack/host_pack.py",
        "lemma-backend/local_app.py",
        "lemma-backend/standalone_app.py",
        "lemma-backend/app/app.py",
        "tests/scenarios/harness/stack.py",
    ):
        assert launcher in where, launcher
