"""What the API decides about a WebRTC view: who gets one, and what they may do.

The streamer in the sandbox (`sandbox-images/streamer`) enforces the input
boundary itself. It accepts a viewer only with a ticket this module signs, and
a ticket names the mode -- `view` or `control` -- so the decision of who may
drive is made here, from the viewer's permission on the machine, and carried
to the one place that can enforce it. Neither the browser nor the relay ever
holds a ticket's key; the browser never sees a ticket at all.

The key is per machine and derived, not stored, exactly as the relay token is:
HMAC of the runtime credential key over the provider id. Re-delivering it to a
resumed sandbox produces the same bytes, and a key read out of one sandbox can
only sign tickets for that sandbox's own screen.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Literal
from uuid import UUID

from app.modules.workspace.config import workspace_settings

#: Where the streamer reads its key and machine id. Beside the relay token, and
#: outside `/tmp/lemma-browser` for the same reason: quiesce must not take it.
STREAM_KEY_PATH = "/tmp/lemma-relay/stream.json"

#: A ticket only has to survive signalling. Anything longer is a ticket that
#: can be replayed after its viewer has gone.
TICKET_TTL_SECONDS = 60

Transport = Literal["vnc", "webrtc", "auto"]
Mode = Literal["view", "control"]


class StreamKeyUnavailable(RuntimeError):
    """No runtime credential key is configured, so no ticket can be signed."""


def stream_key(provider_id: str) -> str:
    """The per-machine key tickets are signed with, as hex."""
    key = workspace_settings.runtime_credential_key
    if not key:
        raise StreamKeyUnavailable(
            "no runtime credential key is configured, so no stream key can be made"
        )
    return hmac.new(
        key.encode(), f"browser-stream:{provider_id}".encode(), hashlib.sha256
    ).hexdigest()


def stream_key_file(provider_id: str) -> bytes:
    """The file the streamer reads: its key, and the id tickets must name."""
    return json.dumps(
        {"key": stream_key(provider_id), "machine": provider_id}, sort_keys=True
    ).encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_ticket(
    *,
    provider_id: str,
    sub: str,
    mode: Mode,
    now: float | None = None,
) -> str:
    """One viewer's admission to one machine's streamer, in one mode.

    `b64url(payload) "." b64url(HMAC-SHA256(key, b64url(payload)))`. The
    streamer verifies over the exact payload string, so key order and
    whitespace here are this side's business only. `jti` makes each ticket
    single-use: the streamer remembers it for the ticket's lifetime.
    """
    issued = time.time() if now is None else now
    payload = _b64(
        json.dumps(
            {
                "sub": sub,
                "machine": provider_id,
                "mode": mode,
                "exp": int(issued) + TICKET_TTL_SECONDS,
                "jti": secrets.token_urlsafe(12),
            },
            separators=(",", ":"),
        ).encode()
    )
    signature = hmac.new(
        bytes.fromhex(stream_key(provider_id)), payload.encode(), hashlib.sha256
    ).digest()
    return f"{payload}.{_b64(signature)}"


def transport_for(user_id: UUID) -> Transport:
    """How this person watches: the rollout decides, and VNC is the default.

    The share is a stable hash of the user id, so a person is in or out of
    the rollout for good rather than flipping between transports per visit.
    Without a TURN provider there is nothing to relay the media through, and
    everyone watches over VNC.
    """
    settings = workspace_settings
    if settings.turn_provider == "none" or settings.browser_stream == "vnc":
        return "vnc"
    if str(user_id) in settings.browser_stream_users:
        return settings.browser_stream
    bucket = int(hashlib.sha256(f"browser-stream:{user_id}".encode()).hexdigest(), 16)
    if bucket % 100 < settings.browser_stream_percent:
        return settings.browser_stream
    return "vnc"


#: Where the sandbox reads whether it may stream at native density. A file
#: delivered beside the relay token, so turning the setting off reaches a
#: running machine at its next browser start without a new image.
DISPLAY_DECISION_PATH = "/tmp/lemma-relay/display.json"


def display_decision() -> bytes:
    """`{"native_density": bool}`: the server's half of the density decision.

    The machine's half is its CPU count, which only it knows for certain (a
    container's quota is not its host's core count), so the sandbox decides
    density 2 itself from this and from that (`lemma-ensure-display`).
    """
    return json.dumps(
        {"native_density": workspace_settings.browser_native_density}
    ).encode()


__all__ = [
    "STREAM_KEY_PATH",
    "TICKET_TTL_SECONDS",
    "Mode",
    "StreamKeyUnavailable",
    "Transport",
    "DISPLAY_DECISION_PATH",
    "display_decision",
    "mint_ticket",
    "stream_key",
    "stream_key_file",
    "transport_for",
]
