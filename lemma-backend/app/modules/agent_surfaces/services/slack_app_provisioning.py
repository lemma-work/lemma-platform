"""Lemma making a teammate its own Slack app, so the person only clicks Allow.

One Slack app is one bot user: a teammate that answers under its own name, with
its own @-handle and its own DM, needs an app of its own. Making one by hand is
four steps -- copy a manifest, create the app, paste three credentials back,
authorise. With the org's configuration token for the workspace, Lemma does the
first three: it creates the app from the agent's manifest and registers its
credentials as the org's own install, the same install the hand-made path ends
in. What is left is the authorisation, which only a person can give.

The configuration token is saved once per (org, workspace) by someone who may
manage the org's installs, since it lets Lemma create apps in that workspace as
the person who generated it. It is encrypted with the secret cipher, and its
columns are in ``app.core.crypto.rotation.REGISTRY`` so a key rotation carries
it forward.

**Rotation.** A refresh token is treated as single-use. A caller that finds the
access token near expiry takes a lease on the row (database clock), rotates
with no connection held, and stores the new pair only if the row's generation
is still the one it read. A caller that finds the lease taken waits briefly and
re-reads, by which time the winner's pair is normally there. A stored token
Slack refuses is marked, and every later use asks for a new paste rather than
calling Slack again. A process that dies between Slack's answer and the commit
loses the new pair; the next rotation is then refused and asks for a paste,
which is the one loss this cannot prevent.

**Creating the app** is idempotent per agent and workspace: a pending row
(``slack_pending_app_bindings``) records the app, a retried create answers from
it, and a creation lease keeps two requests from both reaching Slack. The
install ``create_auth_config`` makes commits on its own, so a create that fails
or is cancelled afterwards undoes itself by deleting the install and the Slack
app, shielded from the cancellation. Apps nobody authorises are swept
(``slack_app_sweep.sweep_unauthorised_apps``).
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.delegation import is_pod_default_agent
from app.core.helpers.humanize import humanize_name
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts.agents import agent_name_in_pod
from app.modules.agent_surfaces.domain.errors import (
    SlackAppCreationBusyError,
    SlackAppCreationForbiddenError,
    SlackAppCreationNotSetUpError,
    SlackAppCreationTokenExpiredError,
    SlackConfigurationTokenRefusedError,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_configuration_token_repository import (  # noqa: E501
    SlackConfigurationTokenRepository,
    reveal_tokens,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_pending_app_binding_repository import (  # noqa: E501
    SOURCE_WEB,
    SlackPendingAppBindingRepository,
)
from app.modules.agent_surfaces.infrastructure.slack_configuration_models import (
    SlackConfigurationTokenModel,
)
from app.modules.agent_surfaces.infrastructure.slack_manager_models import (
    SlackPendingAppBindingModel,
)
from app.modules.agent_surfaces.platforms.slack import app_configuration as slack
from app.modules.agent_surfaces.platforms.slack.manifest import (
    build_slack_app_manifest,
)
from app.modules.agent_surfaces.services import slack_app_create_keys as create_keys
from app.modules.connectors.contracts.own_apps import (
    create_own_app_install,
    delete_unauthorised_install,
    install_is_authorised,
    may_manage_installs,
)
from app.modules.pod.contracts.members import pod_name

logger = get_logger(__name__)


async def _finished[T](work: Awaitable[T]) -> T:
    """``work`` run to its end even if this task is cancelled meanwhile.

    ``asyncio.shield`` alone keeps the work going but lets the cancellation
    unwind the caller at once, and the caller's ``async with`` then closes the
    session the work is still using. So a cancellation here waits for the work
    to finish, then goes on.
    """
    task = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


SLACK_CONNECTOR_ID = "slack"
#: Rotate a little before Slack's 12 hours are up, so a token never expires
#: between being read and being used.
_ROTATE_WITHIN = timedelta(minutes=10)
#: A token this close to expiry is not handed out while another request
#: rotates it; the caller waits for the new one instead.
_STILL_USABLE = timedelta(minutes=1)
#: Longer than one rotation can take (the SDK gives up after
#: ``SLACK_CALL_TIMEOUT_SECONDS``), so a live rotation is never overtaken.
ROTATION_LEASE = timedelta(seconds=60)
#: How often a caller that found the lease taken re-reads before giving up
#: with a retryable 409, and how long it waits between reads.
_LEASE_WAITS = 5
_LEASE_WAIT_SECONDS = 1.0


@dataclass(frozen=True)
class SlackAppWorkspace:
    """A workspace Lemma can create Slack apps in, as an install manager sees it."""

    team_id: str
    team_name: str | None
    #: Whose configuration token it is: apps are created as this Slack user.
    slack_user_id: str | None
    saved_at: datetime
    #: Slack refused the stored token, or Lemma can no longer read it.
    needs_new_token: bool


@dataclass(frozen=True)
class SlackAppWorkspaces:
    """``can_manage`` false means the reader may not make apps, and is shown
    no workspace: the list is the organization's, not the pod's."""

    can_manage: bool
    items: list[SlackAppWorkspace] = field(default_factory=list)


@dataclass(frozen=True)
class ProvisionedSlackApp:
    app_id: str
    auth_config_id: UUID
    #: An app made earlier for the same agent and workspace, or the same key.
    reused: bool = False


@dataclass(frozen=True)
class SlackAppCalls:
    """Slack's side of making an app. A seam, so a test can stand in for Slack."""

    rotate: Callable[[SecretStr], Awaitable[slack.RotatedConfigurationToken]] = (
        slack.rotate_configuration_token
    )
    workspace_name: Callable[[SecretStr], Awaitable[str | None]] = slack.workspace_name
    create_app: Callable[
        [SecretStr, dict[str, object]], Awaitable[slack.CreatedSlackApp]
    ] = slack.create_app
    delete_app: Callable[[SecretStr, str], Awaitable[bool]] = slack.delete_app


@dataclass(frozen=True)
class OrgInstalls:
    """The connectors module's half: who may add installs, adding and removing one."""

    may_manage: Callable[..., Awaitable[bool]] = may_manage_installs
    create_install: Callable[..., Awaitable[UUID]] = create_own_app_install
    delete_unauthorised: Callable[..., Awaitable[bool]] = delete_unauthorised_install
    is_authorised: Callable[..., Awaitable[bool]] = install_is_authorised


@dataclass(frozen=True)
class _Request:
    organization_id: UUID
    pod_id: UUID
    agent_id: UUID
    user_id: UUID
    team_id: str
    source: str = SOURCE_WEB
    slack_user_id: str | None = None


def _workspace(row: SlackConfigurationTokenModel) -> SlackAppWorkspace:
    return SlackAppWorkspace(
        team_id=row.team_id,
        team_name=row.team_name,
        slack_user_id=row.slack_user_id,
        saved_at=row.saved_at,
        needs_new_token=row.refused_at is not None,
    )


def _reused(row: SlackPendingAppBindingModel) -> ProvisionedSlackApp:
    return ProvisionedSlackApp(
        app_id=row.app_id or "", auth_config_id=row.auth_config_id, reused=True
    )


class SlackAppProvisioner:
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        calls: SlackAppCalls | None = None,
        installs: OrgInstalls | None = None,
        lease_wait_seconds: float = _LEASE_WAIT_SECONDS,
    ):
        self.uow = uow
        self.slack = calls or SlackAppCalls()
        self.installs = installs or OrgInstalls()
        self.tokens = SlackConfigurationTokenRepository(uow.session)
        self.pending = SlackPendingAppBindingRepository(uow.session)
        self.lease_wait_seconds = lease_wait_seconds

    async def workspaces(
        self, *, organization_id: UUID, user_id: UUID
    ) -> SlackAppWorkspaces:
        if not await self._may_manage(organization_id, user_id):
            return SlackAppWorkspaces(can_manage=False)
        rows = await self.tokens.list_for_organization(organization_id)
        return SlackAppWorkspaces(
            can_manage=True, items=[_workspace(row) for row in rows]
        )

    async def save_token(
        self, *, organization_id: UUID, user_id: UUID, refresh_token: SecretStr
    ) -> SlackAppWorkspace:
        """Check the refresh token with Slack and keep the pair it rotates to.

        Only the refresh token is asked for: the access token beside it on
        Slack's page expires in hours, and rotating here both proves the refresh
        token works and says which workspace it is for. A token Slack refuses
        here is the paster's to replace, and the error says so.
        """
        await self._require_install_manager(organization_id, user_id)
        async with connection_released(self.uow.session):
            rotated = await self.slack.rotate(refresh_token)
            team_name = await self.slack.workspace_name(rotated.access_token)
        row = await self.tokens.save_pasted(
            organization_id=organization_id,
            token=rotated,
            team_name=team_name,
            saved_by_user_id=user_id,
        )
        await self.uow.commit()
        logger.info(
            "agent_surfaces.slack_app_creation.token_saved",
            organization_id=str(organization_id),
            team_id=rotated.team_id,
        )
        return _workspace(row)

    async def forget(
        self, *, organization_id: UUID, user_id: UUID, team_id: str
    ) -> bool:
        """Drop the saved token. Slack has no call that revokes a configuration
        token, so it stays valid at Slack until its owner revokes it there."""
        await self._require_install_manager(organization_id, user_id)
        removed = await self.tokens.delete(organization_id, team_id)
        await self.uow.commit()
        return removed

    async def create_app(
        self,
        *,
        organization_id: UUID,
        pod_id: UUID,
        agent_id: UUID,
        user_id: UUID,
        team_id: str,
        idempotency_key: str | None = None,
        source: str = SOURCE_WEB,
        slack_user_id: str | None = None,
    ) -> ProvisionedSlackApp:
        """Create the agent's app in Slack and register it as the org's own install.

        Named by the server from the agent, never by the caller: the name is
        the bot's, and the bot is the agent's. ``source`` and ``slack_user_id``
        say where the request came from -- Lemma's Home tab in Slack names the
        Slack user to tell when the app is in.
        """
        await self._require_install_manager(organization_id, user_id)
        request = _Request(
            organization_id,
            pod_id,
            agent_id,
            user_id,
            team_id,
            source=source,
            slack_user_id=slack_user_id,
        )
        app_name = await self._app_name(pod_id, agent_id)
        async with connection_released(self.uow.session):
            known = await create_keys.made_with_key(pod_id, user_id, idempotency_key)
        if known is not None:
            return ProvisionedSlackApp(
                app_id=known.app_id, auth_config_id=known.auth_config_id, reused=True
            )
        waiting = await self.pending.for_agent(agent_id, team_id)
        if waiting is not None:
            return _reused(waiting)
        async with connection_released(self.uow.session):
            lease = await create_keys.take_lease(agent_id, team_id)
        if not lease.held:
            raise SlackAppCreationBusyError(team_id, creating=True)
        try:
            # Read again under the lease: a request that held it a moment ago
            # may have finished in between.
            waiting = await self.pending.for_agent(agent_id, team_id)
            made = (
                _reused(waiting)
                if waiting is not None
                else await self._create(request, app_name)
            )
        finally:
            async with connection_released(self.uow.session):
                await create_keys.release_lease(lease)
        async with connection_released(self.uow.session):
            await create_keys.remember_key(
                pod_id,
                user_id,
                idempotency_key,
                create_keys.MadeApp(made.app_id, made.auth_config_id),
            )
        return made

    async def _create(self, request: _Request, app_name: str) -> ProvisionedSlackApp:
        access_token = await self.fresh_access_token(
            request.organization_id, request.team_id
        )
        manifest = build_slack_app_manifest(agent_name=app_name)
        async with connection_released(self.uow.session):
            created = await self.slack.create_app(access_token, manifest)
        # Install names are unique per org; the app id makes this one so
        # however many times a teammate's app is made again.
        install_name = f"{manifest['display_information']['name']} ({created.app_id})"
        install_id: UUID | None = None
        try:
            install_id = await self.installs.create_install(
                self.uow,
                user_id=request.user_id,
                organization_id=request.organization_id,
                connector_id=SLACK_CONNECTOR_ID,
                name=install_name,
                config={
                    "client_id": created.client_id,
                    "client_secret": created.client_secret,
                    "signing_secret": created.signing_secret,
                },
                # Slack handed these to Lemma; no person chose the secret.
                made_by_lemma=True,
            )
            row_id = await self.pending.add(
                organization_id=request.organization_id,
                pod_id=request.pod_id,
                agent_id=request.agent_id,
                auth_config_id=install_id,
                team_id=request.team_id,
                app_id=created.app_id,
                requested_by_user_id=request.user_id,
                source=request.source,
                slack_user_id=request.slack_user_id,
            )
            await self.uow.commit()
        except BaseException:
            # The install committed inside the connectors module, so a rollback
            # would undo nothing: undo it by hand, and the app with it, even
            # when what interrupted us was a cancellation.
            await _finished(
                self._undo(
                    request,
                    created=created,
                    install_id=install_id,
                    install_name=install_name,
                    access_token=access_token,
                    reason="failed",
                )
            )
            raise
        if row_id is None:
            # Another request for the same agent and workspace got there first
            # (only possible without Redis). Theirs is the app; ours goes.
            await self._undo(
                request,
                created=created,
                install_id=install_id,
                install_name=install_name,
                access_token=access_token,
                reason="duplicate",
            )
            winner = await self.pending.for_agent(request.agent_id, request.team_id)
            if winner is None:
                raise SlackAppCreationBusyError(request.team_id, creating=True)
            return _reused(winner)
        logger.info(
            "agent_surfaces.slack_app_creation.created",
            organization_id=str(request.organization_id),
            team_id=request.team_id,
            app_id=created.app_id,
        )
        return ProvisionedSlackApp(app_id=created.app_id, auth_config_id=install_id)

    async def _undo(
        self,
        request: _Request,
        *,
        created: slack.CreatedSlackApp,
        install_id: UUID | None,
        install_name: str,
        access_token: SecretStr,
        reason: str,
    ) -> None:
        """Delete what a create made and could not keep: the install, then the app."""
        install_deleted = False
        try:
            await self.uow.rollback()
            install_deleted = await self.installs.delete_unauthorised(
                self.uow,
                organization_id=request.organization_id,
                auth_config_id=install_id,
                name=None if install_id is not None else install_name,
            )
            await self.uow.commit()
        except SQLAlchemyError:
            logger.warning(
                "agent_surfaces.slack_app_creation.undo_install.degraded",
                organization_id=str(request.organization_id),
                app_id=created.app_id,
                exc_info=True,
            )
        async with connection_released(self.uow.session):
            app_deleted = await self.slack.delete_app(access_token, created.app_id)
        logger.warning(
            "agent_surfaces.slack_app_creation.undone",
            organization_id=str(request.organization_id),
            team_id=request.team_id,
            app_id=created.app_id,
            reason=reason,
            app_deleted=app_deleted,
            install_deleted=install_deleted,
        )

    async def fresh_access_token(
        self, organization_id: UUID, team_id: str
    ) -> SecretStr:
        """An access token good for the next few minutes, rotating it first if
        it is not -- by this caller, or by whoever already holds the lease."""
        for attempt in itertools.count():
            row = await self.tokens.get(organization_id, team_id)
            if row is None:
                raise SlackAppCreationNotSetUpError(team_id)
            if row.refused_at is not None:
                raise SlackAppCreationTokenExpiredError(team_id)
            pair = reveal_tokens(row)
            left = row.access_expires_at - datetime.now(UTC)
            if left > _ROTATE_WITHIN:
                return pair.access_token
            claimed = await self.tokens.claim_rotation(
                row.id, generation=row.generation, lease=ROTATION_LEASE
            )
            await self.uow.commit()
            if claimed:
                return await self._rotate(row, pair.refresh_token)
            if left > _STILL_USABLE:
                return pair.access_token
            if attempt >= _LEASE_WAITS:
                raise SlackAppCreationBusyError(team_id)
            async with connection_released(self.uow.session):
                await asyncio.sleep(self.lease_wait_seconds)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _rotate(
        self, row: SlackConfigurationTokenModel, refresh_token: SecretStr
    ) -> SecretStr:
        row_id, generation, team_id = row.id, row.generation, row.team_id
        try:
            async with connection_released(self.uow.session):
                rotated = await self.slack.rotate(refresh_token)
        except SlackConfigurationTokenRefusedError as refused:
            await self.tokens.mark_refused(row_id, generation=generation)
            await self.uow.commit()
            logger.warning(
                "agent_surfaces.slack_app_creation.token_refused",
                organization_id=str(row.organization_id),
                team_id=team_id,
                slack_error=refused.details["slack_error"],
            )
            raise SlackAppCreationTokenExpiredError(team_id) from None
        except BaseException:
            await _finished(self._release_rotation(row_id, generation))
            raise
        # Slack has spent the old refresh token now: a cancellation before the
        # new pair is committed would lose it, so the store is shielded.
        stored = await _finished(self._store_rotation(row_id, generation, rotated))
        if not stored:
            # A person pasted a new token while this rotated the old one; theirs
            # is kept, and this access token is still good to use once.
            logger.info(
                "agent_surfaces.slack_app_creation.rotation_superseded",
                team_id=team_id,
            )
        return rotated.access_token

    async def _store_rotation(
        self, row_id: UUID, generation: int, rotated: slack.RotatedConfigurationToken
    ) -> bool:
        stored = await self.tokens.store_rotation(
            row_id, generation=generation, token=rotated
        )
        await self.uow.commit()
        return stored

    async def _release_rotation(self, row_id: UUID, generation: int) -> None:
        try:
            await self.uow.rollback()
            await self.tokens.release_rotation(row_id, generation=generation)
            await self.uow.commit()
        except SQLAlchemyError:
            # The lease runs out on its own; until then, callers wait for it.
            logger.warning(
                "agent_surfaces.slack_app_creation.rotation_release.degraded",
                exc_info=True,
            )

    async def _app_name(self, pod_id: UUID, agent_id: UUID) -> str:
        """What the app and its bot are called: the pod's name for the pod's own
        agent, as the app shows it, and the agent's name otherwise."""
        if is_pod_default_agent(agent_id, pod_id=pod_id):
            name = await pod_name(self.uow.session, pod_id)
        else:
            name = await agent_name_in_pod(self.uow, pod_id=pod_id, agent_id=agent_id)
        return humanize_name(name) or "Lemma"

    async def _may_manage(self, organization_id: UUID, user_id: UUID) -> bool:
        return await self.installs.may_manage(
            self.uow, user_id=user_id, organization_id=organization_id
        )

    async def _require_install_manager(
        self, organization_id: UUID, user_id: UUID
    ) -> None:
        if not await self._may_manage(organization_id, user_id):
            raise SlackAppCreationForbiddenError()
