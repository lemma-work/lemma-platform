"""Fetching a connected account's own profile, for display purposes.

Populates the email and display name shown against an account, by calling the
provider's own "who am I" operation. Split out of the connector service because
it is a self-contained concern that only reads.
"""

from __future__ import annotations

from contextlib import suppress
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

    if connector.id == "slack":
        return await _load_slack_account_profile(connector, credentials)

    profile_operation_by_app = {
        "gmail": ("users_get_profile", {"user_id": "me"}),
        "google_drive": ("about_get", {}),
    }
    config = profile_operation_by_app.get(connector.id)
    if not config:
        return None

    operation_name, payload = config
    with suppress(Exception):
        client = create_lemma_execution_client(
            connector,
            credentials.model_dump(exclude_none=True),
        )
        profile = await client.execute_operation(operation_name, payload)
        profile_dict = profile_to_dict(profile)
        if profile_dict is not None:
            return profile_dict
    return None


async def _load_slack_account_profile(
    connector: ConnectorEntity,
    credentials: OAuthCredentials,
) -> dict | None:
    if not credentials.access_token:
        return None

    with suppress(Exception):
        client = create_lemma_execution_client(
            connector,
            credentials.model_dump(exclude_none=True),
        )
        auth_profile = profile_to_dict(
            await client.execute_operation(
                "auth_test",
                {"token": credentials.access_token},
            )
        )
        if not auth_profile:
            return None

        profile: dict = {"auth_test": auth_profile, **auth_profile}
        user_id = _extract_nested_value(auth_profile, "user_id")
        if user_id:
            try:
                user_info = profile_to_dict(
                    await client.execute_operation(
                        "users_info",
                        {"token": credentials.access_token, "user": user_id},
                    )
                )
                if user_info:
                    profile["user_info"] = user_info
            except Exception:
                logger.debug(
                    "connectors.connector_service.enrich_slack_user_profile_s.diagnostic",
                    user_id=user_id,
                )
        return profile
    return None


def profile_to_dict(profile: object) -> dict | None:
    if isinstance(profile, dict):
        return profile
    if hasattr(profile, "model_dump"):
        data = profile.model_dump(exclude_none=True, exclude_unset=True, mode="json")
        return data if isinstance(data, dict) else None
    return None
