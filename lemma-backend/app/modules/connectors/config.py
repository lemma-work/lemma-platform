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

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.settings_env import dotenv_path


class ConnectorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(), env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    composio_api_key: Optional[str] = Field(
        default=None, description="Composio API key"
    )
    composio_webhook_secret: Optional[str] = Field(
        default=None, description="Composio webhook secret"
    )
    connector_operation_timeout_seconds: float = Field(
        default=45.0,
        description=(
            "Hard timeout for a single connector operation execution. Bounds "
            "calls to external providers so a hung/slow upstream fails fast "
            "instead of holding a DB connection (and wedging the event loop)."
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
