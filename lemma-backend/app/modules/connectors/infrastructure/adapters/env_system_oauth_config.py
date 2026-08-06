from __future__ import annotations

import os

from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    ConnectorKind,
    AuthProvider,
    KindSpec,
    OAuth2Config,
    OAuth2Defaults,
    SystemOAuthCredentialRef,
)
from app.modules.connectors.domain.ports import SystemOAuthConfigPort

# --- Native Lemma-provider OAuth registry -----------------------------------
#
# Native apps (Gmail, Google Calendar, ...) authenticate through the Lemma
# provider's own OAuth2 flow rather than Composio. The OAuth endpoints + scopes
# are static constants for the provider, and the system OAuth client lives in
# env, so neither belongs in the database. They are resolved here at runtime:
# editing a scope is a code change, and system-default availability tracks env
# presence on every request instead of going stale at import time.
#
# Apps that declare their own oauth2_defaults/system_oauth on the stored
# capability (e.g. Slack/Jira via lemma_apps_config.json) take precedence over
# this registry; the registry is the fallback for native apps that store none.

_GOOGLE_OAUTH_ENDPOINTS: dict[str, object] = {
    "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url": "https://oauth2.googleapis.com/token",
    "userinfo_url": "https://www.googleapis.com/oauth2/v3/userinfo",
    "revoke_url": "https://oauth2.googleapis.com/revoke",
    # access_type=offline + prompt=consent make Google return a refresh token and
    # re-issue it on every re-consent.
    "extra_params": {"access_type": "offline", "prompt": "consent"},
}

# Identity scopes requested for every Google app so the account holder's email
# can be resolved from the userinfo endpoint.
_GOOGLE_IDENTITY_SCOPES: tuple[str, ...] = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
)

# The Google apps share one system OAuth client for connector connect flows.
# This is deliberately SEPARATE from the login OAuth client (SuperTokens user
# login, GOOGLE_CLIENT_ID in core config): a system-default connector client
# typically needs a different client id with broader, app-specific scopes
# (gmail.modify, calendar, ...). Prefer the connector-scoped env vars and fall
# back to the legacy shared GOOGLE_CLIENT_ID/SECRET for backward compatibility.
_GOOGLE_SYSTEM_OAUTH = SystemOAuthCredentialRef(
    client_id_env=["CONNECTOR_GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_ID"],
    client_secret_env=["CONNECTOR_GOOGLE_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET"],
)


def _google_oauth2_defaults(*api_scopes: str) -> OAuth2Defaults:
    return OAuth2Defaults(
        default_scopes=[*_GOOGLE_IDENTITY_SCOPES, *api_scopes],
        **_GOOGLE_OAUTH_ENDPOINTS,
    )


# OAuth2 endpoints/scopes for native Lemma-provider apps, keyed by connector id.
NATIVE_LEMMA_OAUTH2_DEFAULTS: dict[str, OAuth2Defaults] = {
    "gmail": _google_oauth2_defaults("https://www.googleapis.com/auth/gmail.modify"),
    "google_calendar": _google_oauth2_defaults(
        "https://www.googleapis.com/auth/calendar"
    ),
    "google_drive": _google_oauth2_defaults("https://www.googleapis.com/auth/drive"),
    "google_docs": _google_oauth2_defaults(
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/drive",
    ),
    "google_sheets": _google_oauth2_defaults(
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ),
}

# System OAuth client env refs for native Lemma-provider apps, keyed by app id.
NATIVE_LEMMA_SYSTEM_OAUTH: dict[str, SystemOAuthCredentialRef] = {
    connector_id: _GOOGLE_SYSTEM_OAUTH
    for connector_id in NATIVE_LEMMA_OAUTH2_DEFAULTS
}


class EnvSystemOAuthConfigAdapter(SystemOAuthConfigPort):
    """Resolve native Lemma OAuth config from code + env without storing it in DB.

    OAuth endpoints/scopes come from the stored capability when present, else the
    native registry above. The system OAuth client id/secret always come from env
    so availability reflects the live environment.
    """

    def _lemma_capability(
        self,
        connector: ConnectorEntity,
    ) -> KindSpec | None:
        # "LEMMA" means any kind we serve ourselves -- sql/mcp/http as well as
        # the vendored package -- matching ConnectorService._lemma_capability.
        # Narrowing to the package spec specifically silently dropped system
        # OAuth default availability for every non-package native kind (e.g.
        # an `http`-kind connector with its own `system_oauth`, like GitHub).
        try:
            capability = connector.capability_for(AuthProvider.LEMMA)
        except ValueError:
            return None
        return capability if capability.kind is not ConnectorKind.COMPOSIO else None

    def _first_env_value(self, names: list[str]) -> str | None:
        for name in names:
            value = os.getenv(name)
            if value:
                return value
        return None

    def resolve_oauth2_defaults(
        self,
        connector: ConnectorEntity,
    ) -> OAuth2Defaults | None:
        """OAuth endpoints/scopes for the app: stored capability, else registry."""
        capability = self._lemma_capability(connector)
        if capability is not None and capability.oauth2_defaults is not None:
            return capability.oauth2_defaults
        return NATIVE_LEMMA_OAUTH2_DEFAULTS.get(connector.id)

    def _resolve_system_oauth(
        self,
        connector: ConnectorEntity,
    ) -> SystemOAuthCredentialRef | None:
        capability = self._lemma_capability(connector)
        if capability is not None and capability.system_oauth is not None:
            return capability.system_oauth
        return NATIVE_LEMMA_SYSTEM_OAUTH.get(connector.id)

    def has_default_oauth_config(self, connector: ConnectorEntity) -> bool:
        system_oauth = self._resolve_system_oauth(connector)
        if system_oauth is None:
            return False
        return bool(
            self._first_env_value(system_oauth.client_id_env_names())
            and self._first_env_value(system_oauth.client_secret_env_names())
        )

    def get_default_oauth_config(
        self,
        connector: ConnectorEntity,
    ) -> OAuth2Config | None:
        oauth_metadata = self.resolve_oauth2_defaults(connector)
        system_oauth = self._resolve_system_oauth(connector)
        if oauth_metadata is None or system_oauth is None:
            return None
        client_id = self._first_env_value(system_oauth.client_id_env_names())
        client_secret = self._first_env_value(system_oauth.client_secret_env_names())
        if not client_id or not client_secret:
            return None
        return OAuth2Config(
            client_id=client_id,
            client_secret=client_secret,
            **oauth_metadata.model_dump(),
        )
