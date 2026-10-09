"""Whether pods may be reached from the web by people outside them.

A pod's web chat and its forms (``/public/web``, the hosted pages, tables
opened to visitors) answer anybody holding a key copied off a page. That is a
deliberate exposure, so a deployment turns it on deliberately:
``PUBLIC_WEB_ENABLED`` is off until an operator sets it, and while it is off
every one of those endpoints answers 404 and nothing can be set to answer
anybody. Here in core, not in the module that serves the chat, because every
module that opens something to visitors asks it.

``PUBLIC_PAGES_URL`` is where Lemma's hosted chat and form pages are served: an
origin routed to the API that shares no cookies with it. Unset, they are served
on ``API_URL``.
"""

from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import settings
from app.core.settings_env import dotenv_path


class PublicWebSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    public_web_enabled: bool = Field(
        default=False,
        description=(
            "Serve pods' web chat and forms to people outside them: every "
            "/public/web endpoint and hosted page, widgets that answer "
            "anybody, and tables opened to visitors."
        ),
    )
    public_web_altcha_enabled: bool = Field(
        default=True,
        description=(
            "Ask a visitor's browser to solve an Altcha proof-of-work before a "
            "new web chat starts and before an email code is sent. On by "
            "default, independent of sign-in's AUTH_ALTCHA_ENABLED, and needs "
            "no key of its own: without AUTH_ALTCHA_HMAC_KEY the challenges "
            "are signed with a key derived from the platform's signing key."
        ),
    )
    public_pages_url: str | None = Field(
        default=None,
        description=(
            "The origin hosted chat and form pages are served on, routed to "
            "this API and sharing no cookies with it, e.g. "
            "https://pages.example.com. Unset, they are served on API_URL."
        ),
    )


public_web_settings = PublicWebSettings()


def public_web_enabled() -> bool:
    return public_web_settings.public_web_enabled


def public_web_altcha_enabled() -> bool:
    return public_web_settings.public_web_altcha_enabled


def hosted_pages_base() -> str:
    """The URL hosted pages are served under, with no trailing slash."""
    return (public_web_settings.public_pages_url or str(settings.api_url)).rstrip("/")


def hosted_pages_origin() -> str:
    """The origin hosted pages run on, which every widget allows."""
    parts = urlsplit(hosted_pages_base().strip())
    return f"{parts.scheme}://{parts.netloc}".lower()


def hosted_pages_host() -> str | None:
    """The ``Host`` pages must arrive on, or ``None`` when they share the API's."""
    if not public_web_settings.public_pages_url:
        return None
    return urlsplit(public_web_settings.public_pages_url.strip()).netloc.lower()
