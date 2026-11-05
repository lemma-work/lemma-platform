"""What Lemma's own Slack app says that must not be lost, staged as work.

The manager belongs to an organization, not a pod, and has no channel of its
own to deliver through, so its messages stay Slack-native (a DM from its bot)
rather than channel lines. A message that is owed -- "X is in Slack now", a
teammate's first word in the channel it was handed -- is a work item written
in the transaction that makes it owed and keyed by its cause, so it exists
exactly when that change commits and is sent once. A retry of the change
finds the key queued and adds nothing.

Interactive answers (views, ephemerals, a replaced button message) are not
staged: they answer the person who just acted, and a retried event answers
again at most.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from pydantic import JsonValue, TypeAdapter

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter, org_namespace
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.work_payloads import (
    SlackManagerMessagePayload,
    SlackTeammateHelloPayload,
)
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_SLACK_MANAGER_MESSAGE,
    SURFACE_SLACK_TEAMMATE_HELLO,
    slack_manager_message_key,
    slack_teammate_hello_key,
)
from app.modules.agent_surfaces.domain.credentials import CredentialsLike
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.slack import manager_blocks as views
from app.modules.agent_surfaces.platforms.slack import workspace_calls as slack
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.group_hello import say_hello_in_group
from app.modules.agent_surfaces.services.slack_manager_context import (
    ManagerContext,
    load_context,
    teammates,
)

logger = get_logger(__name__)

#: Block Kit as the views build it, checked once as the JSON a work item carries.
_AS_JSON = TypeAdapter(list[dict[str, JsonValue]])


class SlackSendDeferred(Exception):
    """Slack said "not now" (a rate limit, an outage): the item tries again."""


async def stage_manager_message(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_id: UUID,
    team_id: str,
    slack_user_id: str | None,
    text: str,
    cause: str,
    blocks: Sequence[Mapping[str, JsonValue]] | None = None,
    refresh_home: bool = False,
    account_id: UUID | None = None,
) -> None:
    """Owe this person a DM from the manager, in ``uow``'s transaction.

    ``cause`` names why, once: ``welcome:<install id>``, ``allowed:<account id>``.
    ``account_id`` sends as that grant of Lemma's app instead of the
    workspace's binding, for an install that did not become one.
    """
    if not slack_user_id:
        return
    await WorkWriter.of(uow).add(
        SURFACE_SLACK_MANAGER_MESSAGE,
        org_namespace(organization_id),
        SlackManagerMessagePayload(
            team_id=team_id,
            slack_user_id=slack_user_id,
            text=text,
            blocks=[dict(block) for block in blocks] if blocks else None,
            refresh_home=refresh_home and account_id is None,
            account_id=account_id,
        ),
        dedup_key=slack_manager_message_key(cause),
    )


async def stage_welcome(
    uow: SqlAlchemyUnitOfWork,
    *,
    install_id: UUID,
    team_id: str,
    installer_slack_user_id: str | None,
) -> None:
    """Owe whoever installed Lemma's app its first word, in the binding's
    transaction: the workspace is in, and the teammate to add first, as a
    button. One per binding (``welcome:<install id>``), so a retried bind or a
    reauthorisation of the same binding adds nothing; a binding made again
    after Lemma was removed is a new one, and is welcomed again."""
    if not installer_slack_user_id:
        return
    ctx = await load_context(
        uow, team_id=team_id, slack_user_id=installer_slack_user_id
    )
    if ctx is None:
        return
    waiting = [
        mate
        for mate in await teammates(uow, ctx)
        if mate.surface is None and mate.can_manage
    ]
    first = waiting[0] if waiting else None
    text, blocks = views.welcome(
        team_name=ctx.install.team_name,
        first=(views.teammate_value(first.pod_id, first.agent_id), first.name)
        if first is not None
        else None,
    )
    await stage_manager_message(
        uow,
        organization_id=ctx.organization_id,
        team_id=team_id,
        slack_user_id=installer_slack_user_id,
        text=text,
        blocks=_AS_JSON.validate_python(blocks),
        cause=f"welcome:{install_id}",
    )


async def _credentials_of(
    uow: SqlAlchemyUnitOfWork, payload: SlackManagerMessagePayload
) -> tuple[CredentialsLike, ManagerContext | None] | None:
    """Whose bot sends it: the grant named, else the workspace's manager."""
    if payload.account_id is not None:
        credentials = await SurfaceCredentialResolver(uow=uow).for_account(
            payload.account_id, platform=SurfacePlatform.SLACK
        )
        return credentials, None
    ctx = await load_context(
        uow, team_id=payload.team_id, slack_user_id=payload.slack_user_id
    )
    return (ctx.credentials, ctx) if ctx is not None else None


async def send_manager_message(
    uow: SqlAlchemyUnitOfWork, payload: SlackManagerMessagePayload
) -> None:
    """Send one owed DM. Lemma removed from the workspace since: nothing to send."""
    from app.modules.agent_surfaces.services.slack_manager import refresh_home

    sender = await _credentials_of(uow, payload)
    if sender is None:
        return
    credentials, ctx = sender
    async with connection_released(uow.session):
        channel, error = await slack.open_dm(credentials, payload.slack_user_id)
        if channel:
            error = await slack.post_message(
                credentials,
                channel=channel,
                text=payload.text,
                blocks=payload.blocks,
            )
    if error in slack.TRANSIENT_ERRORS:
        raise SlackSendDeferred(error)
    if payload.refresh_home and ctx is not None:
        # The DM is out, and only a deferred send retries: a failed refresh
        # retrying the item would send it again.
        await refresh_home(uow, ctx)


async def stage_teammate_hello(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_id: UUID,
    surface_id: UUID,
    channel_id: str,
) -> None:
    """Owe the channel the teammate's hello, in ``uow``'s transaction."""
    await WorkWriter.of(uow).add(
        SURFACE_SLACK_TEAMMATE_HELLO,
        org_namespace(organization_id),
        SlackTeammateHelloPayload(surface_id=surface_id, channel_id=channel_id),
        dedup_key=slack_teammate_hello_key(surface_id, channel_id),
    )


async def say_teammate_hello(
    uow: SqlAlchemyUnitOfWork, payload: SlackTeammateHelloPayload
) -> None:
    """The teammate's own bot says main's group hello: who it is, that people
    outside the pod get Public answers, and that the pod keeps what is said."""
    surface = await SurfaceRepository(uow).get(payload.surface_id)
    if surface is None:
        return
    adapter = SurfacePlatformAdapterRegistry().get(surface.surface_type)
    if adapter is None:
        return
    credentials = await SurfaceCredentialResolver(uow=uow).for_surface(surface)
    await say_hello_in_group(
        uow,
        surface=surface,
        adapter=adapter,
        credentials=credentials,
        channel_id=payload.channel_id,
    )
