from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from app.core.config import settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.platforms.telegram.client import TelegramClient
from app.modules.apps.contracts import get_ready_pod_app_by_name

logger = get_logger(__name__)


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
    """Return the canonical HTTPS app origin Telegram may open.

    Local app hosts are not publicly reachable HTTPS origins. Publishing those
    origins for Telegram is a tunnel concern, not an alternate app
    serving path in the backend.
    """

    if settings.is_local_mode():
        return None

    app_domain = str(settings.app_base_domain or "").strip()
    if not app_domain:
        return None
    return f"https://{public_slug}.{app_domain}"


async def resolve_telegram_mini_app(
    *,
    uow,
    pod_id: UUID,
    app_name: str | None,
) -> TelegramMiniApp | None:
    app = await get_ready_pod_app_by_name(
        uow=uow,
        pod_id=pod_id,
        app_name=app_name,
    )
    if app is None:
        return None
    return TelegramMiniApp(
        app_id=app.id,
        name=app.name,
        url=telegram_mini_app_url(public_slug=app.public_slug),
    )


@dataclass(frozen=True, slots=True)
class TelegramMiniAppSync:
    """What the two Telegram calls need, resolved while the session is open."""

    credentials: dict[str, object]
    menu_button: dict[str, object]


async def prepare_telegram_mini_app_sync(
    *,
    surface: AgentSurfaceEntity,
    credential_resolver,
    uow,
) -> TelegramMiniAppSync | None:
    """The database half, and the one failure a user can act on.

    Raising here still aborts the surface write, which is the behaviour worth
    keeping: "your bot token is missing" is the caller's problem to fix, and a
    surface created without one is not useful.
    """
    if (
        surface.surface_type is not SurfacePlatform.TELEGRAM
        or credential_resolver is None
    ):
        return None
    credentials = await credential_resolver.for_surface(surface)
    if not str(credentials.get("bot_token") or "").strip():
        raise AgentSurfaceValidationError("Telegram bot credentials are unavailable")
    mini_app = await resolve_telegram_mini_app(
        uow=uow,
        pod_id=surface.pod_id,
        app_name=surface.config.telegram.app_name,
    )
    menu_button: dict = {"type": "commands"}
    if mini_app and mini_app.url:
        menu_button = {
            "type": "web_app",
            "text": f"Open {mini_app.label}"[:64],
            "web_app": {"url": mini_app.url},
        }
    return TelegramMiniAppSync(credentials=credentials, menu_button=menu_button)


async def apply_telegram_mini_app_sync(
    plan: TelegramMiniAppSync,
    *,
    client_factory: Callable[..., TelegramClient] | None = None,
) -> None:
    """The two Telegram round trips. No database work, so nothing to hold.

    ``client_factory`` is injected rather than patched so a test can supply a
    client that fails without reaching inside this module. Defaulted to ``None``
    and resolved here, because a default argument bound to the imported name
    would capture it at import and a test could never replace it anyway -- which
    is what `check_import_bound_defaults` exists to stop.
    """
    factory = client_factory or TelegramClient.from_credentials
    client = factory(plan.credentials, timeout=20)
    await client.call(
        "setMyCommands",
        {
            "commands": [
                {"command": "help", "description": "See what this bot can do"},
                {"command": "retry", "description": "Retry the last failed request"},
            ]
        },
    )
    await client.call("setChatMenuButton", {"menu_button": plan.menu_button})


async def apply_mini_app_sync_absorbing_outages(
    plan: TelegramMiniAppSync,
    *,
    surface_id: UUID,
    client_factory: Callable[..., TelegramClient] | None = None,
) -> None:
    """Run the Telegram half, treating an outage as degraded rather than fatal.

    Named rather than inlined into the deferred closure so it can be called
    directly: by the time this runs the surface is committed, and the difference
    between absorbing and re-raising is the difference between a working surface
    with an unset menu button and no surface at all.
    """
    try:
        await apply_telegram_mini_app_sync(plan, client_factory=client_factory)
    except PLATFORM_TRANSPORT_ERRORS:
        logger.warning(
            "agent_surfaces.telegram.mini_app_sync_failed.degraded",
            surface_id=str(surface_id),
            exc_info=True,
        )


async def sync_telegram_mini_app(
    *,
    surface: AgentSurfaceEntity,
    credential_resolver,
    uow,
    client_factory: Callable[..., TelegramClient] | None = None,
) -> None:
    """Resolve under the session; talk to Telegram after the commit.

    This used to do both with the transaction open, so a pooled connection was
    held across two Telegram round trips on every surface create and update.

    The split also changes what a Telegram outage costs, deliberately. Missing
    credentials still abort the write, because that is the caller's to fix. A
    failed `setMyCommands` no longer does: the surface is already committed and
    works, only its command list and menu button are unset, and throwing away a
    surface the user just configured because Telegram was briefly unavailable is
    the worse outcome. It is reported as degraded so the failure is not silent.
    """
    plan = await prepare_telegram_mini_app_sync(
        surface=surface, credential_resolver=credential_resolver, uow=uow
    )
    if plan is None:
        return

    async def _run() -> None:
        await apply_mini_app_sync_absorbing_outages(
            plan, surface_id=surface.id, client_factory=client_factory
        )

    after_commit = getattr(uow, "after_commit", None)
    if callable(after_commit):
        after_commit(_run)
        return
    await _run()
