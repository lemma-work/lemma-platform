"""Lemma's own Slack app's work: what it was told, Allows coming back, what it owes.

* ``surface.slack_manager`` -- one thing the manager was told, written by the
  webhook request that verified it (ids only; ``services/slack_manager``).
* ``surface.slack_app_allowed`` -- subscribed to connectors'
  ``ConnectorAccountConnected``, for a Slack grant the manager's "Add to
  Slack" modal started (its connect request's purpose): the teammate's
  surface is made from the new account. Keyed by the account, so a fact
  emitted twice runs once while queued, and the handler is idempotent after.
* ``surface.slack_workspace_installed`` -- subscribed to the same fact, for a
  grant of Lemma's own app that "Add to Slack" started (its purpose) and that
  came back to its starter's own browser (``browser_bound``): the workspace it
  names gets Lemma's app as its manager (``services/slack_install``). Keyed
  by the account; one organization's adoptions run one at a time, and the
  binding is idempotent, so a renewed grant refreshes and welcomes no one.
* ``surface.slack_manager_message`` -- a DM the manager owes a person, staged
  in the transaction that made it owed and keyed by its cause.
* ``surface.slack_teammate_hello`` -- a teammate's first word in a channel the
  manager handed it, staged once the invite succeeded.

All JOBs: each calls Slack.
"""

from __future__ import annotations

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import (
    JobContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent_surfaces.domain.slack_manager import (
    SLACK_TEAMMATE_APP_PURPOSE,
    ManagerInput,
)
from app.modules.agent_surfaces.domain.work_payloads import (
    SlackManagerMessagePayload,
    SlackTeammateHelloPayload,
)
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_SLACK_APP_ALLOWED,
    SURFACE_SLACK_MANAGER,
    SURFACE_SLACK_MANAGER_MESSAGE,
    SURFACE_SLACK_TEAMMATE_HELLO,
    SURFACE_SLACK_WORKSPACE_INSTALLED,
)
from app.modules.connectors.contracts.facts import ConnectorAccountConnected

#: One person's manager items in one workspace run one at a time.
_ONE_AT_A_TIME = 1


def _uow():
    return SessionUnitOfWorkFactory(async_session_maker)()


async def run_manager_input(ctx: JobContext, payload: ManagerInput) -> None:
    from app.modules.agent_surfaces.services.slack_manager import run

    async with _uow() as uow:
        await run(uow, payload)


async def finish_allowed_app(
    ctx: JobContext, payload: ConnectorAccountConnected
) -> None:
    from app.modules.agent_surfaces.services.slack_manager_bots import (
        bind_allowed_app,
    )

    async with _uow() as uow:
        await bind_allowed_app(uow, payload)


async def adopt_installed_workspace(
    ctx: JobContext, payload: ConnectorAccountConnected
) -> None:
    from app.modules.agent_surfaces.services.slack_install import adopt_workspace

    async with _uow() as uow:
        await adopt_workspace(uow, payload)


async def send_manager_message(
    ctx: JobContext, payload: SlackManagerMessagePayload
) -> None:
    from app.modules.agent_surfaces.services import slack_manager_messages

    async with _uow() as uow:
        await slack_manager_messages.send_manager_message(uow, payload)


async def say_teammate_hello(
    ctx: JobContext, payload: SlackTeammateHelloPayload
) -> None:
    from app.modules.agent_surfaces.services import slack_manager_messages

    async with _uow() as uow:
        await slack_manager_messages.say_teammate_hello(uow, payload)


def register_slack_manager_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACE_SLACK_MANAGER,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=ManagerInput,
        retry=RetryPolicy(max_attempts=3),
        concurrency=_ONE_AT_A_TIME,
    )(run_manager_input)
    registry.handler(
        kind=SURFACE_SLACK_APP_ALLOWED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=ConnectorAccountConnected,
        retry=RetryPolicy(max_attempts=5),
    )(finish_allowed_app)
    registry.handler(
        kind=SURFACE_SLACK_WORKSPACE_INSTALLED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=ConnectorAccountConnected,
        retry=RetryPolicy(max_attempts=5),
        concurrency=_ONE_AT_A_TIME,
    )(adopt_installed_workspace)
    registry.handler(
        kind=SURFACE_SLACK_MANAGER_MESSAGE,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=SlackManagerMessagePayload,
        retry=RetryPolicy(max_attempts=5),
    )(send_manager_message)
    registry.handler(
        kind=SURFACE_SLACK_TEAMMATE_HELLO,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=SlackTeammateHelloPayload,
        retry=RetryPolicy(max_attempts=3),
    )(say_teammate_hello)


def allowed_from_slack(fact: ConnectorAccountConnected) -> bool:
    """A Slack grant the manager's modal started. An ordinary Slack connect --
    no purpose, or another flow's -- is never one."""
    return fact.connector_id == "slack" and fact.purpose == SLACK_TEAMMATE_APP_PURPOSE


def installed_from_add_to_slack(fact: ConnectorAccountConnected) -> bool:
    """See ``slack_install.adopts_workspace``; imported there lazily, so the
    worker's every other subscriber does not pay for the install flow."""
    from app.modules.agent_surfaces.services.slack_install import adopts_workspace

    return adopts_workspace(fact)


register_slack_manager_work(default_registry)
facts.subscribe(
    ConnectorAccountConnected,
    kind=SURFACE_SLACK_APP_ALLOWED,
    cls=WorkClass.EVENTS,
    when=allowed_from_slack,
    dedup=lambda fact: f"slack-app-allowed:{fact.account_id}",
)
facts.subscribe(
    ConnectorAccountConnected,
    kind=SURFACE_SLACK_WORKSPACE_INSTALLED,
    cls=WorkClass.EVENTS,
    when=installed_from_add_to_slack,
    dedup=lambda fact: f"slack-workspace-installed:{fact.account_id}",
    concurrency_key=lambda fact: f"slack-workspace-install:{fact.organization_id}",
)
