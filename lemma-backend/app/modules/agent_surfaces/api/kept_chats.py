"""A visitor's chat, kept in a cookie by a page Lemma hosts.

Only on Lemma's own pages host: there the cookie is first-party and HttpOnly,
so it outlives the week after which Safari clears what a script stores. A
widget on somebody else's site has only its ``localStorage``; a cookie of ours
there would be a third party's, which browsers block.

The cookie holds the session's secret sealed with the deployment's cipher,
never the secret itself, and is named by the widget's id -- nothing a request
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


def kept_secret(request: Request, widget: WebWidget) -> str | None:
    """The secret this widget's cookie keeps, if it holds one this deployment
    sealed: anything else is as if there were no cookie."""
    # The cipher, and what it pulls in, only for a page that keeps a chat.
    from cryptography.fernet import InvalidToken

    from app.core.crypto import get_secret_cipher
    from app.core.crypto.envelope import is_encrypted_str

    sealed = request.cookies.get(_name(widget))
    if not sealed or not is_encrypted_str(sealed):
        return None
    try:
        return get_secret_cipher().decrypt_str(sealed)
    except ValueError, KeyError, InvalidToken, RuntimeError:
        # Tampered, or sealed under a key since retired: start afresh.
        return None


def keep(response: Response, widget: WebWidget, secret: str) -> None:
    from app.core.crypto import get_secret_cipher

    sealed = get_secret_cipher().encrypt_str(secret)
    if sealed is None:  # pragma: no cover - a secret was given
        return
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
