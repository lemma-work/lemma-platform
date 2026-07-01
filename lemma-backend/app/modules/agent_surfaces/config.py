"""Agent surfaces module configuration (native messaging platforms).

Field names are unchanged from the former monolithic ``Settings`` so the
environment variables resolve identically (``SLACK_BOT_TOKEN``,
``TELEGRAM_BOT_TOKEN``, ``MICROSOFT_BOT_APP_ID``, ``SURFACE_*``, …).

Note: ``microsoft_tenant_id`` and ``microsoft_client_*`` are the *login* OAuth
settings and stay in core/identity — only the Teams *bot* (``microsoft_bot_*``)
belongs here.
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SurfaceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # Microsoft Teams bot (separate from login OAuth)
    microsoft_bot_app_id: Optional[str] = Field(
        default=None,
        description=(
            "Microsoft App ID for the Lemma Teams bot (separate from the login OAuth app). "
            "Used to acquire Bot Framework and Graph API tokens via client_credentials grant."
        ),
    )
    microsoft_bot_app_password: Optional[str] = Field(
        default=None,
        description="Client secret for the Lemma Teams bot App Registration.",
    )
    microsoft_bot_tenant_id: Optional[str] = Field(
        default=None,
        description=(
            "Azure tenant ID where the Lemma bot App Registration lives (Lemma's own tenant). "
            "Used as the token endpoint tenant when acquiring Bot Framework tokens. "
            "If omitted, falls back to 'botframework.com' (multi-tenant default)."
        ),
    )
    microsoft_bot_openid_config_url: Optional[str] = Field(
        default=None,
        description=(
            "Optional override for the Bot Framework OpenID configuration URL. "
            "Useful for local testing of Teams webhook JWT validation."
        ),
    )

    # Slack
    slack_signing_secret: Optional[str] = Field(
        default=None,
        description="Slack signing secret for verifying native Slack webhook requests",
    )
    slack_app_token: Optional[str] = Field(
        default=None,
        description="Slack Socket Mode app-level token for local surface receivers",
    )
    slack_bot_token: Optional[str] = Field(
        default=None,
        description="Slack bot token for Lemma-managed native Slack surfaces",
    )

    # WhatsApp Business API
    whatsapp_access_token: Optional[str] = Field(
        default=None, description="WhatsApp Business API access token (NATIVE mode)"
    )
    whatsapp_phone_number_id: Optional[str] = Field(
        default=None, description="WhatsApp Business phone number ID (NATIVE mode)"
    )
    whatsapp_waba_id: Optional[str] = Field(
        default=None, description="WhatsApp Business Account ID (NATIVE mode)"
    )
    whatsapp_verify_token: Optional[str] = Field(
        default=None, description="WhatsApp webhook verification token"
    )
    whatsapp_app_secret: Optional[str] = Field(
        default=None,
        description="Meta app secret for verifying WhatsApp webhook signatures",
    )

    # Telegram
    telegram_bot_token: Optional[str] = Field(
        default=None, description="Telegram bot token (NATIVE mode)"
    )
    telegram_webhook_secret: Optional[str] = Field(
        default=None,
        description="Secret token expected in native Telegram webhook requests",
    )

    # Resend (system email surface)
    resend_api_key: Optional[str] = Field(
        default=None, description="Resend API key for the system email surface"
    )
    resend_inbound_domain: str = Field(
        default="ops.lemma.work",
        description="Domain for per-pod inbound addresses (pod.org@<domain>)",
    )
    resend_from_name: str = Field(
        default="Lemma", description="Display name on outbound Resend emails"
    )
    resend_inbound_signing_secret: Optional[str] = Field(
        default=None,
        description="Secret for verifying Resend inbound webhook signatures",
    )

    # Surface webhook ingress + runtime
    surface_raw_webhook_log_dir: Optional[str] = Field(
        default=None,
        description=(
            "Optional directory for appending raw surface webhook payloads as JSONL "
            "records for debugging."
        ),
    )
    surface_raw_webhook_log_sources: str = Field(
        default="",
        description=(
            "Comma-separated webhook sources to log to the raw surface webhook "
            "debug directory. Empty means log every source."
        ),
    )
    surface_webhook_security_enabled: bool = Field(
        default=True,
        description=(
            "Enable signature, token, and JWT verification for agent-surface "
            "webhook ingress. Disable only for local development when testing with "
            "temporary public URLs."
        ),
    )
    surface_event_dedupe_ttl_seconds: int = Field(
        default=900,
        description="Short TTL for Redis-based agent surface webhook dedupe keys.",
    )
    surface_runtime_history_max_messages: int = Field(
        default=40,
        description=(
            "Maximum prior persisted messages to pass to the model for external "
            "agent-surface conversations. The latest inbound message is passed "
            "separately as the user prompt."
        ),
    )
    surface_runtime_history_window_hours: int = Field(
        default=24,
        description=(
            "Maximum age, in hours, of prior persisted messages passed to the model "
            "for external agent-surface conversations. Set to 0 to disable the "
            "time window."
        ),
    )

    # Native receiver toggles (worker process)
    enable_telegram_polling_mode: bool = Field(
        default=False,
        description=(
            "Start the native Telegram getUpdates receiver from the worker process. "
            "This is intended for local/server environments without Telegram webhooks."
        ),
    )
    enable_slack_socket_mode: bool = Field(
        default=False,
        description=(
            "Start the native Slack Socket Mode receiver from the worker process. "
            "Requires SLACK_APP_TOKEN. Workspace bot credentials are resolved from "
            "the matched surface account."
        ),
    )


surface_settings = SurfaceSettings()
