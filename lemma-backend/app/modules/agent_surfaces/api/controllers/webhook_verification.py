"""The GET handshake a platform runs before it will deliver to a callback URL.

WhatsApp echoes ``hub.challenge`` once ``hub.verify_token`` matches; Telegram
only asks that the URL answers. Shared by the platform, per-number and
per-surface callback routes.
"""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import Response

from app.core.webhooks.signatures import constant_time_equals
from app.modules.agent_surfaces.config import surface_webhook_verification_enabled


def token_matches(provided: str | None, expected: str | None) -> bool:
    """Compare a verify token without leaking its length or prefix in timing.

    `==` on a secret returns as soon as two bytes differ, so the time it takes
    says how much of the token was right -- and this one is guessable a
    character at a time by anyone who can reach the endpoint, which is the whole
    internet, because a platform has to. The signature check two functions up
    already uses `compare_digest`; this comparison was the odd one out.

    A missing expected token is never a match. Otherwise an unconfigured
    deployment would accept `hub.verify_token` absent as equal to absent and
    hand out its challenge.

    Through the shared helper rather than `hmac.compare_digest` directly: this
    token arrives as a query parameter, so it is whatever the caller typed, and
    `compare_digest` on two `str`s raises `TypeError` the moment either one
    leaves ASCII. That turned a wrong token into an unauthenticated 500.
    """
    return constant_time_equals(provided, expected)


def webhook_verification_response(
    platform: str, params: dict[str, str], *, whatsapp_verify_token: str | None = None
) -> Response:
    """Shared GET-verification handshake (WhatsApp hub challenge / Telegram ok)."""
    if platform == "whatsapp":
        mode = params.get("hub.mode")
        challenge = params.get("hub.challenge")
        verify_token = params.get("hub.verify_token")

        # The same answer the POST paths get, rather than the raw flag: the
        # switch is honoured on a developer machine and nowhere else.
        security_enabled = surface_webhook_verification_enabled()
        if (
            mode == "subscribe"
            and challenge
            and (
                not security_enabled
                or token_matches(verify_token, whatsapp_verify_token)
            )
        ):
            return Response(content=challenge, media_type="text/plain")

    if platform == "telegram":
        return Response(content="ok", media_type="text/plain")

    raise HTTPException(status_code=403, detail="Verification failed")
