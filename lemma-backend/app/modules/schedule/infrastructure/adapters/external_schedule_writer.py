"""Provisioning a WEBHOOK schedule's subscription, behind this module's own port.

Here rather than in the composition root because `ExternalScheduleWriter` is a
schedule port, the two errors it raises are schedule errors, and the rule about
which declared defaults survive is a schedule policy. What it needed from
`connectors` -- a trigger bound to an account, and the two calls that create and
drop a remote subscription -- is four operations on `connectors/contracts/triggers`.

The connector errors are translated rather than allowed through. A caller
creating a schedule gets told what is wrong with *its* schedule; a
`CONNECTOR_VALIDATION_ERROR` surfacing from a `POST /schedules` says the fault
is somewhere the caller did not ask about.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.contracts.triggers import (
    ConnectorInfrastructureError,
    ConnectorTriggerNotFoundError,
    ConnectorValidationError,
    TriggerBinding,
    create_trigger_subscription,
    delete_trigger_subscription,
    resolve_trigger_binding,
)
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

#: The three connector operations this adapter drives, as injectable seams.
#: Named so a test can stand a binding up without a database, rather than
#: patching a name inside the module it is testing.
BindingResolver = Callable[..., Awaitable[TriggerBinding]]
TriggerSubscriber = Callable[..., Awaitable[str]]
TriggerUnsubscriber = Callable[..., Awaitable[None]]


def _github_binding(binding: TriggerBinding) -> ScheduleConfig:
    """The routing key for a GitHub trigger, taken from what is already known.

    Nothing here is something a person could sensibly type into a form. The
    installation id lives on the account -- it is what the App install
    redirected back with -- and the event is the trigger they picked. Asking for
    either would be asking someone to copy a number out of a URL, and getting it
    wrong routes another organization's events at their pod.
    """
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


# Connectors whose triggers need no remote subscription, only a routing key.
# Absence from both this table and `TriggerBinding.subscribable` is an error,
# not a shrug.
_LOCAL_BINDERS: dict[str, Callable[[TriggerBinding], ScheduleConfig]] = {
    "github": _github_binding,
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
    ) -> None:
        self.uow = uow
        self._resolve_binding = resolve_binding
        self._subscribe = subscribe
        self._unsubscribe = unsubscribe

    async def _binding(self, schedule: ScheduleEntity) -> TriggerBinding | None:
        """This schedule's trigger, or ``None`` when it names none.

        A schedule with no connector trigger is routed by whatever its author
        put in `config`, and there is nothing to provision on anyone's behalf.
        """
        if not schedule.connector_trigger_id or not schedule.account_id:
            return None
        try:
            return await self._resolve_binding(
                self.uow,
                trigger_id=schedule.connector_trigger_id,
                account_id=schedule.account_id,
                user_id=schedule.user_id,
            )
        except (ConnectorValidationError, ConnectorTriggerNotFoundError) as exc:
            raise ScheduleValidationError(str(exc)) from exc

    async def create_provider_trigger(
        self, schedule: ScheduleEntity
    ) -> ProvisionedTrigger:
        if schedule.schedule_type is not ScheduleType.WEBHOOK:
            return ProvisionedTrigger()
        binding = await self._binding(schedule)
        if binding is None:
            return ProvisionedTrigger()
        if not binding.subscribable:
            return ProvisionedTrigger(
                bound_config=_local_routing_key(binding, schedule)
            )
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

    async def delete_provider_trigger(self, schedule: ScheduleEntity) -> None:
        if schedule.schedule_type is not ScheduleType.WEBHOOK:
            return
        provider_id = schedule.config.get("provider_trigger_id")
        if not provider_id:
            return
        binding = await self._binding(schedule)
        if binding is None or not binding.subscribable:
            return
        try:
            await self._unsubscribe(str(provider_id))
        except ConnectorInfrastructureError as exc:
            raise ScheduleInfrastructureError(str(exc)) from exc


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
        **binder(binding),
    }
