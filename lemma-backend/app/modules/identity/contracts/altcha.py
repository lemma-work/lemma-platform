"""The proof-of-work sign-in asks for, for other doors strangers knock on.

The same Altcha challenge and one-use store as the auth endpoints. Each door
says whether it asks for one (``enabled``; sign-in's ``AUTH_ALTCHA_ENABLED``
when it does not say), and ``purpose`` keeps a proof solved for one door from
opening another.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.identity.services.auth_abuse import (
    AltchaRejected,
    get_auth_abuse_store,
)

__all__ = ["AltchaRejected", "issue_challenge", "verify_proof"]


async def issue_challenge(
    purpose: str, *, enabled: bool | None = None
) -> Mapping[str, object]:
    """A challenge for the browser to solve, or ``{"enabled": false}``.

    The Altcha wire object, passed to the page as is. ``RuntimeError`` when
    protection is on and cannot issue one (no key, no store).
    """
    return await get_auth_abuse_store().issue_altcha(purpose, enabled=enabled)


async def verify_proof(
    payload: str | None, *, purpose: str, enabled: bool | None = None
) -> None:
    """Accept a solved challenge once, or raise ``AltchaRejected``.

    Does nothing while protection is off.
    """
    await get_auth_abuse_store().verify_altcha(
        payload, purpose=purpose, enabled=enabled
    )
