"""Adding a teammate to Slack from inside Slack: the manager's "Add to Slack".

The same three ways the web app offers, in a modal:

* the organization already lets Lemma make apps in this workspace -- one button;
* paste a configuration token's refresh token, and Lemma makes this app and
  every later one;
* make the app by hand from Lemma's manifest, and paste its three values.

Each ends at Slack's own Allow, the one step only a person can take. Its
connect request carries ``SLACK_TEAMMATE_APP_PURPOSE``, so when the grant
arrives (``ConnectorAccountConnected``) the teammate's surface is made from it
here (``bind_allowed_app``) and nobody has to come back to Lemma to finish.

Making an app is idempotent per agent and workspace: a resubmitted modal, or
the same Slack retry, finds the pending app and answers with it.
"""

from __future__ import annotations

from urllib.parse import urlencode
from uuid import UUID

from app.core.config import settings
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.services.surface_connector import connector_slug_for
from app.modules.connectors.contracts.pod_connectors import (
    share_own_account_as_pod_connector,
)
from app.modules.agent_surfaces.domain.entities import (
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import SlackAppCreationForbiddenError
from app.modules.agent_surfaces.domain.surface_config import SurfaceConfig
from app.modules.agent_surfaces.domain.slack_manager import (
    SLACK_TEAMMATE_APP_PURPOSE,
    AddForm,
    ManagerInput,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_configuration_token_repository import (  # noqa: E501
    SlackConfigurationTokenRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_pending_app_binding_repository import (  # noqa: E501
    SOURCE_SLACK,
    SlackPendingAppBindingRepository,
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
from app.modules.agent_surfaces.services.slack_app_provisioning import (
    SLACK_CONNECTOR_ID,
    SlackAppCalls,
    SlackAppProvisioner,
)
from app.modules.agent_surfaces.services.slack_manager_context import (
    ManagerContext,
    Teammate,
    load_context,
    sign_in_url,
    teammates,
)
from app.modules.agent_surfaces.services.slack_manager_form import keep_form, take_form
from app.modules.agent_surfaces.services.slack_manager_messages import (
    stage_manager_message,
)
from app.modules.connectors.contracts.facts import ConnectorAccountConnected  # noqa: TC001
from app.modules.connectors.contracts.own_apps import (
    create_own_app_install,
    delete_unauthorised_install,
    mark_install_made_by_lemma,
    may_manage_installs,
    start_authorization,
)

logger = get_logger(__name__)

#: Where Slack's Allow lands: a page that says "back to Slack", nothing more.
ALLOWED_RETURN_PATH = "/slack/allowed"


def slack_app_calls() -> SlackAppCalls:
    """Slack's side of making an app; a test stands in for it here."""
    return SlackAppCalls()


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
    uow: SqlAlchemyUnitOfWork,
    ctx: ManagerContext,
    pod_id: UUID | None,
    agent_id: UUID | None,
) -> Teammate | None:
    return next(
        (
            mate
            for mate in await teammates(uow, ctx)
            if mate.pod_id == pod_id and mate.agent_id == agent_id
        ),
        None,
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
            f"<{sign_in_url(ctx.team_id, ctx.slack_user_id)}|connect>."
        )
    if mate is None:
        return "That teammate isn't one you can see in Lemma."
    if mate.surface is not None:
        return f"{views.escape(mate.name)} is already in Slack."
    if not mate.can_manage:
        return f"Only an editor of {views.escape(mate.name)} can add it to Slack."
    if not await may_manage_installs(
        uow, user_id=ctx.user_id, organization_id=ctx.organization_id
    ):
        return "Only an organization owner or editor can add apps to Slack."
    return None


async def open_add_modal(uow: SqlAlchemyUnitOfWork, parsed: ManagerInput) -> None:
    """In the request: a tap's trigger lives three seconds."""
    ctx = await load_context(uow, team_id=parsed.team_id, slack_user_id=parsed.user_id)
    if ctx is None or not parsed.trigger_id:
        return
    mate = await _teammate(uow, ctx, parsed.pod_id, parsed.agent_id)
    metadata = views.modal_metadata(parsed.pod_id, parsed.agent_id)
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
    uow: SqlAlchemyUnitOfWork, parsed: ManagerInput, form: AddForm
) -> tuple[dict[str, object], ManagerInput | None]:
    """In the request: check the form is answerable, keep its values encrypted,
    and say "making the app" while the worker does.

    Returns Slack's answer, and the input to queue -- with a reference to the
    kept values, never the values -- or None when nothing is queued. Nothing
    slow happens here: a token Slack refuses is reported in the modal a moment
    later, by the worker.
    """
    if form.has_part_of_own_app and not form.has_own_app:
        missing = {
            block: "Paste all three values, or leave them empty."
            for block, given in (
                (CLIENT_ID_BLOCK_ID, bool(form.client_id)),
                (CLIENT_SECRET_BLOCK_ID, bool(form.client_secret.get_secret_value())),
                (
                    SIGNING_SECRET_BLOCK_ID,
                    bool(form.signing_secret.get_secret_value()),
                ),
            )
            if not given
        }
        return _errors(missing), None
    ctx = await load_context(uow, team_id=parsed.team_id, slack_user_id=parsed.user_id)
    if ctx is None:
        return _errors(
            {TOKEN_BLOCK_ID: "Lemma isn't installed in this workspace any more."}
        ), None
    has_token = (
        await SlackConfigurationTokenRepository(uow.session).get(
            ctx.organization_id, ctx.team_id
        )
        is not None
    )
    if form.is_empty and not has_token:
        return _errors(
            {TOKEN_BLOCK_ID: "Paste a refresh token, or the app's three values below."}
        ), None
    reference = None
    if not form.is_empty:
        async with connection_released(uow.session):
            reference = await keep_form(form)
        if reference is None:
            return _errors(
                {TOKEN_BLOCK_ID: "Lemma couldn't take that just now. Try again."}
            ), None
    mate = await _teammate(uow, ctx, parsed.pod_id, parsed.agent_id)
    body: dict[str, object] = {
        "response_action": "update",
        "view": views.working_modal(
            name=mate.name if mate else "the teammate",
            metadata=views.modal_metadata(parsed.pod_id, parsed.agent_id),
        ),
    }
    return body, parsed.model_copy(update={"form_ref": reference})


async def complete_add(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, parsed: ManagerInput
) -> None:
    """In the worker: make the app (or register the one made by hand), and
    hand over its Allow in the modal."""
    metadata = views.modal_metadata(parsed.pod_id, parsed.agent_id)
    form = AddForm()
    if parsed.form_ref:
        async with connection_released(uow.session):
            taken = await take_form(parsed.form_ref)
        if taken is None:
            await _show(
                uow,
                ctx,
                parsed,
                views.problem_modal(
                    message="That took too long and Lemma let go of what you "
                    "pasted. Open Add to Slack again.",
                    metadata=metadata,
                ),
            )
            return
        form = taken
    mate = await _teammate(uow, ctx, parsed.pod_id, parsed.agent_id)
    problem = await _refusal(uow, ctx, mate)
    if problem or mate is None or ctx.user_id is None:
        view = views.problem_modal(
            message=problem or "Nothing to add.", metadata=metadata
        )
    else:
        try:
            view = await _make_and_authorise(uow, ctx, mate, parsed, form)
        except DomainError as exc:
            await uow.rollback()
            view = views.problem_modal(message=str(exc), metadata=metadata)
    await _show(uow, ctx, parsed, view)


async def _show(
    uow: SqlAlchemyUnitOfWork,
    ctx: ManagerContext,
    parsed: ManagerInput,
    view: dict[str, object],
) -> None:
    if parsed.view_id:
        async with connection_released(uow.session):
            await slack.update_view(ctx.credentials, view_id=parsed.view_id, view=view)


async def _own_app_install(
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, mate: Teammate, form: AddForm
) -> UUID:
    """Register the app the person made by hand, once per teammate and workspace."""
    assert ctx.user_id is not None
    pending = SlackPendingAppBindingRepository(uow.session)
    waiting = await pending.for_agent(mate.agent_id, ctx.team_id)
    if waiting is not None:
        return waiting.auth_config_id
    install_id = await create_own_app_install(
        uow,
        user_id=ctx.user_id,
        organization_id=ctx.organization_id,
        connector_id=SLACK_CONNECTOR_ID,
        # Unique per org, and the same however often the modal is resubmitted:
        # the pending row below is what stops a second.
        name=f"{mate.name} ({form.client_id})",
        config={
            "client_id": form.client_id,
            "client_secret": form.client_secret,
            "signing_secret": form.signing_secret,
        },
    )
    added = await pending.add(
        organization_id=ctx.organization_id,
        pod_id=mate.pod_id,
        agent_id=mate.agent_id,
        auth_config_id=install_id,
        team_id=ctx.team_id,
        app_id=None,
        requested_by_user_id=ctx.user_id,
        source=SOURCE_SLACK,
        slack_user_id=ctx.slack_user_id,
    )
    if added is not None:
        return install_id
    # Another submission for this teammate got there first: theirs stands.
    await uow.rollback()
    await delete_unauthorised_install(
        uow, organization_id=ctx.organization_id, auth_config_id=install_id
    )
    await uow.commit()
    winner = await pending.for_agent(mate.agent_id, ctx.team_id)
    if winner is None:
        raise SlackAppCreationForbiddenError()
    return winner.auth_config_id


async def _make_and_authorise(
    uow: SqlAlchemyUnitOfWork,
    ctx: ManagerContext,
    mate: Teammate,
    parsed: ManagerInput,
    form: AddForm,
) -> dict[str, object]:
    if ctx.user_id is None:
        raise SlackAppCreationForbiddenError()
    provisioner = SlackAppProvisioner(uow, calls=slack_app_calls())
    if form.has_refresh_token:
        await provisioner.save_token(
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            refresh_token=form.refresh_token,
        )
    if form.has_own_app:
        app_id = ""
        auth_config_id = await _own_app_install(uow, ctx, mate, form)
    else:
        made = await provisioner.create_app(
            organization_id=ctx.organization_id,
            pod_id=mate.pod_id,
            agent_id=mate.agent_id,
            user_id=ctx.user_id,
            team_id=ctx.team_id,
            # One app per modal, however often Slack retries its submission.
            idempotency_key=f"slack-view:{parsed.view_id}" if parsed.view_id else None,
            source=SOURCE_SLACK,
            slack_user_id=ctx.slack_user_id,
        )
        app_id, auth_config_id = made.app_id, made.auth_config_id
    authorize_url = await start_authorization(
        uow,
        user_id=ctx.user_id,
        organization_id=ctx.organization_id,
        connector_id=SLACK_CONNECTOR_ID,
        auth_config_id=auth_config_id,
        return_to=ALLOWED_RETURN_PATH,
        purpose=SLACK_TEAMMATE_APP_PURPOSE,
    )
    await uow.commit()
    return views.allow_modal(
        name=mate.name,
        metadata=views.modal_metadata(mate.pod_id, mate.agent_id),
        authorize_url=authorize_url,
        app_id=app_id,
    )


async def _free_surface_name(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, team_id: str
) -> str:
    """``slack``, unless this pod already answers in Slack under it."""
    repo = SurfaceRepository(uow)
    for name in ("slack", f"slack-{team_id.lower()}"):
        if await repo.get_by_pod_and_name(pod_id=pod_id, name=name) is None:
            return name
    return f"slack-{team_id.lower()}-{len(await repo.list_by_pod(pod_id))}"


async def bind_allowed_app(
    uow: SqlAlchemyUnitOfWork, fact: ConnectorAccountConnected
) -> bool:
    """Make the teammate's surface from the account its Allow just created.

    Idempotent: the pending row goes in the transaction that makes the
    surface, so a redelivered fact finds nothing to do. The DM saying so is
    staged in that same transaction.
    """
    from app.modules.agent_surfaces.composition import build_surface_service

    pending = SlackPendingAppBindingRepository(uow.session)
    row = await pending.for_auth_config(fact.auth_config_id)
    if row is None:
        return False
    row_id, organization_id = row.id, row.organization_id
    pod_id, agent_id = row.pod_id, row.agent_id
    team_id, slack_user_id = row.team_id, row.slack_user_id
    try:
        if row.app_id is not None:
            # Only Lemma's own create knows the app's id; one made by hand
            # arrives as three pasted values. Said on the install, before the
            # row that knew goes, for every surface on the app to read.
            await mark_install_made_by_lemma(uow, fact.auth_config_id)
        name = await _free_surface_name(uow, pod_id, team_id)
        # The person whose Allow made the account set this teammate up for
        # this pod, which is the consent sharing it needs: it goes into the
        # pod as a SHARED connector, and the surface runs as that.
        pod_connector_slug = await share_own_account_as_pod_connector(
            uow,
            pod_id=pod_id,
            account_id=fact.account_id,
            owner_user_id=fact.user_id,
            slug_base=connector_slug_for(SurfacePlatform.SLACK, name),
            purpose="A Slack teammate's own app",
        )
        surface = await build_surface_service(uow).create_surface(
            pod_id=pod_id,
            agent_id=agent_id,
            platform=SurfacePlatform.SLACK,
            name=name,
            config=SurfaceConfig(),
            credential_mode=SurfaceCredentialMode.CUSTOM,
            pod_connector_slug=pod_connector_slug,
        )
    except DomainError as exc:
        await uow.rollback()
        logger.warning(
            "agent_surfaces.slack_manager.bind_failed.degraded",
            team_id=team_id,
            error_code=exc.code,
            exc_info=True,
        )
        await stage_manager_message(
            uow,
            organization_id=organization_id,
            team_id=team_id,
            slack_user_id=slack_user_id,
            text=f"I couldn't finish adding the teammate: {exc.message}",
            cause=f"bind-failed:{fact.account_id}",
        )
        await uow.commit()
        return False
    await pending.delete(row_id)
    await stage_manager_message(
        uow,
        organization_id=organization_id,
        team_id=team_id,
        slack_user_id=slack_user_id,
        text=(
            f"<@{surface.surface_identity_id}> is in Slack now. Invite it to a "
            "channel, or invite me and I'll bring it in."
        ),
        cause=f"allowed:{fact.account_id}",
        refresh_home=True,
    )
    await uow.commit()
    logger.info(
        "agent_surfaces.slack_manager.allowed",
        team_id=team_id,
        surface_id=str(surface.id),
    )
    return True
