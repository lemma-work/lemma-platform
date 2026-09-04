"""Composio's trigger subscriptions, and the webhooks they deliver through.

The only place the Composio SDK's trigger surface is touched. Everything here
is a blocking network call made from an event loop, so all of it goes through
`run_blocking` on the `external_http` limiter -- shared with every other
Composio call, so a burst of webhooks cannot starve the CPU pool that chunking
and zipping depend on.

Failures are narrowed to the SDK's two exception roots rather than caught
wholesale. `composio.exceptions.ComposioError` is what the SDK raises on its
own; `composio_client.ComposioError` is the root of its HTTP client's
`APIError` tree -- status, connection and timeout. Between them they cover what
a provider call can do, and anything outside them is a bug here rather than a
provider that said no.
"""

from __future__ import annotations

from functools import lru_cache

from composio import Composio
from composio.exceptions import ComposioError as ComposioSDKError
from composio_client import ComposioError as ComposioTransportError

from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.connectors.domain.errors import ConnectorInfrastructureError

logger = get_logger(__name__)


def supports_provider_subscription(
    auth_provider: str, install: ResolvedAuthInstall | None
) -> bool:
    """Whether a remote subscription can be created for this account at all.

    False is not a failure: a GitHub App has one webhook URL and its
    installation decides which repositories it covers, so there is nothing to
    subscribe to. The caller binds a routing key instead.
    """
    if install is not None and install.composio_toolkit_slug:
        return True
    return auth_provider == AuthProvider.COMPOSIO.value


def _client() -> Composio:
    # Shared, and imported here rather than at module scope. Constructing one
    # ran on every trigger create and delete, each paying 42-262ms of SDK
    # construction on the event loop; importing the SDK eagerly pulled 993
    # modules into every process that touches connectors at all.
    from app.modules.connectors.infrastructure.composio_client import (
        get_composio_client,
    )

    return get_composio_client()


async def create_trigger_subscription(
    *, slug: str, connection_id: str, config: dict[str, object]
) -> str:
    """Subscribe this connected account to one trigger, returning its id."""

    def _create():
        return _client().triggers.create(
            slug=slug,
            connected_account_id=connection_id,
            trigger_config=config or {},
        )

    try:
        response = await run_blocking(_create, limiter="external_http")
    except (ComposioSDKError, ComposioTransportError) as exc:
        logger.debug(
            "runtime.connectors.composio_trigger_creation.diagnostic",
            error_type=type(exc).__name__,
        )
        raise ConnectorInfrastructureError("Connector trigger creation failed") from exc
    return response.trigger_id


async def delete_trigger_subscription(subscription_id: str) -> None:
    """Drop a subscription this module created."""
    try:
        await run_blocking(
            _client().triggers.delete, subscription_id, limiter="external_http"
        )
    except (ComposioSDKError, ComposioTransportError) as exc:
        logger.debug(
            "runtime.connectors.composio_trigger_deletion.diagnostic",
            error_type=type(exc).__name__,
        )
        raise ConnectorInfrastructureError("Connector trigger deletion failed") from exc


@lru_cache(maxsize=1)
def _webhook_verification_client() -> Composio:
    """The SDK client used to verify inbound webhooks, built once.

    Construction is not free -- it reads config, builds an httpx client and
    imports the SDK's lazy namespaces -- and was measured at 76ms cold / 4ms
    warm at the neighbouring call site in ``composio_auth_provider``. Doing it
    per delivery put that on the event loop at a rate the sender picks.

    Cached as an object singleton, which is the sanctioned exception to
    "caching goes through Redis": this is a client handle, not data.
    """
    return Composio(
        api_key=connector_settings.composio_api_key or "webhook-verification"
    )


async def verify_webhook(payload: str, headers: dict[str, object]) -> dict[str, object]:
    """Check one inbound delivery's signature and return what it carried.

    Async because verification runs the provider's synchronous SDK on an
    unauthenticated path whose rate an external sender chooses. A sync
    implementation blocks the event loop once per delivery, so offloading is
    this function's obligation rather than its caller's option.

    No catch: a verifier that raises anything at all is a delivery that did not
    verify, and the webhook controller already says so once for every source
    rather than each one writing the same handler.
    """
    secret = connector_settings.composio_webhook_secret
    if not secret:
        raise ConnectorInfrastructureError(
            "Connector webhook verification is not configured"
        )

    def _verify() -> dict[str, object]:
        return _webhook_verification_client().triggers.verify_webhook(
            id=str(headers.get("webhook-id", "")),
            payload=payload,
            signature=str(headers.get("webhook-signature", "")),
            timestamp=str(headers.get("webhook-timestamp", "")),
            secret=secret,
        )

    return await run_blocking(_verify, limiter="external_http")


__all__ = [
    "create_trigger_subscription",
    "delete_trigger_subscription",
    "supports_provider_subscription",
    "verify_webhook",
]
