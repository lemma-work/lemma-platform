"""Provisioning a WEBHOOK schedule's subscription, behind this module's own port.

Here rather than in the composition root because `ExternalScheduleWriter` is a
schedule port, the two errors it raises are schedule errors, and the rule about
which declared defaults survive is a schedule policy. What it needed from
`connectors` -- a trigger bound to an account, and the two calls that create and
drop a remote subscription -- is four operations on `connectors/contracts/triggers`.

A schedule names a pod connector, never an account. The account a subscription
is made on is what that connector resolves to *now*, for the schedule's owner
(`resolve_standing_pod_connector`): the owner's own for a per-member connector,
the shared one for a shared connector. The pod connector's typed refusals
(CONNECTION_REQUIRED, CONNECTOR_NEEDS_ACCOUNT, ...) pass through untranslated:
they name their own remedy, which a schedule error would lose.

An APP connector (the organization's GitHub App) resolves to no account. Its
GitHub trigger is bound to one of the App's installations instead, picked by
the GitHub account the schedule's config names (`owner`, `org`, or the owner
half of `repository` / `full_name`), or the only one there is.

The connector errors are translated rather than allowed through. A caller
creating a schedule gets told what is wrong with *its* schedule; a
`CONNECTOR_VALIDATION_ERROR` surfacing from a `POST /schedules` says the fault
is somewhere the caller did not ask about.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.contracts.app_connectors import (
    PodConnectorApp,
    pick_app_installation,
    requested_owner,
)
from app.modules.connectors.contracts.triggers import (
    ConnectorInfrastructureError,
    ConnectorTriggerNotFoundError,
    ConnectorValidationError,
    TriggerBinding,
    create_trigger_subscription,
    delete_trigger_subscription,
    resolve_app_trigger_binding,
    resolve_trigger_binding,
)
from app.modules.connectors.contracts.mcp_events import (
    ConnectorDomainError,
    subscribe_to_mcp_event,
    unsubscribe_from_mcp_event,
)
from app.modules.connectors.contracts.pod_connectors import PodConnectorAccount
from app.modules.schedule.domain.errors import (
    ScheduleInfrastructureError,
    ScheduleValidationError,
)
from app.modules.schedule.domain.interfaces import (
    ExternalScheduleWriter,
    ProvisionedTrigger,
    ScheduleConfig,
)
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.services.schedule_connector_policy import resolve_as_owner

#: The three connector operations this adapter drives, as injectable seams.
#: Named so a test can stand a binding up without a database, rather than
#: patching a name inside the module it is testing.
BindingResolver = Callable[..., Awaitable[TriggerBinding]]
TriggerSubscriber = Callable[..., Awaitable[str]]
TriggerUnsubscriber = Callable[..., Awaitable[None]]
#: Which organization owns a pod, read in a session of its own: provisioning
#: runs with the request's connection handed back.
PodOrganization = Callable[[UUID], Awaitable[UUID | None]]
#: The account a schedule's pod connector means right now, for its owner --
#: or the App, for an APP connector.
ConnectorResolver = Callable[
    [SqlAlchemyUnitOfWork, ScheduleEntity],
    Awaitable[PodConnectorAccount | PodConnectorApp],
]
#: Binds a trigger to one of the App's installations.
AppBindingResolver = Callable[..., Awaitable[TriggerBinding]]


class PodHearsChannel(Protocol):
    """Whether a pod's bot hears a Slack channel: somebody connected it."""

    async def __call__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        pod_id: UUID,
        team_id: str,
        channel_id: str,
    ) -> bool: ...


async def _pod_hears_slack_channel(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, team_id: str, channel_id: str
) -> bool:
    from app.modules.agent_surfaces.contracts.slack_channels import (
        pod_hears_slack_channel,
    )

    return await pod_hears_slack_channel(
        uow.session, pod_id=pod_id, team_id=team_id, channel_id=channel_id
    )


async def _pod_organization(pod_id: UUID) -> UUID | None:
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.pod.contracts.members import pod_organization_id

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        return await pod_organization_id(uow, pod_id)


def _github_binding(
    binding: TriggerBinding, config: ScheduleConfig | None = None
) -> ScheduleConfig:
    """The routing key for a GitHub trigger, taken from what is already known.

    Nothing here is something a person could sensibly type into a form. The
    installation id lives on the account -- it is what the App install
    redirected back with -- and the event is the trigger they picked. Asking for
    either would be asking someone to copy a number out of a URL, and getting it
    wrong routes another organization's events at their pod.
    """
    del config  # the repository and actions an author scoped are refined on
    if not binding.installation_id:
        from app.modules.connectors.contracts.github import github_install_url

        where = github_install_url()
        raise ScheduleValidationError(
            "This GitHub account is not bound to an App installation, so there "
            "is nothing to route events from. "
            + (
                f"Install the app at {where}, then reconnect the account."
                if where
                else "Install the app on the organization, then reconnect it."
            )
        )
    return {
        "source": "github",
        "installation_id": binding.installation_id,
        "event": binding.event_type,
    }


#: Slack's trigger events (the catalog's `event_type`), and what each needs
#: its author to say. Every one is matched on the workspace and channel; a
#: thread reply on the thread as well.
_SLACK_TRIGGER_KEYS: dict[str, tuple[str, ...]] = {
    "message": ("team_id", "channel_id"),
    "message.thread": ("team_id", "channel_id", "thread_ts"),
    "app_mention": ("team_id", "channel_id"),
}


def _slack_binding(
    binding: TriggerBinding, config: ScheduleConfig | None = None
) -> ScheduleConfig:
    """The routing key for a Slack trigger: the event, and where it happens.

    A Slack app has one event URL, and it is the pod's bot's: messages are
    heard there, and fire schedules once they are logged in the channel they
    were said in (`schedule.contracts.heard_events`). There is nothing to
    subscribe to, so the key is all provisioning writes.

    The workspace and channel are the author's to name -- which channel to
    watch is the whole of what they are choosing -- and they are not what keeps
    one tenant out of another's: a delivery fires only in pods that connected
    that channel to their bot, whatever any config says. They are checked
    here because the catalog marks them required and nothing else reads the
    trigger's schema: a schedule missing one would be stored and never fire.
    """
    present = config or {}
    required = _SLACK_TRIGGER_KEYS.get(binding.event_type)
    if required is None:
        raise ScheduleValidationError(
            f"Slack has no trigger for '{binding.event_type}' that can fire."
        )
    named = {key: str(present.get(key) or "").strip() for key in required}
    missing = [key for key, value in named.items() if not value]
    if missing:
        raise ScheduleValidationError(
            "This Slack trigger fires on one channel, so its config names it: "
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} "
            "missing. Slack ids, not names: the workspace's T..., the "
            "channel's C..., a thread's ts."
        )
    return {"source": "slack", "event": binding.event_type, **named}


# Connectors whose triggers need no remote subscription, only a routing key.
# Absence from both this table and `TriggerBinding.subscribable` is an error,
# not a shrug.
_LOCAL_BINDERS: dict[
    str, Callable[[TriggerBinding, ScheduleConfig | None], ScheduleConfig]
] = {
    "github": _github_binding,
    "slack": _slack_binding,
}


def defaults_the_author_left_out(
    binding: TriggerBinding, config: ScheduleConfig | None
) -> ScheduleConfig:
    """The trigger's declared defaults, minus every key the author already set.

    Only absent keys are filled. An author who wrote `actions: []` meant it,
    and `bound_config` is merged over the schedule's config, so a default
    returned here for a key they set would overwrite their decision.
    """
    present = config or {}
    return {
        name: value
        for name, value in binding.config_defaults.items()
        if name not in present
    }


class ExternalScheduleWriterAdapter(ExternalScheduleWriter):
    """Provision provider triggers behind the schedule-owned writer port."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        resolve_binding: BindingResolver = resolve_trigger_binding,
        subscribe: TriggerSubscriber = create_trigger_subscription,
        unsubscribe: TriggerUnsubscriber = delete_trigger_subscription,
        listen_to_mcp: TriggerSubscriber = subscribe_to_mcp_event,
        stop_listening_to_mcp: TriggerUnsubscriber = unsubscribe_from_mcp_event,
        pod_organization: PodOrganization = _pod_organization,
        resolve_connector: ConnectorResolver = resolve_as_owner,
        pod_hears_channel: PodHearsChannel = _pod_hears_slack_channel,
        resolve_app_binding: AppBindingResolver = resolve_app_trigger_binding,
    ) -> None:
        self.uow = uow
        self._resolve_app_binding = resolve_app_binding
        self._pod_hears_channel = pod_hears_channel
        self._resolve_connector = resolve_connector
        self._resolve_binding = resolve_binding
        self._subscribe = subscribe
        self._unsubscribe = unsubscribe
        self._listen_to_mcp = listen_to_mcp
        self._stop_listening_to_mcp = stop_listening_to_mcp
        self._pod_organization = pod_organization

    async def _binding(self, schedule: ScheduleEntity) -> TriggerBinding | None:
        """This schedule's trigger, or ``None`` when it names none.

        A schedule with no connector trigger is routed by whatever its author
        put in `config`, and there is nothing to provision on anyone's behalf.
        """
        if not schedule.connector_trigger_id or not schedule.pod_connector_slug:
            return None
        resolved = await self._resolve_connector(self.uow, schedule)
        try:
            if isinstance(resolved, PodConnectorApp):
                return await self._resolve_app_binding(
                    self.uow,
                    trigger_id=schedule.connector_trigger_id,
                    connector_id=resolved.connector_id,
                    installation_id=pick_app_installation(
                        resolved, owner=requested_owner(schedule.config)
                    ),
                )
            return await self._resolve_binding(
                self.uow,
                trigger_id=schedule.connector_trigger_id,
                account_id=resolved.account_id,
                user_id=resolved.owner_user_id,
            )
        except (ConnectorValidationError, ConnectorTriggerNotFoundError) as exc:
            raise ScheduleValidationError(str(exc)) from exc

    async def create_provider_trigger(
        self, schedule: ScheduleEntity
    ) -> ProvisionedTrigger:
        if schedule.schedule_type is not ScheduleType.WEBHOOK:
            return ProvisionedTrigger()
        if schedule.listens_to_mcp:
            return await self._provision_mcp_event(schedule)
        binding = await self._binding(schedule)
        if binding is None:
            return ProvisionedTrigger()
        if not binding.subscribable:
            bound_config = _local_routing_key(binding, schedule)
            await self._require_a_heard_channel(schedule, bound_config)
            return ProvisionedTrigger(bound_config=bound_config)
        if not binding.connection_id:
            raise ScheduleValidationError("Connector account is not active")
        try:
            provider_id = await self._subscribe(
                slug=binding.event_type,
                connection_id=binding.connection_id,
                config=schedule.config,
            )
        except ConnectorInfrastructureError as exc:
            raise ScheduleInfrastructureError(str(exc)) from exc
        return ProvisionedTrigger(provider_trigger_id=provider_id)

    async def _require_a_heard_channel(
        self, schedule: ScheduleEntity, bound_config: ScheduleConfig
    ) -> None:
        """A Slack trigger fires only on a channel the pod's bot hears
        (`agent_surfaces.services.channel_triggers`). On any other it would be
        stored and never fire, with no run, no skip and no log to tell its
        author, so it is refused here, where they are still asking."""
        if bound_config.get("source") != "slack" or schedule.pod_id is None:
            return
        channel_id = str(bound_config.get("channel_id") or "")
        if await self._pod_hears_channel(
            self.uow,
            pod_id=schedule.pod_id,
            team_id=str(bound_config.get("team_id") or ""),
            channel_id=channel_id,
        ):
            return
        raise ScheduleValidationError(
            f"This pod's Slack bot is not in channel {channel_id}, so nothing "
            "said there reaches it and the schedule would never fire. Connect "
            "the channel to the pod's bot first, then create the schedule."
        )

    async def delete_provider_trigger(self, schedule: ScheduleEntity) -> None:
        if schedule.schedule_type is not ScheduleType.WEBHOOK:
            return
        provider_id = schedule.config.get("provider_trigger_id")
        if not provider_id:
            return
        if schedule.listens_to_mcp:
            await self._stop_listening_to_mcp(str(provider_id))
            return
        # Only a remote subscription ever stored a provider id -- a local
        # routing key is written under its own keys -- and dropping one needs
        # nothing but its id. Resolving the connector first would leave the
        # subscription running at the provider whenever the connector no
        # longer resolves, which is exactly when it most needs to stop.
        try:
            await self._unsubscribe(str(provider_id))
        except ConnectorInfrastructureError as exc:
            raise ScheduleInfrastructureError(str(exc)) from exc

    async def _provision_mcp_event(
        self, schedule: ScheduleEntity
    ) -> ProvisionedTrigger:
        """Subscribe, on the account the schedule's pod connector resolves to,
        to the event the config names.

        Our subscription id is the routing key its deliveries carry, stored
        where a Composio trigger keeps its own, so the edit and delete paths
        need nothing new.
        """
        event = schedule.config.get("event")
        arguments = schedule.config.get("arguments") or {}
        if not isinstance(event, str) or not event.strip():
            raise ScheduleValidationError(
                "An MCP event schedule names its event in config.event."
            )
        if not isinstance(arguments, dict):
            raise ScheduleValidationError("config.arguments must be an object.")
        organization_id = (
            await self._pod_organization(schedule.pod_id) if schedule.pod_id else None
        )
        if schedule.pod_id and organization_id is None:
            raise ScheduleValidationError("This schedule's pod no longer exists.")
        resolved = await self._resolve_connector(self.uow, schedule)
        if isinstance(resolved, PodConnectorApp):
            raise ScheduleValidationError(
                f"'{resolved.slug}' acts as the organization's GitHub App; an MCP "
                "event is subscribed to on an account, and it has none."
            )
        try:
            provider_id = await self._listen_to_mcp(
                account_id=resolved.account_id,
                user_id=resolved.owner_user_id,
                event=event.strip(),
                arguments=arguments,
                organization_id=organization_id,
            )
        except ConnectorInfrastructureError as exc:
            raise ScheduleInfrastructureError(str(exc)) from exc
        except ConnectorDomainError as exc:
            raise ScheduleValidationError(str(exc)) from exc
        return ProvisionedTrigger(provider_trigger_id=provider_id)


def _local_routing_key(
    binding: TriggerBinding, schedule: ScheduleEntity
) -> ScheduleConfig:
    """What to store for a trigger nothing subscribes to.

    A connector with neither a remote subscription nor a binder here would
    otherwise be the case that used to return `None` and look like success: the
    schedule row would exist, nothing would be subscribed, and it could never
    fire. Slack's three triggers were inert for exactly that reason from the day
    they were added.
    """
    binder = _LOCAL_BINDERS.get(binding.connector_id)
    if binder is None:
        raise ScheduleValidationError(
            f"'{binding.connector_id}' triggers cannot be provisioned: "
            "no provider subscription can be created for them and no "
            "local routing key is defined, so the schedule would never "
            "fire."
        )
    return {
        **defaults_the_author_left_out(binding, schedule.config),
        **binder(binding, schedule.config),
    }
