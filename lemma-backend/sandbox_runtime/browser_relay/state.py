"""Taking a signed-in session out of the browser, and putting one back.

`agent-browser` reads and writes Playwright's storageState: cookies plus
localStorage. Two things about that shape matter enough to say here.

**It is per session, and a session is a whole browser.** `--session <name>` is
an isolated browser with its own cookies and tabs, so a save taken from a
session that has only ever visited one site contains only that site. That is why
a sign-in happens in a session named for the site rather than in the one the
agent browses with: the scoping is a consequence of where the sign-in happened,
not of a filter applied afterwards that somebody could forget to apply.

**The file is plaintext while it exists.** It is written under `/tmp` with a
mode nobody else can read, read once, and unlinked in a `finally`. It never goes
near `/workspace`, which is the durable volume that survives a pause -- a saved
session left there would outlive the run that captured it and be waiting for
whatever ran next.

The relay does not decide *what* to keep. It hands the backend what the browser
had and takes back what the backend chose to store; the narrowing of a captured
session to the site it belongs to is a policy question, and it is answered where
the public-suffix list lives rather than inside the sandbox.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import secrets

from .chrome import agent_browser_argv

#: Under /tmp, never /workspace. See the module docstring.
_STATE_DIR = Path("/tmp/lemma-relay/state")

#: A storage state larger than this is not a session, it is a site abusing
#: localStorage. Bounded so one page cannot make a capture into a memory
#: problem for the backend that receives it.
_MAX_STATE_BYTES = 2 * 1024 * 1024

_COMMAND_TIMEOUT_SECONDS = 60.0


class StateOperationFailed(RuntimeError):
    """The CLI could not save or load the session."""


def session_for_domain(domain: str) -> str:
    """The browser session a given site's sign-in lives in.

    Named for the site so that what a capture can possibly contain is decided
    by where the person signed in. Sessions are enumerable by anything with a
    shell in this sandbox, which is deliberate and worth being plain about: the
    agent can drive the session it has been given, because driving it is the
    entire point. What it cannot do is reach a *different* site's session, or
    find a saved one lying on disk after the run that used it has ended.
    """
    safe = "".join(c if c.isalnum() or c in "-." else "-" for c in domain.lower())
    return f"login-{safe.strip('-') or 'site'}"


async def _run(argv: list[str]) -> tuple[int, str]:
    """Run the CLI and collect its output without ever waiting for EOF.

    The daemon inherits the pipe, so `communicate()` never returns -- the same
    trap `chrome.py` documents at length. Waiting on the *process* is safe.
    """
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        await asyncio.wait_for(process.wait(), timeout=_COMMAND_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        with suppress(ProcessLookupError):
            process.kill()
        raise StateOperationFailed("the browser CLI did not finish") from exc

    output = ""
    if process.stdout is not None:
        with suppress(Exception):
            # Bounded and non-blocking: whatever has already been buffered, for
            # the log. Nothing waits on more arriving.
            buffered = process.stdout._buffer  # type: ignore[attr-defined]
            output = bytes(buffered[:4096]).decode("utf-8", "replace")
        process.stdout.feed_eof()
    return process.returncode or 0, output


async def save_session(*, session: str) -> dict:
    """Everything this browser session is signed in to, as storage state."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(_STATE_DIR, 0o700)
    path = _STATE_DIR / f"{secrets.token_hex(16)}.json"
    try:
        code, output = await _run(
            agent_browser_argv("state", "save", str(path), session=session)
        )
        if code != 0:
            raise StateOperationFailed(f"state save failed: {output.strip()[:200]}")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise StateOperationFailed("the browser wrote no session") from exc
        if len(raw) > _MAX_STATE_BYTES:
            raise StateOperationFailed(
                f"the session is larger than {_MAX_STATE_BYTES} bytes"
            )
        try:
            state = json.loads(raw)
        except ValueError as exc:
            raise StateOperationFailed("the session is not readable") from exc
        if not isinstance(state, dict):
            raise StateOperationFailed("the session is not an object")
        return state
    finally:
        # The window in which a plaintext session exists on disk ends here,
        # whatever happened above.
        with suppress(OSError):
            path.unlink()


async def load_session(state: dict, *, session: str) -> None:
    """Put a stored session back, so the next page load is already signed in."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(_STATE_DIR, 0o700)
    path = _STATE_DIR / f"{secrets.token_hex(16)}.json"
    try:
        path.write_bytes(json.dumps(state).encode())
        os.chmod(path, 0o600)
        code, output = await _run(
            agent_browser_argv("state", "load", str(path), session=session)
        )
        if code != 0:
            raise StateOperationFailed(f"state load failed: {output.strip()[:200]}")
    except OSError as exc:
        raise StateOperationFailed("the session could not be staged") from exc
    finally:
        with suppress(OSError):
            path.unlink()
