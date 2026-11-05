"""Lemma's own Slack app as the manager of a workspace's teammates.

One Slack app is one bot user, so every teammate answers through an app of its
own, under its own name. Lemma's app answers nobody. It is where a workspace
sees its teammates (the Home tab), adds one ("Add to Slack"), and hands a
channel to one: invited somewhere, it asks the inviter which teammate should
join, brings that teammate's bot in, and leaves.

**Its own lane.** A delivery is the manager's when Lemma's app signed it, no
teammate's surface receives it, and Lemma's app manages that workspace
(``SlackVerification.is_managers``). The webhook controller decides that once,
before onboarding and before ``accept()``: a manager delivery is never stored
as an inbound message (its modal carries pasted secrets), never classified, and
never reaches the group-update path -- where the manager leaving a channel
would read as "the bot left" and archive the teammate's mirror. What must be
answered in Slack's three seconds is answered in the request (``answer_now``);
the rest is one ``surface.slack_manager`` work item carrying ids only
(``queue``), run here (``run``).

**Webhook only.** The manager has no surface, so a Socket Mode deployment
never reaches it; Lemma's app must deliver to the HTTP events URL.

Workspaces still on the older arrangement, where Lemma's app *was* a pod's
bot, keep it: their deliveries name that pod's surface and never come here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from app.core.authorization.scope import uow_scope
from app.core.config import settings
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.work import SYSTEM_NAMESPACE, WorkWriter, org_namespace
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.place_privacy import PlacePrivacy
from app.modules.agent_surfaces.domain.slack_manager import (
    ManagerInput,
    ManagerInputKind,
    ParsedManagerDelivery,
)
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_SLACK_MANAGER,
    slack_manager_key,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (  # noqa: E501
    SlackManagerRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.slack import manager_blocks as views
from app.modules.agent_surfaces.platforms.slack import workspace_calls as slack
from app.modules.agent_surfaces.platforms.slack.home import SlackHomeSurface
from app.modules.agent_surfaces.services import slack_manager_bots as bots
from app.modules.agent_surfaces.services.channel_allow import (
    allow_channel,
    take_back_channel,
)
from app.modules.agent_surfaces.services.slack_manager_context import (
    ManagerContext,
    Teammate,
    load_context,
    load_install,
    organization_pod_ids,
    sign_in_url,
    teammates,
    workspace_surfaces,
)
from app.modules.agent_surfaces.services.slack_manager_messages import (
    stage_teammate_hello,
)
from app.modules.connectors.contracts.retirement import retire_accounts

logger = get_logger(__name__)

#: An ADD_SUBMITTED waits this long before the worker replaces the modal, so
#: Slack has applied the request's own "Making the app…" first.
_AFTER_SLACK_APPLIES = timedelta(seconds=1)

#: Kinds with nothing to do: read so the lane can drop them on purpose.
_DROPPED = frozenset({ManagerInputKind.OTHER, ManagerInputKind.LEFT_CHANNEL})


@dataclass(frozen=True, slots=True)
class RequestAnswer:
    """What the webhook request answers, and what it leaves for the worker.

    ``body`` is the HTTP response Slack reads (a modal's ``response_action``),
    or None for an empty 200. ``queued`` is the input to run off the request.
    """

    body: dict[str, object] | None = None
    queued: ManagerInput | None = None
    delay: timedelta | None = None


async def answer_now(
    uow_factory: UnitOfWorkFactory, delivery: ParsedManagerDelivery
) -> RequestAnswer:
    """The part that cannot wait for a worker: Slack gives a trigger 3 seconds."""
    parsed = delivery.input
    if parsed.kind in _DROPPED:
        return RequestAnswer()
    if parsed.kind is ManagerInputKind.ADD_CLICKED:
        async with uow_scope(uow_factory) as uow:
            await bots.open_add_modal(uow, parsed)
        return RequestAnswer()
    if parsed.kind is ManagerInputKind.ADD_SUBMITTED:
        if delivery.form is None:
            return RequestAnswer()
        async with uow_scope(uow_factory) as uow:
            body, queued = await bots.acknowledge_add(uow, parsed, delivery.form)
        return RequestAnswer(body=body, queued=queued, delay=_AFTER_SLACK_APPLIES)
    # Nothing a person needs in the request: the trigger and the typed values
    # stay behind, only ids go on.
    return RequestAnswer(queued=parsed.model_copy(update={"trigger_id": None}))


async def queue(
    uow_factory: UnitOfWorkFactory,
    parsed: ManagerInput,
    *,
    source_event_id: str,
    delay: timedelta | None = None,
) -> bool:
    """Write the one ``surface.slack_manager`` item for this delivery.

    Keyed by Slack's event id, so a delivery Slack retries while its first is
    still queued adds nothing; and one person's items in one workspace run
    one at a time.
    """
    async with uow_scope(uow_factory) as uow:
        install = await load_install(uow, parsed.team_id)
        namespace = (
            org_namespace(install.organization_id) if install else SYSTEM_NAMESPACE
        )
        item = await WorkWriter.of(uow).add(
            SURFACE_SLACK_MANAGER,
            namespace,
            parsed,
            dedup_key=slack_manager_key(source_event_id),
            concurrency_key=(
                f"slack-manager:{parsed.team_id or '-'}:{parsed.user_id or '-'}"
            ),
            delay=delay,
        )
        await uow.commit()
    return item is not None


async def run(uow: SqlAlchemyUnitOfWork, parsed: ManagerInput) -> None:
    """One queued input, in the worker."""
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
        # The add is done and its Allow is on screen. A retry would take the
        # form again, find it spent, and put "took too long" over that Allow.
        await refresh_home(uow, ctx)


async def refresh_home(uow: SqlAlchemyUnitOfWork, ctx: ManagerContext) -> None:
    """``show_home`` after the real work is done and committed, best effort.

    A Home that fails to refresh is shown again the next time it is opened;
    failing the caller instead would redo what already happened -- a DM sent
    again, an add retried over its own Allow, a sign-in answered as an error.
    Reads only, so rolling back loses nothing and leaves ``uow`` usable.
    """
    try:
        await show_home(uow, ctx)
    except Exception:
        await uow.rollback()
        logger.warning(
            "agent_surfaces.slack_manager.home_refresh_failed.degraded",
            team_id=ctx.team_id,
            exc_info=True,
        )


async def refresh_home_of(
    uow: SqlAlchemyUnitOfWork, *, team_id: str, slack_user_id: str
) -> None:
    """``refresh_home`` for a Slack user named by id; the context is read too."""
    try:
        ctx = await load_context(uow, team_id=team_id, slack_user_id=slack_user_id)
    except Exception:
        await uow.rollback()
        logger.warning(
            "agent_surfaces.slack_manager.home_refresh_failed.degraded",
            team_id=team_id,
            exc_info=True,
        )
        return
    if ctx is not None:
        await refresh_home(uow, ctx)


async def show_home(uow: SqlAlchemyUnitOfWork, ctx: ManagerContext) -> None:
    if not ctx.slack_user_id:
        return
    if ctx.user_id is None:
        view = views.connect_home(
            link_url=sign_in_url(ctx.team_id, ctx.slack_user_id),
            team_name=ctx.install.team_name,
        )
    elif not ctx.is_member:
        view = views.outsider_home(
            organization_name=ctx.organization_name or "another organization",
            link_url=sign_in_url(ctx.team_id, ctx.slack_user_id),
        )
    else:
        mates = await teammates(uow, ctx)
        shown = mates[: views.ROSTER_SHOWN]
        view = views.roster_home(
            team_name=ctx.install.team_name,
            teammates=[_roster_entry(mate) for mate in shown],
            lemma_url=settings.frontend_url,
            more=len(mates) - len(shown),
        )
    async with connection_released(uow.session):
        await slack.publish_home(ctx.credentials, user_id=ctx.slack_user_id, view=view)


def _roster_entry(mate: Teammate) -> views.RosterEntry:
    return views.RosterEntry(
        pod_id=str(mate.pod_id),
        agent_id=str(mate.agent_id),
        name=mate.name,
        bot_user_id=mate.bot_user_id,
    )


async def _offer_channel(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, parsed: ManagerInput
) -> None:
    """Invited to a channel: ask the inviter, privately, which teammate joins.

    The manager stays only while there is a choice to make; with nobody to
    ask, or nothing to offer, it says why and steps out.
    """
    if not parsed.channel_id:
        return
    if not parsed.user_id:
        async with connection_released(uow.session):
            await slack.leave_channel(ctx.credentials, parsed.channel_id)
        return
    candidates: list[tuple[str, str]] = []
    if ctx.user_id is None:
        text = (
            "Connect your Lemma account first, then invite me again and I'll "
            "bring a teammate in here: "
            f"<{sign_in_url(ctx.team_id, parsed.user_id)}|connect>."
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
        if not candidates:
            await slack.leave_channel(ctx.credentials, parsed.channel_id)


#: What a Slack refusal means to the person who asked, in their words.
_HANDOFF_REASONS = {
    "not_in_channel": "I'm no longer in the channel. Invite me again.",
    "channel_not_found": "Slack can't find that channel any more.",
    "is_archived": "the channel is archived.",
    "app_cannot_join_channel": "this workspace doesn't let apps into that channel.",
    "restricted_action": "this workspace doesn't let apps into that channel.",
    "cant_invite": "Slack won't let me invite that teammate there.",
    "no_permission": "Slack won't let me invite that teammate there.",
}


async def _hand_over(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, parsed: ManagerInput
) -> None:
    """Bring the chosen teammate's bot in, let it answer there, and step out."""
    channel = parsed.channel_id or ""
    reason = await _bring_in(uow, ctx, parsed)
    if channel and reason != _NOT_HERE:
        async with connection_released(uow.session):
            await slack.leave_channel(ctx.credentials, channel)
    if not parsed.response_url:
        return
    if reason is None:
        surface = (
            await SurfaceRepository(uow).get(parsed.surface_id)
            if parsed.surface_id
            else None
        )
        text, blocks = views.handed_over(
            channel_id=channel,
            bot_user_id=(surface.surface_identity_id if surface else "") or "",
        )
    else:
        text, blocks = views.not_handed_over(channel_id=channel, reason=reason)
    async with connection_released(uow.session):
        await slack.respond(parsed.response_url, text=text, blocks=blocks)


#: A tap that names a channel other than the one it was tapped in.
_NOT_HERE = "that button isn't for this channel."


async def _bring_in(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, parsed: ManagerInput
) -> str | None:
    """None when the teammate is in; otherwise why not, to say to the person.

    The channel's route and place are written first, so the teammate's own
    ``member_joined_channel`` finds the channel already its own; a refused
    invite takes them back. The channel is mirrored as private unless Slack
    says it is public.
    """
    channel = parsed.channel_id or ""
    if (
        parsed.container_channel_id
        and channel
        and parsed.container_channel_id != channel
    ):
        return _NOT_HERE
    chosen = next(
        (
            mate
            for mate in await teammates(uow, ctx)
            if mate.surface is not None and mate.surface.id == parsed.surface_id
        ),
        None,
    )
    if not channel or chosen is None or chosen.surface is None:
        return "that teammate isn't yours to set up here."
    if not chosen.can_manage:
        return f"only an editor of {views.escape(chosen.name)} can give it a channel."
    surface = chosen.surface
    async with connection_released(uow.session):
        facts = await SlackHomeSurface(credentials=ctx.credentials).place_facts(channel)
    is_private = facts is None or facts.privacy is not PlacePrivacy.PUBLIC
    surfaces = SurfaceRepository(uow)
    allowed = await allow_channel(
        uow,
        surfaces=surfaces,
        surface=surface,
        channel_id=channel,
        owner_user_id=ctx.user_id,
        is_private=is_private,
    )
    async with connection_released(uow.session):
        error = await slack.invite(
            ctx.credentials,
            channel=channel,
            user_ids=[surface.surface_identity_id or ""],
        )
    if error:
        if allowed is not None:
            await take_back_channel(
                uow, surfaces=surfaces, surface_id=surface.id, allowed=allowed
            )
        logger.warning(
            "agent_surfaces.slack_manager.handoff_failed.degraded",
            team_id=ctx.team_id,
            error=error,
            private=is_private,
        )
        return _HANDOFF_REASONS.get(error, f"Slack said {error}.")
    await stage_teammate_hello(
        uow,
        organization_id=ctx.organization_id,
        surface_id=surface.id,
        channel_id=channel,
    )
    await uow.commit()
    logger.info(
        "agent_surfaces.slack_manager.handed_over",
        team_id=ctx.team_id,
        surface_id=str(surface.id),
        private=is_private,
    )
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
        for surface in await workspace_surfaces(uow, ctx.team_id, pod_ids=pods)
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
    is gone, the workspace is free for any organization, and installing Lemma
    again makes a fresh binding. Proofs of its people outlive it.
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
    account_id: UUID = install.account_id
    await retire_accounts(uow.session, connector_id="slack", account_ids=[account_id])
    await repo.delete_install(parsed.team_id)
    await uow.commit()
    logger.info("agent_surfaces.slack_manager.uninstalled", team_id=parsed.team_id)
