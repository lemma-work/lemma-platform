"""Agent surfaces module configuration (native messaging platforms).

Field names are unchanged from the former monolithic ``Settings`` so the
environment variables resolve identically (``SLACK_SIGNING_SECRET``,
``TELEGRAM_BOT_TOKEN``, ``MICROSOFT_BOT_APP_ID``, ``SURFACE_*``, …).

Note: ``microsoft_tenant_id`` and ``microsoft_client_*`` are the *login* OAuth
settings and stay in core/identity — only the Teams *bot* (``microsoft_bot_*``)
belongs here.
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.settings_env import dotenv_path


class SurfaceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
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
    microsoft_bot_app_name: Optional[str] = Field(
        default=None,
        description=(
            "Human-friendly display name of the Lemma Teams bot, used as the "
            "surface reach handle when the Graph servicePrincipal lookup is "
            "unavailable (e.g. Application.Read.All not consented)."
        ),
    )

    # Slack
    slack_signing_secret: Optional[str] = Field(
        default=None,
        description="Slack signing secret for verifying native Slack webhook requests",
    )
    slack_app_id: Optional[str] = Field(
        default=None,
        description=(
            "App id of this deployment's own Slack app (the 'A0…' Slack shows on "
            "the app's Basic Information page). Inbound events name the app they "
            "came from, and the shared webhook may also serve orgs running their "
            "own Slack app, so this is how an event is matched to us rather than "
            "to them. An account connected through OAuth stores its own app id, "
            "which wins; this is the fallback for one that did not."
        ),
    )
    slack_home_logo_url: Optional[str] = Field(
        default=None,
        description=(
            "Public https URL of the logo shown on the Slack App Home. Slack "
            "fetches this from its own servers, so a localhost or private URL "
            "silently renders nothing — leave unset rather than pointing at one."
        ),
    )
    slack_app_token: Optional[str] = Field(
        default=None,
        description="Slack Socket Mode app-level token for local surface receivers",
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
    whatsapp_display_phone_number: Optional[str] = Field(
        default=None,
        description=(
            "Human-messageable global Lemma WhatsApp number. When omitted, the "
            "number is resolved from Meta using whatsapp_phone_number_id."
        ),
    )

    # Telegram
    telegram_bot_token: Optional[str] = Field(
        default=None, description="Telegram bot token (NATIVE mode)"
    )
    telegram_webhook_secret: Optional[str] = Field(
        default=None,
        description="Secret token expected in native Telegram webhook requests",
    )
    telegram_manager_bot_token: Optional[str] = Field(
        default=None,
        description=(
            "Token for the Telegram control-plane bot that provisions dedicated "
            "managed bots for surfaces."
        ),
    )
    telegram_manager_bot_username: Optional[str] = Field(
        default=None,
        description=(
            "Username of the Telegram control-plane bot, without or with the @ prefix."
        ),
    )
    telegram_manager_webhook_secret: Optional[str] = Field(
        default=None,
        description="Secret token expected on Telegram manager webhook requests.",
    )

    # Resend (system email surface)
    # No default. A fallback domain looks like configuration and is not: it
    # mints addresses on a domain nobody owns, which deliver nowhere and whose
    # replies match no surface. Absent means "email is not set up", which the
    # code says out loud rather than papering over.
    resend_inbound_domain: Optional[str] = Field(
        default=None,
        description=(
            "Verified catch-all domain for agent inbound addresses "
            "(agent.pod@<domain>). Required to use the Resend surface."
        ),
    )
    resend_from_name: str = Field(
        default="Lemma", description="Display name on outbound Resend emails"
    )
    resend_auto_provision_enabled: bool = Field(
        default=False,
        description=(
            "Give a pod with no active surface a system Resend email surface the "
            "first time it tries to notify someone. Off by default because it is "
            "outward-facing: every pod that turns it on sends mail from the same "
            "Resend domain, so deliverability and abuse reputation are shared."
        ),
    )

    # Surface webhook ingress + runtime
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
    enable_telegram_manager_polling_mode: bool = Field(
        default=False,
        description=(
            "Poll the Telegram manager bot from the worker process. Intended for "
            "local development without a public HTTPS webhook."
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


def resolve_resend_inbound_secret() -> str | None:
    """The signing secret for the Resend webhook carrying inbound email.

    One setting, ``RESEND_WEBHOOK_SECRET``, owned by core because the API key
    and sender identity already live there and splitting a provider's config
    across two Settings classes is what produced two secrets, two API keys and
    two answers to "which domain do we send from".
    """
    from app.core.config import reveal_secret, settings

    return (reveal_secret(settings.resend_webhook_secret) or "").strip() or None


def resolve_resend_api_key() -> str | None:
    """The Resend API key, read from its single home in core settings."""
    from app.core.config import reveal_secret, settings

    return (reveal_secret(settings.resend_api_key) or "").strip() or None
