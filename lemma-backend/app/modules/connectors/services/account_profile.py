"""Fetching a connected account's own profile, for display purposes.

Populates the email and display name shown against an account, by calling the
provider's own "who am I" operation. Split out of the connector service because
it is a self-contained concern that only reads.
"""

from __future__ import annotations

from contextlib import suppress
from app.core.concurrency.offload import run_blocking
from app.modules.connectors.domain.account import OAuthCredentials
from app.modules.connectors.domain.connector import AuthProvider, ConnectorEntity
from app.modules.connectors.infrastructure.adapters.lemma_connector_factory import (
    create_lemma_execution_client,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


def _extract_nested_value(source: dict, key: str):
    """Find `key` anywhere in a nested provider profile response."""
    if not isinstance(source, dict):
        return None
    if key in source:
        return source[key]
    for value in source.values():
        if isinstance(value, dict):
            found = _extract_nested_value(value, key)
            if found is not None:
                return found
    return None


async def load_native_account_profile(
    connector: ConnectorEntity,
    credentials: OAuthCredentials,
) -> dict | None:
    try:
        connector.capability_for(AuthProvider.LEMMA)
    except ValueError:
        return None

    # Slack and Gmail are not here any more: they install as `http` and their
    # profile operation is curated in `connector_profile_operations.json`, so
    # `_fetch_account_profile` reaches them through the same catalog route as
    # every other kind.
    profile_operation_by_app = {
        "google_drive": ("about_get", {}),
    }
    config = profile_operation_by_app.get(connector.id)
    if not config:
        return None

    operation_name, payload = config
    with suppress(Exception):
        client = await run_blocking(
            create_lemma_execution_client,
            connector,
            credentials.model_dump(exclude_none=True),
            limiter="cpu_bound",
        )
        profile = await client.execute_operation(operation_name, payload)
        profile_dict = profile_to_dict(profile)
        if profile_dict is not None:
            return profile_dict
    return None


def profile_to_dict(profile: object) -> dict | None:
    if isinstance(profile, dict):
        return profile
    if hasattr(profile, "model_dump"):
        data = profile.model_dump(exclude_none=True, exclude_unset=True, mode="json")
        return data if isinstance(data, dict) else None
    return None
