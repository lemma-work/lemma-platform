"""Render an exception as text an operator can act on.

``f"{type(exc).__name__}: {exc}"`` is the usual shape, and it quietly fails for
the exceptions that matter most. A transport exception frequently carries no
message at all — ``httpx.ReadError()`` stringifies to ``""`` — so the pattern
produces ``"ReadError: "``, which is then stored as a run's failure reason and
shown to whoever has to work out what happened. It names a category and says
nothing about the event.

The fix is not more text, it is not *less* text than the type name: when there
is no message, the colon and the empty space after it are the only things
removed, and when there is a ``__cause__`` with something to say, it is
appended, because for a wrapped transport failure the cause is the part that
identifies the host, port or timeout involved.
"""

from __future__ import annotations


def describe_exception(exc: BaseException, *, with_cause: bool = True) -> str:
    """``"Type: message"``, degrading to ``"Type"`` rather than ``"Type: "``."""

    text = _one_line(exc)
    rendered = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    if not with_cause:
        return rendered
    cause = exc.__cause__ or exc.__context__
    if cause is None or cause is exc:
        return rendered
    cause_text = _one_line(cause)
    if not cause_text and type(cause) is type(exc):
        # A same-type re-raise with nothing new to say adds only noise.
        return rendered
    caused_by = f"{type(cause).__name__}: {cause_text}" if cause_text else type(cause).__name__
    return f"{rendered} (caused by {caused_by})"


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())
