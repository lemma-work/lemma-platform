from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlparse
from uuid import UUID

from app.core.config import settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.platforms.telegram.client import TelegramClient
from app.modules.apps.contracts import get_ready_pod_app


@dataclass(frozen=True)
class TelegramMiniApp:
    app_id: UUID
    name: str
    url: str | None

    @property
    def label(self) -> str:
        """Return a human-friendly label for Telegram's compact app surfaces."""

        return " ".join(
            word.capitalize()
            for word in self.name.replace("_", " ").replace("-", " ").split()
        )


def telegram_mini_app_url(*, public_slug: str) -> str | None:
    """Return the HTTPS URL Telegram may open for a selected Lemma app.

    Cloud apps keep their isolated app origin. Local public development uses a
    path on the ephemeral HTTPS API tunnel because a quick tunnel cannot mint a
    wildcard subdomain for every local app.
    """

    api_url = settings.api_url.rstrip("/")
    if settings.is_local_mode():
        if urlparse(api_url).scheme != "https":
            return None
        return f"{api_url}/public/telegram-mini-apps/{quote(public_slug, safe='')}"

    app_domain = str(settings.app_base_domain or "").strip()
    if not app_domain:
        return None
    return f"https://{public_slug}.{app_domain}"


async def resolve_telegram_mini_app(
    *,
    uow,
    pod_id: UUID,
    app_id: UUID | None,
) -> TelegramMiniApp | None:
    app = await get_ready_pod_app(uow=uow, pod_id=pod_id, app_id=app_id)
    if app is None:
        return None
    return TelegramMiniApp(
        app_id=app.id,
        name=app.name,
        url=telegram_mini_app_url(public_slug=app.public_slug),
    )


async def sync_telegram_mini_app(
    *,
    surface: AgentSurfaceEntity,
    credential_resolver,
    uow,
) -> None:
    if (
        surface.surface_type is not SurfacePlatform.TELEGRAM
        or credential_resolver is None
    ):
        return
    credentials = await credential_resolver.for_surface(surface)
    if not str(credentials.get("bot_token") or "").strip():
        raise AgentSurfaceValidationError("Telegram bot credentials are unavailable")
    mini_app = await resolve_telegram_mini_app(
        uow=uow,
        pod_id=surface.pod_id,
        app_id=surface.config.telegram.app_id,
    )
    menu_button: dict = {"type": "commands"}
    if mini_app and mini_app.url:
        menu_button = {
            "type": "web_app",
            "text": f"Open {mini_app.label}"[:64],
            "web_app": {"url": mini_app.url},
        }
    client = TelegramClient.from_credentials(credentials, timeout=20)
    await client.call(
        "setMyCommands",
        {
            "commands": [
                {"command": "help", "description": "See what this bot can do"},
                {"command": "retry", "description": "Retry the last failed request"},
            ]
        },
    )
    await client.call("setChatMenuButton", {"menu_button": menu_button})
