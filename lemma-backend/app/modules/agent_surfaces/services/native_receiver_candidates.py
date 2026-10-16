"""Which native receivers a deployment should be running, and with what.

One candidate per upstream credential: a Telegram bot to long-poll, a Slack
app to hold a Socket Mode connection for, a Resend key to poll. Split from the
coordinator, which leases and supervises candidates and has no business knowing
how each platform's credentials are spelled. Each platform answers the same two
questions here -- its credentials for a surface with no account of its own, and
the candidate its credentials make -- so adding a platform is one entry in each
table rather than another branch in a chain.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from app.core.config import reveal_secret
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.credentials import (
    CredentialPayload,
    CredentialsLike,
    SlackCredentials,
    SurfaceCredentials,
    TelegramCredentials,
    as_secret,
    credentials_for,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ports import SurfaceAccountPort
from app.modules.agent_surfaces.services.native_receiver_base import (
    NativeReceiverCandidate,
    receiver_key,
)
from app.modules.agent_surfaces.services.resend_polling_receiver import (
    resend_candidate_from_surface,
    resend_receiver_credentials,
)

logger = get_logger(__name__)


def system_telegram_credentials() -> TelegramCredentials | None:
    """The deployment's own bot, when it has one."""
    if not reveal_secret(surface_settings.telegram_bot_token):
        logger.debug(
            "agent_surfaces.event_receiver_service.telegram_system_surface_exists_but.diagnostic"
        )
        return None
    return TelegramCredentials(bot_token=as_secret(surface_settings.telegram_bot_token))


def system_telegram_candidate() -> NativeReceiverCandidate | None:
    """The deployment's shared bot, polled whether or not any surface names it.

    Its key is a fingerprint of its own token like any other, and without a
    stored offset a restart re-reads whatever Telegram still holds.
    """
    token = (reveal_secret(surface_settings.telegram_bot_token) or "").strip()
    if not token:
        return None
    return NativeReceiverCandidate(
        key=receiver_key("telegram", "system", token),
        platform=SurfacePlatform.TELEGRAM,
        surface_ids=(),
        credential_label="system",
        credentials=TelegramCredentials(bot_token=as_secret(token)),
    )


_SYSTEM_CREDENTIALS: dict[SurfacePlatform, Callable[[], SurfaceCredentials | None]] = {
    SurfacePlatform.TELEGRAM: system_telegram_credentials,
    SurfacePlatform.RESEND: resend_receiver_credentials,
}


async def receiver_credentials(
    surface: AgentSurfaceEntity,
    account_port: SurfaceAccountPort,
    account_cache: dict[UUID, CredentialPayload],
) -> SurfaceCredentials | None:
    """The credentials this surface's receiver would run on, or None.

    ``account_cache`` holds each account's stored payload for the scan, so
    surfaces sharing an account read it once.
    """
    if surface.account_id is None:
        system = _SYSTEM_CREDENTIALS.get(surface.surface_type)
        return system() if system is not None else None

    if surface.account_id not in account_cache:
        account = await account_port.get_account(surface.account_id)
        if account is None:
            logger.debug(
                "agent_surfaces.event_receiver_service.native_receiver_skipped_surface_s.diagnostic",
                account_id=surface.account_id,
            )
            return None
        account_cache[surface.account_id] = dict(account.credentials or {})
    credentials = credentials_for(
        surface.surface_type, account_cache[surface.account_id]
    )
    if (
        isinstance(credentials, SlackCredentials)
        and surface.credential_mode is SurfaceCredentialMode.SYSTEM
    ):
        # A workspace on the deployment's own app holds its Socket Mode
        # connection with the deployment's app token, whatever its account says.
        credentials = credentials.model_copy(
            update={"app_token": as_secret(surface_settings.slack_app_token)}
        )
    return credentials


def _telegram_candidate(
    surface: AgentSurfaceEntity, credentials: CredentialsLike
) -> NativeReceiverCandidate | None:
    telegram = TelegramCredentials.of(credentials)
    bot_token = (reveal_secret(telegram.bot_token) or "").strip()
    if not bot_token:
        logger.debug(
            "agent_surfaces.event_receiver_service.telegram_native_receiver_skipped_surface.diagnostic"
        )
        return None
    credential_label = str(surface.account_id) if surface.account_id else "system"
    return NativeReceiverCandidate(
        key=receiver_key("telegram", credential_label, bot_token),
        platform=SurfacePlatform.TELEGRAM,
        surface_ids=(surface.id,),
        credential_label=credential_label,
        credentials=telegram,
    )


def _slack_candidate(
    surface: AgentSurfaceEntity, credentials: CredentialsLike
) -> NativeReceiverCandidate | None:
    slack = SlackCredentials.of(credentials)
    app_token = (reveal_secret(slack.app_token) or "").strip()
    if not app_token:
        logger.debug(
            "agent_surfaces.event_receiver_service.slack_native_receiver_skipped_surface.diagnostic"
        )
        return None
    credential_label = (
        "system"
        if surface.credential_mode is SurfaceCredentialMode.SYSTEM
        else str(surface.account_id)
    )
    return NativeReceiverCandidate(
        key=receiver_key("slack", credential_label, app_token),
        platform=SurfacePlatform.SLACK,
        surface_ids=(surface.id,),
        credential_label=credential_label,
        credentials=slack,
    )


_CANDIDATES: dict[
    SurfacePlatform,
    Callable[[AgentSurfaceEntity, CredentialsLike], NativeReceiverCandidate | None],
] = {
    SurfacePlatform.TELEGRAM: _telegram_candidate,
    SurfacePlatform.SLACK: _slack_candidate,
    SurfacePlatform.RESEND: resend_candidate_from_surface,
}


def candidate_from_surface(
    surface: AgentSurfaceEntity, credentials: CredentialsLike
) -> NativeReceiverCandidate | None:
    """The receiver these credentials make for this surface, if its platform
    has native receivers at all."""
    build = _CANDIDATES.get(surface.surface_type)
    return build(surface, credentials) if build is not None else None


def enabled_receiver_platforms() -> set[SurfacePlatform]:
    """The platforms this deployment runs native receivers for."""
    platforms: set[SurfacePlatform] = set()
    if surface_settings.enable_telegram_polling_mode:
        platforms.add(SurfacePlatform.TELEGRAM)
    if surface_settings.enable_slack_socket_mode:
        platforms.add(SurfacePlatform.SLACK)
    if surface_settings.enable_resend_polling_mode:
        platforms.add(SurfacePlatform.RESEND)
    return platforms


async def load_receiver_candidates(
    uow: SqlAlchemyUnitOfWork,
) -> list[NativeReceiverCandidate]:
    """One candidate per upstream credential the deployment should be polling.

    Surfaces sharing a credential (one Resend key serves every pod address, one
    Slack app many workspaces) merge into one candidate serving all of them.
    """
    platforms = enabled_receiver_platforms()
    if not platforms:
        return []
    from app.modules.agent_surfaces.infrastructure.adapters.account_adapter import (
        SqlAlchemySurfaceAccountAdapter,
    )
    from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
        SurfaceRepository,
    )

    repository = SurfaceRepository(uow)
    account_port = SqlAlchemySurfaceAccountAdapter(uow)
    surfaces = await repository.list_active_native_receiver_surfaces(platforms)
    account_cache: dict[UUID, CredentialPayload] = {}
    candidates: dict[str, NativeReceiverCandidate] = {}
    system_telegram = (
        system_telegram_candidate() if SurfacePlatform.TELEGRAM in platforms else None
    )
    if system_telegram is not None:
        candidates[system_telegram.key] = system_telegram
    for surface in surfaces:
        credentials = await receiver_credentials(surface, account_port, account_cache)
        if credentials is None:
            continue
        candidate = candidate_from_surface(surface, credentials)
        if candidate is None:
            continue
        candidate = replace(candidate, pod_ids=(surface.pod_id,))
        existing = candidates.get(candidate.key)
        if existing is None:
            candidates[candidate.key] = candidate
        else:
            candidates[candidate.key] = NativeReceiverCandidate(
                key=existing.key,
                platform=existing.platform,
                surface_ids=tuple(sorted({*existing.surface_ids, surface.id})),
                credential_label=existing.credential_label,
                credentials=existing.credentials,
                pod_ids=tuple(sorted({*existing.pod_ids, surface.pod_id})),
            )
    return list(candidates.values())


__all__ = [
    "candidate_from_surface",
    "enabled_receiver_platforms",
    "load_receiver_candidates",
    "receiver_credentials",
    "system_telegram_candidate",
]
