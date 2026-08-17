"""A websocket client going away is not an error.

Production logged 129 ERROR records a day that were browser tabs closing. They
arrive from uvicorn's websocket layer through asyncio's default exception
handler, which has no way to know the difference, and they were enough on their
own to make an error-rate alert useless.

The filter has to be narrow in both directions: quiet for the disconnect, and
completely untouched for everything else `asyncio` says — because a real
unhandled exception on the loop reaches us the same way.
"""

from __future__ import annotations

import logging

import pytest

from app.core.log import log as log_module
from app.core.log.log import _ClientDisconnectFilter

# One of four records production emits. `ConnectionClosed.__str__` builds a
# different string depending on who closed and whether a close frame came back,
# and only this branch happens to contain "no close frame received" -- which is
# why a filter requiring that phrase let the other three through, 26 a day.
_DISCONNECT = (
    "ConnectionClosedError exception in shielded future\n"
    "future: <Future finished exception=ConnectionClosedError(None, "
    "Close(code=<CloseCode.INTERNAL_ERROR: 1011>, reason='keepalive ping "
    "timeout'), None)>; no close frame received"
)


def _record(
    message: str, *, name: str = "asyncio", level: int = logging.ERROR
) -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1, msg=message,
        args=(), exc_info=None,
    )


@pytest.fixture(autouse=True)
def _at_info(monkeypatch):
    monkeypatch.setattr(log_module, "_configured_log_level", logging.INFO)


def test_a_keepalive_disconnect_is_dropped() -> None:
    assert _ClientDisconnectFilter().filter(_record(_DISCONNECT)) is False


def test_a_real_asyncio_error_still_reports() -> None:
    """The failure mode that would make this filter worse than the noise."""
    record = _record("Task exception was never retrieved\nZeroDivisionError")

    assert _ClientDisconnectFilter().filter(record) is True


def test_a_connection_closed_without_a_keepalive_timeout_still_reports() -> None:
    """A socket that failed mid-write is worth seeing; only the ping timeout is not."""
    record = _record(
        "ConnectionClosedError exception in shielded future; connection reset"
    )

    assert _ClientDisconnectFilter().filter(record) is True


def test_another_library_saying_the_same_words_is_untouched() -> None:
    record = _record(_DISCONNECT, name="app.modules.agent")

    assert _ClientDisconnectFilter().filter(record) is True


def test_it_is_kept_when_the_process_is_running_at_debug(monkeypatch) -> None:
    """Someone chasing a disconnect has turned DEBUG on; do not hide it from them."""
    monkeypatch.setattr(log_module, "_configured_log_level", logging.DEBUG)

    assert _ClientDisconnectFilter().filter(_record(_DISCONNECT)) is True


def test_records_below_warning_are_never_inspected() -> None:
    record = _record(_DISCONNECT, level=logging.INFO)

    assert _ClientDisconnectFilter().filter(record) is True


def test_the_exception_type_counts_even_when_the_message_omits_it() -> None:
    """asyncio sometimes carries the class on exc_info rather than in the text."""
    record = _record("keepalive ping timeout; no close frame received")
    record.exc_info = (ConnectionResetError, ConnectionResetError(), None)
    assert _ClientDisconnectFilter().filter(record) is True

    record.exc_info = (type("ConnectionClosedError", (Exception,), {}), None, None)
    assert _ClientDisconnectFilter().filter(record) is False


def test_the_filter_is_installed_ahead_of_the_exception_scrubber() -> None:
    """Order is load-bearing: the scrubber clears exc_info off the record."""
    handler = logging.StreamHandler()
    log_module._install_safe_exception_filter(handler)

    kinds = [type(f).__name__ for f in handler.filters]
    assert kinds.index("_ClientDisconnectFilter") < kinds.index("_SafeExceptionFilter")


# `websockets` renders a closed connection four ways (see
# `websockets/exceptions.py`, `ConnectionClosed.__str__`). All four are the same
# event -- a client that stopped answering pings -- and all four must be dropped.
_SENT_THEN_RECEIVED = (
    "ConnectionClosedError exception in shielded future\n"
    "future: <Future finished exception=ConnectionClosedError(None, "
    "Close(code=<CloseCode.INTERNAL_ERROR: 1011>, reason='keepalive ping "
    "timeout'), None)>; then received Close(code=<CloseCode.ABNORMAL_CLOSURE: "
    "1006>, reason='')"
)
_RECEIVED_NO_CLOSE_SENT = (
    "ConnectionClosedError exception in shielded future\n"
    "future: <Future finished exception=ConnectionClosedError(Close("
    "code=<CloseCode.INTERNAL_ERROR: 1011>, reason='keepalive ping timeout'), "
    "None, None)>; no close frame sent"
)
_RECEIVED_THEN_SENT = (
    "ConnectionClosedError exception in shielded future\n"
    "future: <Future finished exception=ConnectionClosedError(Close("
    "code=<CloseCode.INTERNAL_ERROR: 1011>, reason='keepalive ping timeout'), "
    "None, None)>; then sent Close(code=<CloseCode.NORMAL_CLOSURE: 1000>, reason='')"
)


@pytest.mark.parametrize(
    "message",
    [_SENT_THEN_RECEIVED, _RECEIVED_NO_CLOSE_SENT, _RECEIVED_THEN_SENT],
    ids=["sent-then-received", "received-no-close-sent", "received-then-sent"],
)
def test_every_keepalive_timeout_phrasing_is_dropped(message):
    """The residual. These three were reaching production error dashboards at
    ERROR while the fourth was filtered, because the filter demanded a phrase
    only the fourth contains."""
    assert _ClientDisconnectFilter().filter(_record(message)) is False


def test_a_socket_that_failed_mid_write_is_still_reported():
    """The reason the filter is narrow at all: a ConnectionClosed that is *not*
    a keepalive timeout is a real fault and must survive."""
    message = (
        "ConnectionClosedError exception in shielded future\n"
        "future: <Future finished exception=ConnectionClosedError(None, "
        "Close(code=<CloseCode.ABNORMAL_CLOSURE: 1006>, reason=''), None)>; "
        "no close frame received"
    )

    assert _ClientDisconnectFilter().filter(_record(message)) is True
