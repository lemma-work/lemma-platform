"""Adding a teammate to Slack from inside Slack: the manager's "Add to Slack".

The same three ways the web app offers, in a modal:

* the organization already lets Lemma make apps in this workspace -- one button;
* paste a configuration token's refresh token, and Lemma makes this app and
  every later one;
* make the app by hand from Lemma's manifest, and paste its three values.

Each ends at Slack's own Allow, the one step only a person can take. When the
account that Allow creates arrives, the teammate's surface is made from it here
(``bind_allowed_app``), so nobody has to come back to Lemma to finish.
"""

from __future__ import annotations

from urllib.parse import urlencode
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import SlackAppCreationForbiddenError
from app.modules.agent_surfaces.infrastructure.repositories.slack_configuration_token_repository import (
    SlackConfigurationTokenRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (
    SlackManagerRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.slack import manager_blocks as views
from app.modules.agent_surfaces.platforms.slack import workspace_calls as slack
from app.modules.agent_surfaces.platforms.slack.manager_blocks import (
    CLIENT_ID_BLOCK_ID,
    CLIENT_SECRET_BLOCK_ID,
    SIGNING_SECRET_BLOCK_ID,
    TOKEN_BLOCK_ID,
)
from app.modules.agent_surfaces.platforms.slack.manager_parser import ManagerInput
from app.modules.agent_surfaces.services.slack_app_provisioning import (
    SLACK_CONNECTOR_ID,
    SlackAppProvisioner,
)
from app.modules.agent_surfaces.services.slack_manager_context import (
    ManagerContext,
    Teammate,
    load_context,
    person_link_url,
    teammates,
)
from app.modules.connectors.contracts.own_apps import (
    create_own_app_install,
    may_manage_installs,
    start_authorization,
)

logger = get_logger(__name__)

#: Where Slack's Allow lands: a page that says "back to Slack", nothing more.
ALLOWED_RETURN_PATH = "/slack/allowed"


def new_app_url(app_name: str) -> str:
    """Lemma's manifest, opened in Slack's own "create an app" form.

    Through our API rather than straight to Slack: the manifest URL-encoded is
    longer than a Slack button's link may be.
    """
    query = urlencode({"agent_name": app_name})
    return (
        f"{settings.api_url.rstrip('/')}/surface-setup/slack/manifest/new-app?{query}"
    )


async def _teammate(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, pod_id: str | None
) -> Teammate | None:
    return next(
        (mate for mate in await teammates(uow, ctx) if str(mate.pod_id) == pod_id), None
    )


async def _refusal(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext | None, mate: Teammate | None
) -> str | None:
    """Why this person may not add this teammate to Slack, or None if they may."""
    if ctx is None:
        return "Lemma isn't installed in this workspace any more."
    if ctx.user_id is None:
        if not ctx.slack_user_id:
            return "Connect your Lemma account first."
        return (
            "Connect your Lemma account first: "
            f"<{person_link_url(ctx.team_id, ctx.slack_user_id)}|connect>."
        )
    if mate is None:
        return "That teammate isn't one you can see in Lemma."
    if mate.surface is not None:
        return f"{mate.name} is already in Slack."
    if not mate.can_manage:
        return f"Only an editor of {mate.name} can add it to Slack."
    if not await may_manage_installs(
        uow, user_id=ctx.user_id, organization_id=ctx.organization_id
    ):
        return "Only an organization owner or editor can add apps to Slack."
    return None


async def open_add_modal(uow: SqlAlchemyUnitOfWork, parsed: ManagerInput) -> None:
    ctx = await load_context(uow, team_id=parsed.team_id, slack_user_id=parsed.user_id)
    if ctx is None or not parsed.trigger_id:
        return
    mate = await _teammate(uow, ctx, parsed.pod_id)
    metadata = {"pod": parsed.pod_id or ""}
    problem = await _refusal(uow, ctx, mate)
    if problem or mate is None:
        view = views.problem_modal(
            message=problem or "Nothing to add.", metadata=metadata
        )
    elif await SlackConfigurationTokenRepository(uow.session).get(
        ctx.organization_id, ctx.team_id
    ):
        view = views.add_modal_with_lemma(name=mate.name, metadata=metadata)
    else:
        view = views.add_modal_by_hand(
            name=mate.name, metadata=metadata, manifest_url=new_app_url(mate.name)
        )
    async with connection_released(uow.session):
        await slack.open_modal(ctx.credentials, trigger_id=parsed.trigger_id, view=view)


def _errors(errors: dict[str, str]) -> dict[str, object]:
    return {"response_action": "errors", "errors": errors}


async def acknowledge_add(
    uow: SqlAlchemyUnitOfWork, parsed: ManagerInput
) -> dict[str, object]:
    """Check the form is answerable, and say "making the app" while the worker does.

    Nothing slow happens here -- Slack wants this answer within 3 seconds -- so a
    token Slack refuses is reported in the modal a moment later, by the worker.
    """
    form = parsed.form
    if form.has_part_of_own_app and not form.has_own_app:
        missing = {
            block: "Paste all three values, or leave them empty."
            for block, value in (
                (CLIENT_ID_BLOCK_ID, form.client_id),
                (CLIENT_SECRET_BLOCK_ID, form.client_secret),
                (SIGNING_SECRET_BLOCK_ID, form.signing_secret),
            )
            if not value
        }
        return _errors(missing)
    ctx = await load_context(uow, team_id=parsed.team_id, slack_user_id=parsed.user_id)
    if ctx is None:
        return _errors(
            {TOKEN_BLOCK_ID: "Lemma isn't installed in this workspace any more."}
        )
    has_token = (
        await SlackConfigurationTokenRepository(uow.session).get(
            ctx.organization_id, ctx.team_id
        )
        is not None
    )
    if not (form.refresh_token or form.has_own_app or has_token):
        return _errors(
            {TOKEN_BLOCK_ID: "Paste a refresh token, or the app's three values below."}
        )
    mate = await _teammate(uow, ctx, parsed.pod_id)
    return {
        "response_action": "update",
        "view": views.working_modal(
            name=mate.name if mate else "the teammate",
            metadata={"pod": parsed.pod_id or ""},
        ),
    }


async def complete_add(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, parsed: ManagerInput
) -> None:
    """Make the app (or register the one made by hand), and hand over its Allow."""
    metadata = {"pod": parsed.pod_id or ""}
    mate = await _teammate(uow, ctx, parsed.pod_id)
    problem = await _refusal(uow, ctx, mate)
    if problem or mate is None or ctx.user_id is None:
        view = views.problem_modal(
            message=problem or "Nothing to add.", metadata=metadata
        )
    else:
        try:
            view = await _make_and_authorise(uow, ctx, mate, parsed)
        except DomainError as exc:
            await uow.rollback()
            view = views.problem_modal(message=str(exc), metadata=metadata)
    if parsed.view_id:
        async with connection_released(uow.session):
            await slack.update_view(ctx.credentials, view_id=parsed.view_id, view=view)


async def _make_and_authorise(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, mate: Teammate, parsed: ManagerInput
) -> dict[str, object]:
    if ctx.user_id is None:
        raise SlackAppCreationForbiddenError()
    form = parsed.form
    provisioner = SlackAppProvisioner(uow)
    if form.refresh_token:
        await provisioner.save_token(
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            refresh_token=form.refresh_token,
        )
    if form.has_own_app:
        app_id = ""
        auth_config_id = await create_own_app_install(
            uow,
            user_id=ctx.user_id,
            organization_id=ctx.organization_id,
            connector_id=SLACK_CONNECTOR_ID,
            name=f"{mate.name} ({uuid4().hex[:6]})",
            config={
                "client_id": form.client_id,
                "client_secret": form.client_secret,
                "signing_secret": form.signing_secret,
            },
        )
    else:
        created = await provisioner.create_pod_app(
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            team_id=ctx.team_id,
            app_name=mate.name,
        )
        app_id, auth_config_id = created.app_id, created.auth_config_id
    authorize_url = await start_authorization(
        uow,
        user_id=ctx.user_id,
        organization_id=ctx.organization_id,
        connector_id=SLACK_CONNECTOR_ID,
        auth_config_id=auth_config_id,
        return_to=ALLOWED_RETURN_PATH,
    )
    await SlackManagerRepository(uow.session).add_pending_binding(
        auth_config_id=auth_config_id,
        pod_id=mate.pod_id,
        team_id=ctx.team_id,
        app_id=app_id or None,
        requested_by_user_id=ctx.user_id,
        slack_user_id=ctx.slack_user_id,
    )
    await uow.commit()
    return views.allow_modal(
        name=mate.name,
        metadata={"pod": str(mate.pod_id)},
        authorize_url=authorize_url,
        app_id=app_id,
    )


async def _free_surface_name(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, team_id: str
) -> str:
    """``slack``, unless this pod already answers in another workspace under it."""
    repo = SurfaceRepository(uow)
    for name in ("slack", f"slack-{team_id.lower()}"):
        if await repo.get_by_pod_and_name(pod_id=pod_id, name=name) is None:
            return name
    return f"slack-{uuid4().hex[:8]}"


async def bind_allowed_app(
    uow: SqlAlchemyUnitOfWork,
    *,
    auth_config_id: UUID,
    account_id: UUID,
    user_id: UUID,
) -> bool:
    """Make the teammate's surface from the account its Allow just created.

    Only for an app the manager made, and only for the person who asked: an
    account authorised against that install by somebody else is theirs to use.
    """
    from app.modules.agent_surfaces.composition import build_surface_service

    repo = SlackManagerRepository(uow.session)
    pending = await repo.pending_binding(auth_config_id)
    if pending is None:
        return False
    if pending.requested_by_user_id != user_id:
        # Somebody else's Allow: theirs is not this person's to hand a pod. Say
        # so to whoever asked, rather than leave them waiting on nothing.
        logger.info(
            "agent_surfaces.slack_manager.allowed_by_someone_else",
            team_id=pending.team_id,
        )
        await _tell(
            uow,
            pending.team_id,
            pending.slack_user_id,
            "Someone else allowed the teammate's app with their own Lemma account, "
            "so I left it unconnected. Open the Allow link I gave you and allow it "
            "yourself.",
        )
        return False
    pod_id, team_id, slack_user_id = (
        pending.pod_id,
        pending.team_id,
        pending.slack_user_id,
    )
    try:
        surface = await build_surface_service(uow).create_surface(
            pod_id=pod_id,
            agent_id=pod_id,
            platform=SurfacePlatform.SLACK,
            name=await _free_surface_name(uow, pod_id, team_id),
            credential_mode=SurfaceCredentialMode.CUSTOM,
            account_id=account_id,
        )
    except DomainError as exc:
        await uow.rollback()
        logger.warning(
            "agent_surfaces.slack_manager.bind_failed", team_id=team_id, error=str(exc)
        )
        await _tell(
            uow, team_id, slack_user_id, f"I couldn't finish adding the teammate: {exc}"
        )
        return False
    await SlackManagerRepository(uow.session).drop_pending_binding(auth_config_id)
    await uow.commit()
    await _tell(
        uow,
        team_id,
        slack_user_id,
        f"<@{surface.surface_identity_id}> is in Slack now. Invite it to a channel, "
        "or invite me and I'll bring it in.",
    )
    return True


async def _tell(
    uow: SqlAlchemyUnitOfWork, team_id: str, slack_user_id: str | None, text: str
) -> None:
    """A word in the manager's DM with the person, and their Home brought up to date."""
    from app.modules.agent_surfaces.services.slack_manager import show_home

    if not slack_user_id:
        return
    ctx = await load_context(uow, team_id=team_id, slack_user_id=slack_user_id)
    if ctx is None:
        return
    async with connection_released(uow.session):
        channel = await slack.open_dm(ctx.credentials, slack_user_id)
        if channel:
            await slack.post_message(ctx.credentials, channel=channel, text=text)
    await show_home(uow, ctx)
