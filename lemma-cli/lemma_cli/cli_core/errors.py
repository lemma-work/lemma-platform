"""The CLI's last error boundary.

Commands catch `LemmaAPIError` where they can say something specific. Everything
else used to escape to Typer's rich traceback, which renders every httpx and
attrs frame in the stack — hundreds of lines whose entire payload is the final
one. This turns the errors we can recognise into a single actionable line on
stderr, and leaves anything genuinely unexpected alone so it still tracebacks.
"""

from __future__ import annotations

import sys

# Errors the SDK raises that mean "your setup or the server", not "the CLI has a
# bug". Kept as name strings so this module stays importable without the SDK.
_CONNECTION = "LemmaConnectionError"
_TIMEOUT = "LemmaTimeoutError"

# What to try, by status. These are the lines users paste into support: the
# server's envelope already says what happened (code, message, request_id) and
# says nothing about what to do about it. Only statuses with one obvious action
# are listed -- a guessed instruction is worse than none, so a 500 gets silence.
# Lives here rather than in state.py because both error paths need it and this
# module is the one with no SDK import at module scope.
_NEXT_STEP: dict[int, str] = {
    401: "Your session has expired — run `lemma auth login`.",
    403: (
        "You are signed in, but this token has no permission for it. "
        "A pod grant or an org role is missing."
    ),
    404: "Check the pod and the server you are pointed at — `lemma config show`.",
}

# A 409 is any "not in this state": a name taken, but also a question already
# closed, a channel of the wrong kind, a run already finished. Told "something
# with that name already exists" after answering a closed question, a user went
# looking for a name. Only a 409 that says it is a duplicate gets that advice;
# the rest stand on the server's own message, which the error line already shows.
_DUPLICATE_STEP = (
    "Something with that name already exists — pick another, or update it."
)
_DUPLICATE_CODES = ("ALREADY_EXISTS", "NAME_TAKEN", "NAME_CONFLICT", "DUPLICATE")
_DUPLICATE_WORDS = ("already exists", "already in use", "already taken")


def _is_duplicate(exc: object) -> bool:
    code = str(getattr(exc, "code", None) or "").upper()
    message = str(getattr(exc, "message", None) or "").lower()
    return any(part in code for part in _DUPLICATE_CODES) or any(
        words in message for words in _DUPLICATE_WORDS
    )


# What to try, by the server's code, where it says more than the status does.
# An agent deleting a table gets a 403 that no grant or role fixes: a person has
# to approve it. Told "a pod grant or an org role is missing", the agent and the
# person it worked for both went looking for a setting that does not exist.
_NEXT_STEP_BY_CODE: dict[str, str] = {
    "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL": (
        "An agent may not do this on its own. Ask the person to approve it "
        "(request_approval), then try again, or ask them to do it themselves."
    ),
}


def next_step_for(exc: object) -> str | None:
    """The one-line "now do this" for an API error, or None if there isn't one."""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in _NEXT_STEP_BY_CODE:
        return _NEXT_STEP_BY_CODE[code]
    status = getattr(exc, "status_code", None)
    if status == 429:
        # 429 carries its own wait, when the server advised one. Kept out of the
        # table because the message depends on the exception, not just the code.
        retry_after = getattr(exc, "retry_after", None)
        wait = (
            f"Wait {retry_after:g}s and try again"
            if isinstance(retry_after, (int, float))
            else "Wait and try again"
        )
        return f"Rate limited. {wait}, or ask an admin to raise the limit."
    if status == 409:
        return _DUPLICATE_STEP if _is_duplicate(exc) else None
    return _NEXT_STEP.get(status) if isinstance(status, int) else None


# The base URL the last client actually dialed. Recorded by client_session()
# because that is the only place the server is fully resolved (config + --server
# + env), and read only to make a failure message name the right server.
_dialed_base_url: str | None = None


def set_dialed_base_url(url: str | None) -> None:
    global _dialed_base_url
    _dialed_base_url = url


def dialed_base_url() -> str | None:
    return _dialed_base_url


def report_cli_error(exc: BaseException, *, base_url: str | None = None) -> bool:
    """Print a one-line diagnosis for a known error. Return whether we handled it.

    A False return means the caller should re-raise: an error we cannot explain
    better than the traceback can is one the traceback should still show.
    """
    try:
        from lemma_sdk.errors import LemmaAPIError, LemmaConfigError, LemmaError
    except ImportError:  # pragma: no cover - the SDK is a hard dependency
        return False

    if not isinstance(exc, LemmaError):
        return False

    names = {cls.__name__ for cls in type(exc).__mro__}
    server = base_url or _dialed_base_url
    where = f" at {server}" if server else ""

    if _TIMEOUT in names:
        message = (
            f"the server{where} did not respond in time. "
            "Retry, or raise the limit with --timeout."
        )
    elif _CONNECTION in names:
        message = (
            f"cannot reach the Lemma server{where} ({exc}). "
            "Check that it is running and that `lemma config show` points at "
            "the server you meant — `lemma servers list` shows the rest."
        )
    elif isinstance(exc, LemmaConfigError):
        message = f"{exc} Run `lemma init` to set up this CLI."
    elif isinstance(exc, LemmaAPIError):
        # No `({status})` prefix: LemmaAPIError.__str__ already opens with
        # `[{status}] {code}: {message}`, so adding one printed it twice.
        step = next_step_for(exc)
        message = f"request failed: {exc}" + (f"\n       {step}" if step else "")
    else:
        message = str(exc) or type(exc).__name__

    print(f"error  {message}", file=sys.stderr)  # noqa: T201 — stderr, pre-state
    return True
