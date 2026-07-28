from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlparse
from uuid import UUID

from app.core.config import settings
from app.modules.apps.domain.entities import AppStatus
from app.modules.apps.infrastructure.repositories import AppRepository


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
    if app_id is None:
        return None
    app = await AppRepository(uow).get(app_id)
    if (
        app is None
        or app.id is None
        or app.pod_id != pod_id
        or app.status is not AppStatus.READY
    ):
        return None
    return TelegramMiniApp(
        app_id=app.id,
        name=app.name,
        url=telegram_mini_app_url(public_slug=app.public_slug),
    )
