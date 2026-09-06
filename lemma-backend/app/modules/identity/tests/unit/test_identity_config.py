"""Golden test for identity config: env-var names + defaults preserved.

The field set is exact and every default is asserted, for the same reason the
datastore and agent versions of this file are: these came out of
`app/core/config.py`, and a value drifting in the move is the failure mode that
looks like nothing. Transcribed from `Settings` before the move and checked
against it -- all 25 came across unchanged.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import Settings as CoreSettings, reveal_secret
from app.modules.identity.config import (
    TELEGRAM_OIDC_ISSUER,
    TELEGRAM_OIDC_JWKS_URI,
    IdentitySettings,
)

pytestmark = pytest.mark.unit

# (field, ENV var, default)
EXPECTED = [
    ("auth_altcha_hmac_key", "AUTH_ALTCHA_HMAC_KEY", None),
    ("auth_altcha_max_number", "AUTH_ALTCHA_MAX_NUMBER", 100000),
    ("auth_bounce_webhook_secret", "AUTH_BOUNCE_WEBHOOK_SECRET", None),
    # `default_factory=list`, so `.default` is PydanticUndefined and the
    # effective default is a fresh empty list -- asserted through an instance
    # below rather than through the field.
    ("auth_disposable_email_allowlist", "AUTH_DISPOSABLE_EMAIL_ALLOWLIST", []),
    ("auth_jwks_unknown_kid_cache_size", "AUTH_JWKS_UNKNOWN_KID_CACHE_SIZE", 1024),
    ("auth_jwks_unknown_kid_ttl_seconds", "AUTH_JWKS_UNKNOWN_KID_TTL_SECONDS", 60.0),
    ("auth_trusted_proxy_ips", "AUTH_TRUSTED_PROXY_IPS", []),
    ("auth_website_base_path", "AUTH_WEBSITE_BASE_PATH", "/auth"),
    (
        "auth_whatsapp_mobile_verification_enabled",
        "AUTH_WHATSAPP_MOBILE_VERIFICATION_ENABLED",
        False,
    ),
    ("desktop_auth_create_limit", "DESKTOP_AUTH_CREATE_LIMIT", 100),
    ("desktop_auth_create_window_seconds", "DESKTOP_AUTH_CREATE_WINDOW_SECONDS", 60),
    ("microsoft_client_id", "MICROSOFT_CLIENT_ID", None),
    ("microsoft_client_secret", "MICROSOFT_CLIENT_SECRET", None),
    ("microsoft_tenant_id", "MICROSOFT_TENANT_ID", None),
    ("organization_home_cache_ttl_seconds", "ORGANIZATION_HOME_CACHE_TTL_SECONDS", 30),
    ("session_cookie_domain", "SESSION_COOKIE_DOMAIN", None),
    ("session_cookie_older_domain", "SESSION_COOKIE_OLDER_DOMAIN", None),
    ("session_cookie_same_site", "SESSION_COOKIE_SAME_SITE", None),
    ("session_cookie_secure", "SESSION_COOKIE_SECURE", None),
    ("supertokens_api_base_path", "SUPERTOKENS_API_BASE_PATH", "/auth"),
    ("supertokens_api_gateway_path", "SUPERTOKENS_API_GATEWAY_PATH", "/st"),
    ("telegram_oidc_client_id", "TELEGRAM_OIDC_CLIENT_ID", None),
    ("telegram_oidc_client_secret", "TELEGRAM_OIDC_CLIENT_SECRET", None),
    ("telegram_oidc_redirect_uri", "TELEGRAM_OIDC_REDIRECT_URI", None),
    ("user_cache_ttl_seconds", "USER_CACHE_TTL_SECONDS", 1800),
]


def test_identity_settings_field_set_is_exact():
    assert set(IdentitySettings.model_fields) == {
        field for field, _env, _default in EXPECTED
    }


def test_identity_settings_defaults():
    # Declared defaults only -- immune to a developer's local .env / os.environ.
    for field, _env, default in EXPECTED:
        info = IdentitySettings.model_fields[field]
        actual = info.default_factory() if info.default_factory else info.default
        if isinstance(default, SecretStr):
            assert isinstance(actual, SecretStr) or actual is None, field
            continue
        assert actual == default, field


def test_a_blank_older_cookie_domain_is_kept_rather_than_folded_to_none():
    """`older_cookie_domain=""` is a value, not an absent one.

    SuperTokens reads the empty string as "the cookies being replaced were
    host-only" and clears them; `None` means "nothing to replace" and clears
    nothing. Desktop is making exactly that migration -- v0.7.0 rendered
    `SESSION_COOKIE_DOMAIN` empty, main renders `.lemma.localhost` -- so an
    install that crossed it holds both cookies, sends both, and SuperTokens
    answers the refresh 500 with `The request contains multiple session
    cookies`. The SDK retries a 500 per query, for ever.

    Its neighbours *do* fold blank to None, which is why this is
    asserted rather than left to a future tidy-up: making this field consistent
    with them is the one edit that silently un-fixes the loop.
    """
    assert (
        IdentitySettings(session_cookie_older_domain="").session_cookie_older_domain
        == ""
    )
    assert IdentitySettings().session_cookie_older_domain is None
    # ...while the neighbour it sits beside keeps folding blank to None.
    assert IdentitySettings(session_cookie_domain="").session_cookie_domain is None


def test_telegram_oidc_endpoints_are_constants_not_settings():
    """Telegram's OIDC endpoints must not be reachable from the environment.

    They were four settings whose env vars nothing in this repo has ever set,
    and whose values are facts about Telegram rather than choices this
    deployment makes. Two of them decide who signs a token this service will
    trust: an operator-settable `TELEGRAM_OIDC_ISSUER` or
    `TELEGRAM_OIDC_JWKS_URI` lets whoever can set an env var point verification
    at a key set they control. Constants, and asserted so here, because
    "nothing sets it today" is not the same claim as "nothing can".
    """
    for name in (
        "telegram_oidc_issuer",
        "telegram_oidc_authorization_endpoint",
        "telegram_oidc_token_endpoint",
        "telegram_oidc_jwks_uri",
    ):
        assert name not in IdentitySettings.model_fields, name

    assert TELEGRAM_OIDC_ISSUER == "https://oauth.telegram.org"
    assert TELEGRAM_OIDC_JWKS_URI.startswith(TELEGRAM_OIDC_ISSUER + "/")


def test_the_two_configured_predicates_moved_with_their_fields():
    """`is_*_configured` reads `self.<field>`, which no settings gate can see.

    `check_settings_attrs.py` matches `<alias>.<attr>` from an import, so a
    method on the settings class reading its own field is invisible to it.
    Leaving either of these behind on `Settings` while their fields moved would
    have been an AttributeError at runtime and a green gate -- so they are
    asserted to be here.
    """
    settings = IdentitySettings()
    assert settings.is_microsoft_oauth_configured() is False
    assert settings.is_telegram_oidc_configured() is False
    assert not hasattr(CoreSettings, "is_microsoft_oauth_configured")
    assert not hasattr(CoreSettings, "is_telegram_oidc_configured")


# Identity's credential-valued fields. `microsoft_client_secret` was covered by
# `core/tests/unit/test_settings_secrets.py` until it moved here; the rest never
# had this assertion at all, which the move is a good moment to fix.
SECRET_FIELDS = (
    "auth_altcha_hmac_key",
    "auth_bounce_webhook_secret",
    "microsoft_client_secret",
    "telegram_oidc_client_secret",
)


@pytest.mark.parametrize("field", SECRET_FIELDS)
def test_an_identity_secret_is_hidden_in_a_repr(field: str) -> None:
    annotation = IdentitySettings.model_fields[field].annotation
    assert "SecretStr" in str(annotation), (
        f"{field} is typed {annotation}, so its value appears in full in any "
        f"repr of the settings object -- a traceback, a log record, a debugger"
    )

    plaintext = f"the-real-{field.replace('_', '-')}"
    configured = IdentitySettings.model_construct(**{field: SecretStr(plaintext)})

    assert plaintext not in repr(getattr(configured, field))
    assert plaintext not in repr(configured)
    # And still usable at the point of use, which is the other half.
    assert reveal_secret(getattr(configured, field)) == plaintext
