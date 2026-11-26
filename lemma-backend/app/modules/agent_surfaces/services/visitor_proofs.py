"""The proof-of-work a web visitor solves before the pod spends anything on them.

A widget's key is on a page for anybody to copy, so a script holding it could
start chats, mail codes to strangers and post rows as fast as it likes. With
bot protection on (``PUBLIC_WEB_ALTCHA_ENABLED``, the default) each of those
costs a solved challenge: starting an anonymous session, asking for a code, and
every answer to a form from somebody the pod has not confirmed. A proof is
single-use and bound to what it was asked for, so one solved to start a chat
answers nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from app.core.public_web import public_web_altcha_enabled
from app.modules.agent_surfaces.domain.web_widgets import refused
from app.modules.identity.contracts.altcha import (
    AltchaRejected,
    issue_challenge,
    verify_proof,
)

SESSION_PROOF = "web-session"
CODE_PROOF = "web-code"
#: One answer to a form from somebody the pod has not confirmed: each answer
#: costs its own proof, so a session solved once is not a licence to post rows.
ROW_PROOF = "web-row"

#: What a page asks a challenge for, by the word the API takes.
ProofFor = Literal["session", "code", "row"]
_PURPOSES: dict[str, str] = {
    "session": SESSION_PROOF,
    "code": CODE_PROOF,
    "row": ROW_PROOF,
}


async def visitor_challenge(purpose: ProofFor) -> Mapping[str, object]:
    """What a page solves before it does ``purpose``: ``{"enabled": false}``
    when protection is off."""
    try:
        return await issue_challenge(
            _PURPOSES[purpose], enabled=public_web_altcha_enabled()
        )
    except RuntimeError as exc:
        raise refused(
            "The security check is unavailable. Try again shortly.",
            503,
            "altcha_unavailable",
        ) from exc


async def prove_human(payload: str | None, *, purpose: str) -> None:
    """Check a solved proof for ``purpose``, spending it. Passes when the
    deployment has bot protection off."""
    try:
        await verify_proof(
            payload, purpose=purpose, enabled=public_web_altcha_enabled()
        )
    except AltchaRejected as exc:
        raise refused(
            "The security check didn't pass. Try again.", 400, "altcha_failed"
        ) from exc
