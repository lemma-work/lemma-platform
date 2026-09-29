"""Minting the link that connects a Telegram chat to the signed-in user.

The API half of `telegram_chat_link`: this makes the link, the bot redeems it.
Only for the deployment's shared Telegram bot, because that is the one bot
whose chats are not already somebody's -- a pod's own bot answers whoever the
pod lets it, and has no signup to skip.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.errors import (
    TelegramLinkPodUnavailableError,
    TelegramSystemBotUnavailableError,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.onboarding_pod_choice import candidate_pods
from app.modules.agent_surfaces.services.telegram_link_tokens import (
    TelegramLinkTokenStore,
)
from app.modules.identity.contracts.surfaces import user_preferences

NOT_CONFIGURED = (
    "This Lemma has no shared Telegram bot. On Lemma Desktop, add a bot token "
    "under Settings → This Mac → Server setup → Telegram."
)
UNREACHABLE = (
    "Telegram did not answer for this Lemma's bot token. On Lemma Desktop, "
    "check it under Settings → This Mac → Server setup → Telegram."
)


@dataclass(frozen=True, slots=True)
class LinkablePod:
    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class TelegramLinkOptions:
    bot_username: str
    pods: list[LinkablePod]
    #: The pod a link answers from when none is chosen: the one this person's
    #: Telegram already reaches, or else the first they could pick.
    pod_id: UUID | None


@dataclass(frozen=True, slots=True)
class TelegramLink:
    url: str
    bot_username: str
    expires_at: datetime
    pod_id: UUID | None


async def system_bot_username() -> str | None:
    """The shared bot's @username, from `getMe` (cached in Redis per token).

    None both when there is no token and when Telegram does not answer for it;
    the caller tells the two apart by asking whether a token is set.
    """
    from app.modules.agent_surfaces.platforms.telegram.service import (
        TelegramPlatformService,
    )
    from app.modules.agent_surfaces.services.credential_resolver import (
        native_credentials,
    )

    credentials = native_credentials(SurfacePlatform.TELEGRAM)
    if not credentials.get("bot_token"):
        return None
    return await TelegramPlatformService(credentials).get_bot_username()


def system_bot_configured() -> bool:
    from app.modules.agent_surfaces.services.credential_resolver import (
        native_credentials,
    )

    return bool(native_credentials(SurfacePlatform.TELEGRAM).get("bot_token"))


LinkablePods = tuple[list[LinkablePod], UUID | None]


async def linkable_pods(uows: UnitOfWorkFactory, user_id: UUID) -> LinkablePods:
    """Every pod the chat could be attached to, and the one it would be.

    The same list signup offers when it asks "which workspace?", so a pod
    chosen here is one the bot will accept when the link is redeemed.
    """
    async with uows() as uow:
        listed = await candidate_pods(uow, user_id=user_id, limit=None)
        pods = [LinkablePod(UUID(str(pod["id"])), str(pod["name"])) for pod in listed]
        chosen = (await user_preferences(uow, user_id)).default_surface_for(
            SurfacePlatform.TELEGRAM.value
        )
        surface = await SurfaceRepository(uow).get(chosen) if chosen else None
    current = surface.pod_id if surface is not None else None
    if any(pod.id == current for pod in pods):
        return pods, current
    return pods, pods[0].id if pods else None


class TelegramLinkService:
    def __init__(
        self,
        uows: UnitOfWorkFactory,
        *,
        tokens: TelegramLinkTokenStore | None = None,
        bot_username: Callable[[], Awaitable[str | None]] = system_bot_username,
        bot_configured: Callable[[], bool] = system_bot_configured,
        pods_for: Callable[[UUID], Awaitable[LinkablePods]] | None = None,
    ) -> None:
        self._tokens = tokens or TelegramLinkTokenStore()
        self._bot_username = bot_username
        self._bot_configured = bot_configured
        self._pods = pods_for or (lambda user_id: linkable_pods(uows, user_id))

    async def _username(self) -> str:
        if not self._bot_configured():
            raise TelegramSystemBotUnavailableError(NOT_CONFIGURED)
        username = await self._bot_username()
        if not username:
            raise TelegramSystemBotUnavailableError(UNREACHABLE)
        return username

    async def options(self, user_id: UUID) -> TelegramLinkOptions:
        username = await self._username()
        pods, suggested = await self._pods(user_id)
        return TelegramLinkOptions(bot_username=username, pods=pods, pod_id=suggested)

    async def mint(self, user_id: UUID, pod_id: UUID | None) -> TelegramLink:
        username = await self._username()
        pods, suggested = await self._pods(user_id)
        if pod_id is not None and not any(pod.id == pod_id for pod in pods):
            raise TelegramLinkPodUnavailableError()
        chosen = pod_id or suggested
        minted = await self._tokens.mint(user_id=user_id, pod_id=chosen)
        return TelegramLink(
            url=f"https://t.me/{username}?start={minted.start_payload}",
            bot_username=username,
            expires_at=minted.grant.expires_at,
            pod_id=chosen,
        )
