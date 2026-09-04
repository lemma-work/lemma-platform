"""Subscribing to a connector's events, as a module that schedules on them uses it.

Four operations and one value object, in place of the eighteen internals the
composition root reached for to provision a single schedule. Those eighteen --
five repositories, three adapters, the Composio SDK client and `ConnectorService`
itself -- existed to answer three questions, and the reason there were so many
is that the questions were being assembled from parts rather than asked:

* `resolve_trigger_binding` is the whole of what used to be `_resolve_manager`:
  four reads (trigger, account, auth config, connector) and one derivation
  (which install backs the account), collapsed into the one thing the caller
  wanted -- *can this account carry this trigger, and with what*. Getting there
  by hand meant building `ConnectorService` with its eight collaborators, then
  reaching through it to `service.auth_config_repository` and calling
  `service._resolve_auth_install`, a private method, from another module.
* `create_trigger_subscription` / `delete_trigger_subscription` are the remote
  half, which only Composio has.
* `verify_webhook` is what an inbound delivery has to pass.

The binding carries values, not entities: an account and a trigger row have
thirty fields between them and the caller reads five. `subscribable` is the one
that used to be a `ManagersFactory` returning an object whose only job was to
be non-`None`.

A submodule for the same reason as `provisioning.py` beside it: importing this
pulls the model layer, and `contracts/__init__` is imported by anything that
wants any contract at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.api.dependencies import (
    _trigger_repository,
    get_connector_service,
)
from app.modules.connectors.domain.errors import (
    ConnectorInfrastructureError,
    ConnectorTriggerNotFoundError,
    ConnectorValidationError,
)
from app.modules.connectors.infrastructure.composio_triggers import (
    create_trigger_subscription,
    delete_trigger_subscription,
    supports_provider_subscription,
    verify_webhook,
)


@dataclass(frozen=True, slots=True)
class TriggerBinding:
    """What a caller needs to route or subscribe to one trigger on one account."""

    connector_id: str
    event_type: str
    #: Every default the trigger's `config_schema` declares. Which of them
    #: actually apply is the caller's decision, because only the caller knows
    #: what its author already wrote.
    config_defaults: dict[str, object] = field(default_factory=dict)
    #: The provider-side installation this account came back from -- GitHub's
    #: App installation id. Not something a person could type: it arrives on
    #: the OAuth redirect, and guessing it routes another organization's events.
    installation_id: str | None = None
    #: The provider's handle for the connected account, needed to subscribe.
    connection_id: str | None = None
    #: Whether a remote subscription can be created at all. When false the
    #: trigger is delivered by an installation-wide webhook and the caller
    #: routes on `installation_id` instead.
    subscribable: bool = False


async def resolve_trigger_binding(
    uow: SqlAlchemyUnitOfWork, *, trigger_id: str, account_id: UUID, user_id: UUID
) -> TriggerBinding:
    """Bind one trigger to one of this user's accounts.

    Raises rather than returning `None` for each of the three ways the pair can
    be wrong -- unknown trigger, an account for a different connector, an
    account whose auth config has gone -- because a caller that stored the
    resource anyway would have written a row that can never fire.
    """
    trigger = await _trigger_repository(uow).get(trigger_id)
    if trigger is None:
        raise ConnectorTriggerNotFoundError(trigger_id)

    service = get_connector_service(uow)
    account = await service.get_account(account_id, user_id)
    if account.connector_id != trigger.connector_id:
        raise ConnectorValidationError("Account does not match trigger connector")
    auth_config = await service.auth_config_repository.get(account.auth_config_id)
    if auth_config is None:
        raise ConnectorValidationError("Account auth configuration not found")

    connector = await service.get_connector(account.connector_id)
    provider = getattr(auth_config.provider, "value", str(auth_config.provider))
    return TriggerBinding(
        connector_id=trigger.connector_id,
        event_type=trigger.event_type,
        config_defaults=_declared_defaults(trigger.config_schema),
        installation_id=str(account.external_ref) if account.external_ref else None,
        connection_id=getattr(account.credentials, "connection_id", None),
        subscribable=supports_provider_subscription(
            provider, service._resolve_auth_install(connector, auth_config)
        ),
    )


def _declared_defaults(config_schema: dict[str, object] | None) -> dict[str, object]:
    """The `default` each property of a trigger's config schema declares.

    Without this a `default` in `config_schema` is decoration: the form
    prefills it, and a schedule created through the API or the CLI with an
    empty config gets nothing. That difference is not academic -- GitHub's
    `workflow_run` defaults to completed runs because a busy repository emits
    one delivery per run per state change, so the API path would wake an agent
    three times for one CI run while the UI path woke it once.
    """
    properties = (config_schema or {}).get("properties") or {}
    if not isinstance(properties, dict):
        return {}
    return {
        name: spec["default"]
        for name, spec in properties.items()
        if isinstance(spec, dict) and "default" in spec
    }


__all__ = [
    "ConnectorInfrastructureError",
    "ConnectorTriggerNotFoundError",
    "ConnectorValidationError",
    "TriggerBinding",
    "create_trigger_subscription",
    "delete_trigger_subscription",
    "resolve_trigger_binding",
    "verify_webhook",
]
