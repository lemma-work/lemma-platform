"""A visitor's chat, kept in a cookie by a page Lemma hosts.

Only on Lemma's own pages host: there the cookie is first-party and HttpOnly,
so it outlives the week after which Safari clears what a script stores. A
widget on somebody else's site has only its ``localStorage``; a cookie of ours
there would be a third party's, which browsers block.

The cookie holds the session's secret sealed by the vault and bound to the
widget, never the secret itself, and is named by the widget's id -- nothing a request
supplied goes into a cookie's name or path.
"""

from __future__ import annotations

from fastapi import Request, Response

from app.core.public_web import hosted_pages_host
from app.modules.agent_surfaces.domain.web_widgets import WebWidget

#: How long a page Lemma hosts remembers its visitor's chat.
KEPT_CHAT_SECONDS = 90 * 24 * 3600
#: Every widget's cookie is sent to every widget's endpoints on the pages host;
#: each is named by its own widget, so a widget reads only its own.
_PATH = "/public/web"


def keeps_chats(request: Request) -> bool:
    """Whether this request reached the origin Lemma hosts pages on."""
    host = hosted_pages_host()
    return host is not None and request.headers.get("host", "").lower() == host


def _name(widget: WebWidget) -> str:
    return f"lemma_chat_{widget.id.hex}"


#: What a kept chat's cookie is sealed for, bound with the widget's id.
_SEALED_PURPOSE = "agent_surfaces.web_widget.kept_chat"


async def kept_secret(request: Request, widget: WebWidget) -> str | None:
    """The secret this widget's cookie keeps, if it holds one this deployment
    sealed for this widget: anything else is as if there were no cookie."""
    # The vault, and what it pulls in, only for a page that keeps a chat.
    from app.modules.vault.contracts import (
        SealedValueInvalid,
        VaultUnavailable,
        is_sealed,
        open_text,
    )

    sealed = request.cookies.get(_name(widget))
    if not sealed:
        return None
    if is_sealed(sealed):
        try:
            return await open_text(
                sealed, purpose=_SEALED_PURPOSE, bindings=(str(widget.id),)
            )
        except SealedValueInvalid, VaultUnavailable:
            # Tampered, moved from another widget, or under a retired key.
            return None
    return await _legacy_secret(sealed)


async def _legacy_secret(sealed: str) -> str | None:
    """A cookie the previous release sealed with the Fernet cipher, so a
    visitor is not logged out by the deploy. Re-sealed on their next visit."""
    from cryptography.fernet import InvalidToken

    from app.core.crypto.envelope import is_encrypted_str
    from app.modules.vault.contracts import legacy_text

    if not is_encrypted_str(sealed):
        return None
    try:
        return await legacy_text(sealed)
    except ValueError, KeyError, InvalidToken, RuntimeError:
        return None


async def keep(response: Response, widget: WebWidget, secret: str) -> None:
    from app.modules.vault.contracts import seal_value

    sealed = await seal_value(
        secret, purpose=_SEALED_PURPOSE, bindings=(str(widget.id),)
    )
    response.set_cookie(
        _name(widget),
        sealed,
        max_age=KEPT_CHAT_SECONDS,
        path=_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )


def forget(response: Response, widget: WebWidget) -> None:
    response.delete_cookie(_name(widget), path=_PATH)
