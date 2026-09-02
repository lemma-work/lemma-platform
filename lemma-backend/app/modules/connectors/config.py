"""Connector module configuration (Composio + connector runtime).

Field names are unchanged from the former monolithic ``Settings`` so environment
variables resolve identically (``COMPOSIO_API_KEY``, ``CONNECTOR_ENCRYPTION_KEY``,
…). The ``schedule`` module also reads ``composio_*`` from here (an allowed
schedule→connector dependency).

System-default OAuth clients for native (Lemma-provider) connector connect
flows are resolved directly from the environment in
``infrastructure/adapters/env_system_oauth_config.py`` (env-presence drives
availability), kept SEPARATE from the login OAuth client in core config:
``CONNECTOR_GOOGLE_CLIENT_ID`` / ``CONNECTOR_GOOGLE_CLIENT_SECRET`` (a system
connector client typically needs different scopes than login), falling back to
the legacy shared ``GOOGLE_CLIENT_ID`` / ``GOOGLE_CLIENT_SECRET``.
"""

from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.settings_env import dotenv_path


class ConnectorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    connector_breaker_enabled: bool = Field(
        default=True,
        description=(
            "Stop calling a connector operation that has failed repeatedly, "
            "instead of making every caller wait for the same timeout. Env: "
            "``CONNECTOR_BREAKER_ENABLED``."
        ),
    )
    connector_breaker_failure_threshold: int = Field(
        default=5,
        ge=1,
        description=(
            "Consecutive infrastructure failures before an operation is "
            "disabled. Only 5xx/timeout failures count -- a rejected request or "
            "a stale credential is the caller's problem, not the provider's. "
            "Env: ``CONNECTOR_BREAKER_FAILURE_THRESHOLD``."
        ),
    )
    connector_breaker_cooldown_seconds: int = Field(
        default=60,
        ge=1,
        description=(
            "How long an operation stays disabled. The first call after this "
            "is a probe: one failure re-opens it. Env: "
            "``CONNECTOR_BREAKER_COOLDOWN_SECONDS``."
        ),
    )
    connector_breaker_failure_window_seconds: int = Field(
        default=120,
        ge=1,
        description=(
            "How long a failure counts toward the streak. Without a window, "
            "five failures spread over a week would trip a breaker on a "
            "provider that is fine. Env: "
            "``CONNECTOR_BREAKER_FAILURE_WINDOW_SECONDS``."
        ),
    )
    connector_composio_deadline_seconds: float = Field(
        default=90.0,
        ge=1,
        description=(
            "Last-resort ceiling on one Composio SDK call, for callers that do "
            "not route through the operation gateway -- surface email builds "
            "the Composio gateway directly, and that path had no bound at all. "
            "Deliberately equal to the dispatcher's Composio per-kind ceiling "
            "so it never pre-empts the tighter timeouts the routed path already "
            "applies. Env: ``CONNECTOR_COMPOSIO_DEADLINE_SECONDS``."
        ),
    )
    composio_api_key: Optional[str] = Field(
        default=None, description="Composio API key"
    )
    composio_webhook_secret: Optional[str] = Field(
        default=None, description="Composio webhook secret"
    )
    composio_sdk_telemetry_enabled: bool = Field(
        default=False,
        description=(
            "Allow the Composio SDK to send invocation telemetry to Composio. "
            "Disabled by default so connector names, failures, and execution "
            "metadata do not leave the deployment unexpectedly."
        ),
    )
    connector_operation_timeout_seconds: float = Field(
        default=45.0,
        description=(
            "Hard timeout for a single connector operation execution. Bounds "
            "calls to external providers so a hung/slow upstream fails fast "
            "instead of holding a DB connection (and wedging the event loop). "
            "Used for any kind without an entry in the per-kind overrides."
        ),
    )
    connector_discovery_timeout_seconds: float = Field(
        default=25.0,
        description=(
            "Hard timeout for one discovery round (MCP tool listing, OpenAPI "
            "spec fetch). Discovery runs inside the request that creates or "
            "refreshes an install, so without this an unresponsive server "
            "holds the request open until the ASGI worker gives up."
        ),
    )
    connector_spec_max_bytes: int = Field(
        default=8 * 1024 * 1024,
        description="Largest OpenAPI spec accepted from a tenant-supplied URL.",
    )
    connector_credential_refresh_skew_seconds: float = Field(
        default=120.0,
        description=(
            "How far before expiry a credential is proactively refreshed. Only "
            "credentials that report an expiry are refreshed at all; everything "
            "else relies on the reactive 401 path, which also catches "
            "server-side revocation."
        ),
    )
    connector_composio_managed_files_enabled: bool = Field(
        default=False,
        description=(
            "Let the Composio SDK handle files itself. Off, because the flag "
            "governs both directions and its download half writes the payload "
            "to this container's local disk and returns a path the caller "
            "cannot open. Kept as a switch so a deployment can fall back if a "
            "tool's upload shape turns out not to be covered."
        ),
    )
    connector_inline_result_max_bytes: int = Field(
        default=1024 * 1024,
        description=(
            "Largest binary result returned inline as base64. Anything bigger "
            "is streamed to the pod datastore and returned as a reference, so a "
            "large download never has to be held in memory (twice, once base64 "
            "encoded) or serialized into a JSON response."
        ),
    )
    connector_response_max_bytes: int = Field(
        default=64 * 1024 * 1024,
        description="Hard ceiling on a binary result; larger is refused, not buffered.",
    )
    connector_sql_engine_cache_size: int = Field(
        default=32,
        description=(
            "How many external SQL engines to keep pooled. Evicting disposes "
            "the engine, so this bounds open connections to customer databases."
        ),
    )
    connector_github_app_slug: Optional[str] = Field(
        default=None,
        description=(
            "The GitHub App's URL slug, as in github.com/apps/<slug>. Needed to "
            "send someone to install the App; the OAuth half works without it, "
            "but a user token can only reach repositories the App is installed "
            "on, so an uninstalled App authorizes fine and then sees nothing. "
            "Env: CONNECTOR_GITHUB_APP_SLUG."
        ),
    )
    connector_github_app_private_key: Optional[SecretStr] = Field(
        default=None,
        description=(
            "PEM for the GitHub App, used to mint short-lived installation "
            "tokens. Only needed to act as the app rather than as the person; "
            "the default is to act as the person. Accepts a literal PEM or one "
            "with escaped newlines. Env: CONNECTOR_GITHUB_APP_PRIVATE_KEY."
        ),
    )
    connector_github_app_private_key_path: Optional[str] = Field(
        default=None,
        description=(
            "Path to the .pem GitHub hands you, as an alternative to inlining "
            "it. Env: CONNECTOR_GITHUB_APP_PRIVATE_KEY_PATH."
        ),
    )
    connector_github_app_webhook_secret: Optional[SecretStr] = Field(
        default=None,
        description=(
            "The GitHub App's webhook secret, used to verify inbound trigger "
            "deliveries. Env: CONNECTOR_GITHUB_APP_WEBHOOK_SECRET."
        ),
    )
    connector_github_app_webhook_secret_previous: Optional[SecretStr] = Field(
        default=None,
        description=(
            "The previous webhook secret, still accepted while a rotation is in "
            "flight. Both are live for as long as it takes to update GitHub, "
            "and with only one the window is a stream of 403s that GitHub "
            "answers by disabling the hook. "
            "Env: CONNECTOR_GITHUB_APP_WEBHOOK_SECRET_PREVIOUS."
        ),
    )
    connector_encryption_key: Optional[str] = Field(
        default=None,
        description=(
            "Fernet key used to encrypt connector auth configs and account "
            "credentials at rest. Required outside local/testing."
        ),
    )


connector_settings = ConnectorSettings()
