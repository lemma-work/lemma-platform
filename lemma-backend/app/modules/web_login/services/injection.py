"""Putting a saved session into the sandbox browser, and nowhere else.

This mirrors ``agent/tools/workspace_cli/github_credential_bridge.py``, which
solves the same shape for ``git``: a credential that a shell tool has to
actually hold, where every other connector's token stays server-side. The rules
it established are the rules here.

**The file, not the argument list.** ``agent-browser --state <path>`` reads a
file, and the file is written over the runtime file API. Argv is world-readable
through ``/proc/<pid>/cmdline`` and is recorded by execve auditing, so a session
blob must never travel that way — and the environment is worse still, because an
ordinary ``env`` would print it straight into a tool result and the transcript.

**``/tmp``, not ``/workspace``.** ``/tmp`` dies with the sandbox; ``/workspace``
is a durable volume that survives every pause. A session left on the durable disk
outlives the reason it was injected.

**Nothing comes back.** :func:`inject_web_login` returns whether it worked, never
what it used. The secret exists inside this function and nowhere else in the
call chain, which is what keeps it out of tool results, transcripts and logs.

**One origin at a time.** The session for the site the agent is going to, not
every session the person owns. A hostile page plus a fully-loaded cookie jar is
the sharpest risk in this design, and the narrow load is what bounds it.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from uuid import uuid4

from app.core.log.log import get_logger
from app.modules.web_login.domain.entities import WebLoginKind, WebLoginSecret
from app.modules.web_login.services.totp import (
    InvalidTotpSeed,
    seconds_remaining,
    totp,
)

logger = get_logger(__name__)

#: Written under /tmp, which dies with the sandbox. Never /workspace.
_STATE_DIR = "/tmp/lemma-web-login"

#: A code with less than this left will expire while the page is submitting.
#: Waiting for the next window beats a login that fails as a wrong secret.
_MIN_TOTP_SECONDS = 5


@dataclass(frozen=True, slots=True)
class InjectionOutcome:
    """What happened, in terms safe to put in a tool result."""

    injected: bool
    reason: str
    #: True when a fresh code was generated. The code itself is not here.
    totp_supplied: bool = False


async def inject_web_login(
    workspace_session,
    secret: WebLoginSecret,
    *,
    kind: WebLoginKind,
) -> InjectionOutcome:
    """Load one saved session into the sandbox browser.

    Returns an outcome, never the secret. The caller reports the outcome to the
    agent; there is nothing in it that would matter if the transcript leaked.
    """
    if kind is WebLoginKind.SESSION and not secret.state:
        return InjectionOutcome(False, "This saved login has no stored session.")

    if not secret.state:
        # A password-only item cannot be injected as a session. Typing it into
        # the page is a separate act, and one that belongs to the person.
        return InjectionOutcome(
            False,
            "This site is saved as a password rather than a session, so there is "
            "nothing to load. Ask the person to sign in once so a session can be "
            "captured.",
        )

    path = f"{_STATE_DIR}/{uuid4().hex}.json"
    await workspace_session.exec_command(
        cmd=f"mkdir -p {shlex.quote(_STATE_DIR)} && chmod 700 {shlex.quote(_STATE_DIR)}",
        timeout=20,
    )
    await workspace_session.write_file(path, secret.state.encode("utf-8"))
    try:
        await workspace_session.exec_command(
            cmd=f"chmod 600 {shlex.quote(path)}", timeout=20
        )
        result = await workspace_session.exec_command(
            cmd=f"agent-browser state load {shlex.quote(path)}", timeout=60
        )
    finally:
        # The window the file exists for is the injection itself. It would die
        # with the sandbox anyway; this makes it die sooner.
        await workspace_session.exec_command(
            cmd=f"rm -f {shlex.quote(path)}", timeout=20
        )

    if not result.get("success") or result.get("exit_code") not in (0, None):
        return InjectionOutcome(
            False, "The browser would not load the saved session for this site."
        )
    return InjectionOutcome(True, "Saved session loaded.")


def current_totp(
    secret: WebLoginSecret, *, at: float | None = None
) -> tuple[str | None, str]:
    """A fresh code and how it went, or ``(None, reason)``.

    The *seed* never reaches the sandbox — only six digits that stop working in
    half a minute. A seed in the same box as the password is not a second
    factor.

    ``at`` exists because the answer depends on where the clock sits inside the
    thirty-second window: a caller retrying after a near-expiry refusal, and a
    test, both need to say which moment they mean rather than race the wall
    clock.
    """
    if not secret.totp_seed:
        return None, "No second-factor seed is saved for this site."
    if seconds_remaining(at=at) < _MIN_TOTP_SECONDS:
        return None, "The current code is about to expire; try again in a moment."
    try:
        return totp(secret.totp_seed, at=at), "Generated."
    except InvalidTotpSeed:
        logger.warning("web_login.totp_seed_unusable.degraded")
        return None, "The saved second-factor seed is not usable."


def capture_command(path: str) -> str:
    """The command that writes the browser's current session out to `path`.

    Its own function because the capture path and the injection path have to
    agree on the format, and `agent-browser state save`/`state load` are a pair.
    """
    return f"mkdir -p {shlex.quote(_STATE_DIR)} && agent-browser state save {shlex.quote(path)}"


def new_state_path() -> str:
    return f"{_STATE_DIR}/{uuid4().hex}.json"


def looks_like_session_state(raw: str) -> bool:
    """Whether captured output is plausibly a session bundle.

    Guards against storing an error message as somebody's login: the failure
    mode without this is a saved item that looks fine in the list and fails
    every time it is used.
    """
    try:
        parsed = json.loads(raw)
    except ValueError:
        return False
    return isinstance(parsed, dict) and bool(
        parsed.get("cookies") or parsed.get("origins")
    )
