"""Identity module configuration.

Twenty-five settings that only `mod:identity` reads, moved off
`app/core/config.py`. Env var names are unchanged: no settings class in this
repo sets `env_prefix`, so pydantic-settings derives each name from the field
identically on whichever class holds it, and `SESSION_COOKIE_DOMAIN`,
`TELEGRAM_OIDC_CLIENT_ID` and the rest reach this class exactly as they reached
`Settings`. Nothing in lemma-stack, the desktop host pack, compose or the docs
needs touching.

Telegram's OIDC endpoints did not come with them -- see `TELEGRAM_OIDC_*` below.
"""

from typing import Literal, Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path
from app.core.config import reveal_secret

# Telegram's published OIDC deployment, not a choice this deployment makes.
# These were four settings whose env vars nothing in the repo has ever set, and
# whose values are facts about a third party -- so they were configuration in
# name only. Constants also close a real hole: an operator-settable issuer or
# JWKS URI is a token-forgery path, because `_verify` would then trust whatever
# key set the environment pointed it at.
TELEGRAM_OIDC_ISSUER = "https://oauth.telegram.org"
TELEGRAM_OIDC_AUTHORIZATION_ENDPOINT = "https://oauth.telegram.org/auth"
TELEGRAM_OIDC_TOKEN_ENDPOINT = "https://oauth.telegram.org/token"
TELEGRAM_OIDC_JWKS_URI = "https://oauth.telegram.org/.well-known/jwks.json"


class IdentitySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    auth_altcha_hmac_key: Optional[SecretStr] = Field(
        default=None,
        description="HMAC key used to sign self-hosted ALTCHA challenges",
    )
    auth_altcha_max_number: int = Field(
        default=100_000,
        ge=10_000,
        le=2_000_000,
        description="Maximum proof-of-work search space for ALTCHA challenges",
    )
    auth_bounce_webhook_secret: Optional[SecretStr] = Field(
        default=None,
        description="HMAC secret for normalized hard-bounce webhook events",
    )
    auth_disposable_email_allowlist: list[str] = Field(
        default_factory=list,
        description="Domains that override the bundled disposable-email list",
    )
    auth_jwks_unknown_kid_cache_size: int = Field(
        default=1024,
        description=(
            "Maximum key ids remembered as not-found. The sender chooses the "
            "ids, so the map has to be bounded or the guard just moves the "
            "damage from the event loop to memory. Env: "
            "``AUTH_JWKS_UNKNOWN_KID_CACHE_SIZE``."
        ),
    )
    auth_jwks_unknown_kid_ttl_seconds: float = Field(
        default=60.0,
        description=(
            "How long a JWKS key id that was looked up and not found is refused "
            "without going back to the network. SuperTokens reads `kid` from the "
            "token header BEFORE verifying the signature and has no negative "
            "cache, so without this an unauthenticated client sending forged "
            "tokens with random `kid` values forces one synchronous HTTP round "
            "trip per request, on the event loop, under a lock that excludes "
            "every other verification. Set to 0 to disable the guard. Env: "
            "``AUTH_JWKS_UNKNOWN_KID_TTL_SECONDS``."
        ),
    )
    auth_trusted_proxy_ips: list[str] = Field(
        default_factory=list,
        description="Immediate proxy IPs allowed to supply Forwarded/X-Forwarded-For",
    )
    auth_website_base_path: str = Field(
        default="/auth",
        description="Path where the centralized auth UI is rendered",
    )
    auth_whatsapp_mobile_verification_enabled: bool = Field(
        default=False,
        description=(
            "Allow signed messages sent to Lemma's global WhatsApp number to "
            "verify an authenticated user's mobile number"
        ),
    )
    desktop_auth_create_limit: int = Field(
        default=100,
        ge=0,
        description=(
            "Maximum desktop auth handoff requests a client IP may create per "
            "rate-limit window. Set to 0 to disable the application-level cap."
        ),
    )
    desktop_auth_create_window_seconds: int = Field(
        default=60,
        ge=1,
        description="Desktop auth handoff creation rate-limit window in seconds.",
    )
    microsoft_client_id: Optional[str] = Field(
        default=None, description="Microsoft OAuth Client ID"
    )
    microsoft_client_secret: Optional[SecretStr] = Field(
        default=None, description="Microsoft OAuth Client Secret"
    )
    microsoft_tenant_id: Optional[str] = Field(
        default=None,
        description=(
            "Microsoft Entra tenant ID. Defaults to 'common' when unset to allow "
            "both personal and organizational accounts."
        ),
    )
    organization_home_cache_ttl_seconds: int = Field(
        default=30,
        description=(
            "TTL in seconds for the cached organization landing page (pods with "
            "their apps, agents and the caller's roles). Short because it is a "
            "read-heavy view of slow-moving content; the roles it carries are "
            "for display, and every permission check inside a pod resolves them "
            "live. Set to 0 to always rebuild from the database."
        ),
    )
    session_cookie_domain: Optional[str] = Field(
        default=None,
        description="Optional cookie domain for sharing auth sessions across subdomains",
    )
    session_cookie_older_domain: Optional[str] = Field(
        default=None,
        description=(
            "The cookie domain this deployment is migrating away from. Set it "
            "for one release after changing session_cookie_domain so the old "
            "cookies are cleared instead of colliding with the new ones."
        ),
    )
    session_cookie_same_site: Optional[Literal["lax", "none", "strict"]] = Field(
        default=None,
        description="Override SameSite for auth session cookies",
    )
    session_cookie_secure: Optional[bool] = Field(
        default=None,
        description="Override the secure flag for auth session cookies",
    )
    supertokens_api_base_path: str = Field(
        default="/auth",
        description="SuperTokens API base path relative to the SuperTokens gateway",
    )
    supertokens_api_gateway_path: str = Field(
        default="/st",
        description="SuperTokens gateway path relative to api_url",
    )
    telegram_oidc_client_id: Optional[str] = Field(
        default=None,
        description="Telegram Web Login client ID issued by BotFather",
    )
    telegram_oidc_client_secret: Optional[SecretStr] = Field(
        default=None,
        description="Telegram Web Login client secret issued by BotFather",
    )
    telegram_oidc_redirect_uri: Optional[str] = Field(
        default=None,
        description="Registered Telegram OIDC callback URL",
    )
    # datastore query/document-processing/kreuzberg/pdf/signed-url config moved to
    # app/modules/datastore/config.py (datastore_database_url stays here — infra).
    user_cache_ttl_seconds: int = Field(
        default=1800,
        description="TTL for cached identity users loaded by id",
    )

    # Deliberately NOT including `session_cookie_older_domain`: an empty string
    # is a meaningful value there. SuperTokens reads `older_cookie_domain=""`
    # as "the previous cookies were host-only, clear those", which is exactly
    # the migration desktop is making (v0.7.0 rendered SESSION_COOKIE_DOMAIN=""
    # and main renders `.lemma.localhost`). Folding blank to None would turn the
    # one setting that fixes that install into no setting at all.
    @field_validator("session_cookie_domain", mode="before")
    @classmethod
    def _blank_optional_string_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("auth_website_base_path", mode="before")
    @classmethod
    def _normalise_auth_website_base_path(cls, value: object) -> str:
        candidate = str(value or "/auth").strip()
        if "://" in candidate or "?" in candidate or "#" in candidate:
            raise ValueError("AUTH_WEBSITE_BASE_PATH must be a relative URL path")
        segments = [segment for segment in candidate.split("/") if segment]
        if any(segment in {".", ".."} for segment in segments):
            raise ValueError("AUTH_WEBSITE_BASE_PATH cannot contain dot segments")
        return "/" + "/".join(segments) if segments else "/"

    def is_microsoft_oauth_configured(self) -> bool:
        """Check if Microsoft OAuth is properly configured."""
        return all(
            [
                self.microsoft_client_id,
                self.microsoft_client_secret,
            ]
        )

    def is_telegram_oidc_configured(self) -> bool:
        """Return whether the global Telegram Web Login client is usable."""
        return bool(
            self.telegram_oidc_client_id
            and reveal_secret(self.telegram_oidc_client_secret)
            and self.telegram_oidc_redirect_uri
        )


identity_settings = IdentitySettings()
