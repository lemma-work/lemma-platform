"""Lemma's own Slack app as the manager of a workspace's teammates.

One Slack app is one bot user, so every teammate answers through an app of its
own, under its own name. Lemma's app answers nobody. It is where a workspace
sees its teammates (the Home tab), adds one ("Add to Slack"), and hands a
channel to one: invited somewhere, it asks the inviter which teammate should
join, brings that teammate's bot in, and leaves.

A delivery is the manager's when Lemma's own app signed it in a workspace where
no teammate answers through that app -- verification then names no receiving
surface, only the app. Workspaces still on the older arrangement, where Lemma's
app *was* a pod's bot, keep it: their deliveries name that pod's surface and
never reach this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue

from app.core.config import settings
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceIngressRequest,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (
    SlackManagerRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.slack import manager_blocks as views
from app.modules.agent_surfaces.platforms.slack import workspace_calls as slack
from app.modules.agent_surfaces.platforms.slack.manager_parser import (
    ManagerInput,
    ManagerInputKind,
    parse_manager_input,
)
from app.modules.agent_surfaces.services import slack_manager_bots as bots
from app.modules.agent_surfaces.services.channel_allow import allow_channel
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.slack_manager_context import (
    ManagerContext,
    Teammate,
    load_context,
    organization_pod_ids,
    person_link_url,
    teammates,
    workspace_surfaces,
)
from app.modules.connectors.contracts.retirement import retire_accounts

logger = get_logger(__name__)


def is_manager_delivery(
    payload: dict[str, JsonValue], receiver_surface_ids: Sequence[UUID] | None
) -> bool:
    """Lemma's own app, verified, with no teammate's surface receiving it.

    ``receiver_surface_ids`` is what the signature check resolved: an empty list
    -- not None, which means an unverified fan-in -- is the manager's own
    candidate matching and nothing else.
    """
    app_id = surface_settings.slack_app_id
    if not app_id or receiver_surface_ids is None or list(receiver_surface_ids):
        return False
    nested = payload.get("payload")
    inner = nested if isinstance(nested, dict) else payload
    return str(inner.get("api_app_id") or "") == app_id


@dataclass(frozen=True)
class ManagerSync:
    """What the webhook request itself answers, before anything is queued.

    ``body`` is the HTTP response Slack reads (a modal's ``response_action``);
    ``publish`` says whether the delivery still goes to the worker.
    """

    body: dict[str, object] | None = None
    publish: bool = False


async def handle_sync(payload: dict[str, JsonValue], uow_factory) -> ManagerSync:
    """The part that cannot wait for a worker: Slack gives a trigger 3 seconds."""
    parsed = parse_manager_input(payload)
    if parsed.kind is ManagerInputKind.ADD_CLICKED:
        async with uow_factory() as uow:
            await bots.open_add_modal(uow, parsed)
        return ManagerSync()
    if parsed.kind is ManagerInputKind.ADD_SUBMITTED:
        async with uow_factory() as uow:
            body = await bots.acknowledge_add(uow, parsed)
        # A problem is shown in the modal and nothing is queued; otherwise the
        # modal says "making the app" and the worker makes it.
        return ManagerSync(body=body, publish=body.get("response_action") == "update")
    return ManagerSync(publish=True)


async def handle_event(uow_factory, request: SurfaceIngressRequest) -> bool:
    """Everything else the manager receives. True when it was the manager's."""
    if (
        not isinstance(request, SurfacePlatformWebhookIngress)
        or request.source != "slack"
    ):
        return False
    if not is_manager_delivery(request.payload, request.receiver_surface_ids):
        return False
    parsed = parse_manager_input(request.payload)
    async with uow_factory() as uow:
        await _route(uow, parsed)
    return True


async def _route(uow: SqlAlchemyUnitOfWork, parsed: ManagerInput) -> None:
    if parsed.kind is ManagerInputKind.UNINSTALLED:
        await _uninstalled(uow, parsed)
        return
    ctx = await load_context(uow, team_id=parsed.team_id, slack_user_id=parsed.user_id)
    if ctx is None:
        logger.info("agent_surfaces.slack_manager.no_install", team_id=parsed.team_id)
        return
    if parsed.kind is ManagerInputKind.HOME_OPENED:
        await show_home(uow, ctx)
    elif parsed.kind is ManagerInputKind.JOINED_CHANNEL:
        await _offer_channel(uow, ctx, parsed)
    elif parsed.kind is ManagerInputKind.JOIN_CLICKED:
        await _hand_over(uow, ctx, parsed)
    elif parsed.kind in (ManagerInputKind.DIRECT_MESSAGE, ManagerInputKind.MENTIONED):
        await _point_at_teammates(uow, ctx, parsed)
    elif parsed.kind is ManagerInputKind.ADD_SUBMITTED:
        await bots.complete_add(uow, ctx, parsed)
        await show_home(uow, ctx)


async def show_home(uow: SqlAlchemyUnitOfWork, ctx: ManagerContext) -> None:
    if not ctx.slack_user_id:
        return
    if ctx.user_id is None:
        view = views.connect_home(
            link_url=person_link_url(ctx.team_id, ctx.slack_user_id),
            team_name=ctx.install.team_name,
        )
    elif not ctx.is_member:
        view = views.outsider_home(
            organization_name=ctx.organization_name or "another organization",
            link_url=person_link_url(ctx.team_id, ctx.slack_user_id),
        )
    else:
        mates = await teammates(uow, ctx)
        # Credentials only for the teammates the Home shows: each is its own
        # app, so each is its own read.
        shown = mates[: views.ROSTER_SHOWN]
        view = views.roster_home(
            team_name=ctx.install.team_name,
            teammates=[await _roster_entry(uow, ctx, mate) for mate in shown],
            lemma_url=settings.frontend_url,
            more=len(mates) - len(shown),
        )
    async with connection_released(uow.session):
        await slack.publish_home(ctx.credentials, user_id=ctx.slack_user_id, view=view)


async def _roster_entry(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, mate: Teammate
) -> views.RosterEntry:
    open_url = None
    if mate.surface is not None:
        app_id = (
            await SurfaceCredentialResolver(uow=uow).slack_webhook_credentials(
                mate.surface
            )
        ).app_id
        if app_id:
            open_url = f"https://slack.com/app_redirect?app={app_id}&team={ctx.team_id}"
    return views.RosterEntry(
        pod_id=str(mate.pod_id),
        name=mate.name,
        bot_user_id=mate.bot_user_id,
        open_url=open_url,
    )


async def _offer_channel(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, parsed: ManagerInput
) -> None:
    """Invited to a channel: ask the inviter, privately, which teammate joins."""
    if not parsed.channel_id or not parsed.user_id:
        return
    if ctx.user_id is None:
        text = (
            "Connect your Lemma account first, and I'll bring a teammate in here: "
            f"<{person_link_url(ctx.team_id, parsed.user_id)}|connect>."
        )
        blocks = None
    else:
        candidates = [
            (str(mate.surface.id), mate.name)
            for mate in await teammates(uow, ctx)
            if mate.surface is not None and mate.can_manage
        ]
        text, blocks = views.channel_picker(
            channel_id=parsed.channel_id, teammates=candidates
        )
    async with connection_released(uow.session):
        await slack.post_ephemeral(
            ctx.credentials,
            channel=parsed.channel_id,
            user=parsed.user_id,
            text=text,
            blocks=blocks,
        )


#: What a Slack refusal means to the person who asked, in their words.
_HANDOFF_REASONS = {
    "missing_scope": "its app is missing a permission. Reinstall it from my Home tab.",
    "not_in_channel": "I'm no longer in the channel. Invite me again.",
    "channel_not_found": "Slack can't find that channel any more.",
    "is_archived": "the channel is archived.",
    "app_cannot_join_channel": "this workspace doesn't let apps into that channel.",
    "restricted_action": "this workspace doesn't let apps into that channel.",
}


async def _hand_over(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, parsed: ManagerInput
) -> None:
    """Bring the chosen teammate's bot in, let it answer there, and step out."""
    channel = parsed.channel_id or ""
    reason = await _bring_in(uow, ctx, parsed)
    if parsed.response_url:
        if reason is None:
            surface = await SurfaceRepository(uow).get(UUID(parsed.surface_id or ""))
            text, blocks = views.handed_over(
                channel_id=channel,
                bot_user_id=(surface.surface_identity_id if surface else "") or "",
            )
        else:
            text, blocks = views.not_handed_over(channel_id=channel, reason=reason)
        await slack.respond(parsed.response_url, text=text, blocks=blocks)


async def _bring_in(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, parsed: ManagerInput
) -> str | None:
    """None when the teammate is in; otherwise why not, to say to the person."""
    channel = parsed.channel_id or ""
    chosen = next(
        (
            mate
            for mate in await teammates(uow, ctx)
            if mate.surface is not None and str(mate.surface.id) == parsed.surface_id
        ),
        None,
    )
    if not channel or chosen is None or chosen.surface is None:
        return "that teammate isn't yours to set up here."
    if not chosen.can_manage:
        return f"only an editor of {chosen.name} can give it a channel."
    surface = chosen.surface
    teammate_credentials = await SurfaceCredentialResolver(uow=uow).for_surface(surface)
    async with connection_released(uow.session):
        private = await slack.is_private_channel(ctx.credentials, channel)
        if private:
            error = await slack.invite(
                ctx.credentials,
                channel=channel,
                user_ids=[surface.surface_identity_id or ""],
            )
        else:
            error = await slack.join_channel(teammate_credentials, channel)
    if error:
        logger.info(
            "agent_surfaces.slack_manager.handoff_failed",
            team_id=ctx.team_id,
            error=error,
            private=bool(private),
        )
        return _HANDOFF_REASONS.get(error, f"Slack said {error}.")
    await allow_channel(
        uow,
        surfaces=SurfaceRepository(uow),
        surface=surface,
        channel_id=channel,
        owner_user_id=ctx.user_id,
    )
    async with connection_released(uow.session):
        await slack.post_message(
            teammate_credentials,
            channel=channel,
            text=f"Hi, I'm {chosen.name}. Mention me to ask me something.",
        )
        await slack.leave_channel(ctx.credentials, channel)
    return None


async def _point_at_teammates(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, parsed: ManagerInput
) -> None:
    """Someone wrote to the manager as if it answered: name who does."""
    if not parsed.channel_id or not parsed.user_id:
        return
    pods = await organization_pod_ids(uow, ctx.organization_id)
    bots_here = [
        surface.surface_identity_id or ""
        for surface in await workspace_surfaces(uow, ctx, pod_ids=pods)
    ]
    text = views.manager_reply(bot_user_ids=[bot for bot in bots_here if bot])
    async with connection_released(uow.session):
        if parsed.kind is ManagerInputKind.DIRECT_MESSAGE:
            await slack.post_message(
                ctx.credentials, channel=parsed.channel_id, text=text
            )
        else:
            await slack.post_ephemeral(
                ctx.credentials,
                channel=parsed.channel_id,
                user=parsed.user_id,
                text=text,
            )


async def _uninstalled(uow: SqlAlchemyUnitOfWork, parsed: ManagerInput) -> None:
    """Lemma's app removed from the workspace: the binding and its token go.

    The teammates' own apps are their own installs and stay; only the manager
    is gone, and installing Lemma again makes a fresh binding.
    """
    if not parsed.team_id:
        return
    repo = SlackManagerRepository(uow.session)
    install = await repo.install_for_team(parsed.team_id)
    if install is None:
        return
    if (
        parsed.revoked_bots is not None
        and install.bot_user_id not in parsed.revoked_bots
    ):
        return
    await retire_accounts(
        uow.session, connector_id="slack", account_ids=[install.account_id]
    )
    await repo.delete_install(parsed.team_id)
    await uow.commit()
    logger.info("agent_surfaces.slack_manager.uninstalled", team_id=parsed.team_id)
