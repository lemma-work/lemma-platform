"""Turning an exception into fields a person can act on, without leaking one.

Lifted out of ``log.py`` whole. It is the one cluster in that module with a
subject of its own -- everything here answers "what can we safely say about this
failure?" -- and ``log.py`` is long enough that the architecture ratchet refuses
to let it grow.

The redaction rule is the reason this is not just ``traceback.format_exc()``:
frames are filtered to this application's own code, capped, and hashed into a
stable fingerprint, and the message and traceback are bounded. A log line is a
place secrets go to be indexed forever.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import traceback
from pathlib import Path
from types import TracebackType

#: Bounds on what one failure may contribute to a log line.
_MAX_ERROR_MESSAGE_CHARS = 4_000
_MAX_ERROR_TRACEBACK_CHARS = 20_000


def _exception_info(
    event_dict: dict[str, object],
) -> tuple[type[BaseException], BaseException, TracebackType | None] | None:
    exc_info = event_dict.get("exc_info")
    if exc_info is True:
        exc_info = sys.exc_info()
    elif isinstance(exc_info, BaseException):
        # structlog takes the exception itself and nine call sites pass it that
        # way. They fell through the tuple check below and lost the message and
        # the traceback. See `test_logging_pipeline` for what that cost.
        exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
    if not exc_info:
        record: logging.LogRecord | None = event_dict.get("_record")
        exc_info = record.exc_info if record is not None else None
    if not exc_info or not isinstance(exc_info, tuple) or len(exc_info) != 3:
        return None
    exc_type, exc, tb = exc_info
    if not isinstance(exc, BaseException) or not isinstance(exc_type, type):
        return None
    return exc_type, exc, tb


def _safe_module_name(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    for marker in ("/lemma-backend/app/", "/lemma-backend/sandbox_runtime/"):
        if marker in normalized:
            relative = normalized.split(marker, 1)[1].rsplit(".", 1)[0]
            prefix = "app" if marker.endswith("/app/") else "sandbox_runtime"
            return prefix + "." + relative.replace("/", ".")
    return Path(filename).stem


def _safe_exception_fields(
    exc_type: type[BaseException],
    tb: TracebackType | None,
    exc: BaseException | None = None,
) -> dict[str, object]:
    """Everything known about a failure, in a form someone can act on.

    This used to emit only the exception's *type* plus module/function/line
    frames — no message, no traceback. That is enough to count failures and
    almost never enough to fix one: "ValueError in service.py:412" does not say
    which value. The message and the formatted traceback are the diagnosis, so
    they are included.
    """
    extracted = traceback.extract_tb(tb) if tb is not None else []
    application = [
        frame
        for frame in extracted
        if "/lemma-backend/app/" in frame.filename.replace("\\", "/")
        or "/lemma-backend/sandbox_runtime/" in frame.filename.replace("\\", "/")
    ]
    selected = (application or extracted)[-8:]
    frames: list[dict[str, object]] = [
        {
            "module": _safe_module_name(frame.filename),
            "function": frame.name,
            "line": frame.lineno,
        }
        for frame in selected
    ]
    fingerprint = "|".join(
        [exc_type.__name__]
        + [f"{frame['module']}:{frame['function']}:{frame['line']}" for frame in frames]
    )
    fields: dict[str, object] = {
        "error_type": exc_type.__name__,
        "error_stack_hash": hashlib.sha256(fingerprint.encode()).hexdigest(),
    }
    if frames:
        fields["error_frames"] = frames
    if exc is not None:
        message = str(exc).strip()
        if message:
            fields["error_message"] = message[:_MAX_ERROR_MESSAGE_CHARS]
        if tb is not None:
            fields["error_traceback"] = "".join(
                traceback.format_exception(exc_type, exc, tb)
            )[-_MAX_ERROR_TRACEBACK_CHARS:]
    return fields
