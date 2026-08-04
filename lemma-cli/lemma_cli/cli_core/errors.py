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
        status = getattr(exc, "status_code", None)
        message = f"request failed ({status}): {exc}" if status else f"request failed: {exc}"
    else:
        message = str(exc) or type(exc).__name__

    print(f"error  {message}", file=sys.stderr)
    return True
