"""Short-lived signed tokens for embedding a conversation widget in an iframe.

A widget iframe loads its HTML cross-origin from the API host. In a third-party
(embedded) context the session cookie is only sent when it is ``SameSite=None;
Secure`` — not guaranteed (local dev over HTTP, or any cross-site setup). This
stateless HMAC token — bound to ``(conversation_id, tool_call_id, user_id)`` and
short-lived — lets the widget serve route authenticate the viewer when no session
cookie arrives. Mirrors ``app.modules.datastore.services.files.file_url``.
"""

from __future__ import annotations

import base64
import json
import time
from uuid import UUID

from app.core.crypto import get_secret_signer

#: Signing purpose for the unified signer (HKDF subkey label).
_PURPOSE = "widget-url"


class InvalidWidgetToken(Exception):
    """Raised when a widget embed token is malformed, tampered, or expired."""


def widget_serve_path(conversation_id: UUID | str, tool_call_id: str) -> str:
    """Canonical token-less serve path (frontend embed target + tool-result URL)."""
    return f"/widgets/serve/{conversation_id}/{tool_call_id}"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def mint_widget_token(
    *,
    conversation_id: UUID,
    tool_call_id: str,
    user_id: UUID,
    expires_at_epoch: int,
) -> str:
    payload = json.dumps(
        {
            "c": str(conversation_id),
            "t": tool_call_id,
            "u": str(user_id),
            "e": int(expires_at_epoch),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    # signer.sign returns "<kid>.<sig>"; token is "<payload>.<kid>.<sig>".
    return f"{_b64e(payload)}.{get_secret_signer().sign(_PURPOSE, payload)}"


def verify_widget_token(
    token: str,
    *,
    conversation_id: UUID,
    tool_call_id: str,
    now_epoch: int | None = None,
) -> UUID:
    """Return the viewer ``user_id`` for a valid token bound to ``(conv, tool_call)``.

    Raises ``InvalidWidgetToken`` if malformed, tampered, expired, or minted for a
    different widget (replay binding).
    """
    now = now_epoch if now_epoch is not None else int(time.time())
    try:
        payload_b64, signature = token.split(".", 1)
        payload = _b64d(payload_b64)
        # Tokens are "<kid>.<sig>" and verify via the unified signer (HKDF off the
        # required SECRET_ENCRYPTION_KEY). There is no insecure dev-secret
        # fallback: a non-local deployment without the key fails closed rather
        # than accepting tokens forged with a public constant.
        if not get_secret_signer().verify(_PURPOSE, payload, signature):
            raise InvalidWidgetToken("signature mismatch")
        data = json.loads(payload)
        if int(data["e"]) < now:
            raise InvalidWidgetToken("token expired")
        if data.get("c") != str(conversation_id) or data.get("t") != tool_call_id:
            raise InvalidWidgetToken("token bound to a different widget")
        return UUID(str(data["u"]))
    except InvalidWidgetToken:
        raise
    # Named rather than caught broadly, because "malformed token" is a 4xx and
    # the set of ways this body can be malformed is short and knowable: the
    # split and `UUID(...)` and `int(...)` raise ValueError (`binascii.Error`
    # and `JSONDecodeError` are both subclasses), a payload that decodes to
    # something other than an object gives TypeError or AttributeError, and a
    # missing claim gives KeyError. Anything else is a bug in signing or in
    # this function, and reporting it as somebody else's bad token is how it
    # would stay hidden.
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise InvalidWidgetToken("malformed token") from exc
