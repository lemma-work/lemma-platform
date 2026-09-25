"""What a failed provider call is allowed to say about itself in an API error."""

from __future__ import annotations

#: How much of a vendor's error text to carry back. Long enough for the
#: sentence that names the cause, short enough that a stack trace or an HTML
#: error page cannot ride out in an API response.
UPSTREAM_MESSAGE_LIMIT = 400


def upstream_error_details(exc: Exception) -> dict[str, object]:
    details: dict[str, object] = {"error_type": type(exc).__name__}
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        details["upstream_status"] = status_code
    code = getattr(exc, "code", None)
    if isinstance(code, str) and len(code) <= 100:
        details["upstream_code"] = code
    # The one thing worth reading, and it used to be dropped. A Composio
    # auth-config failure answers "Composio does not have managed
    # credentials for this toolkit" -- the whole explanation, in one
    # sentence -- and the caller saw `error_type` and a status code. This is
    # the vendor's own API error, not anything a user typed.
    message = str(exc).strip()
    if message:
        details["upstream_message"] = message[:UPSTREAM_MESSAGE_LIMIT]
    return details
