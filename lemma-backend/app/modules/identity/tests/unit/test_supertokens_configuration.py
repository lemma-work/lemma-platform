from __future__ import annotations

from app.core.cors import get_allowed_cors_origin_regex, get_allowed_cors_origins
from app.core.config import settings
from app.modules.identity.infrastructure.supertokens_auth.initialization import (
    build_supertokens_app_info,
    build_thirdparty_providers,
    initialize_supertokens,
)
from app.modules.test_support.e2e_base import _reset_supertokens_testing_state


def test_build_supertokens_app_info_uses_full_urls():
    original_api_url = settings.api_url
    original_auth_frontend_url = settings.auth_frontend_url
    original_auth_website_base_path = settings.auth_website_base_path

    settings.api_url = "https://api.lemma.work"
    settings.auth_frontend_url = "https://auth.lemma.work"
    settings.auth_website_base_path = "/auth"

    try:
        app_info = build_supertokens_app_info()
    finally:
        settings.api_url = original_api_url
        settings.auth_frontend_url = original_auth_frontend_url
        settings.auth_website_base_path = original_auth_website_base_path

    assert app_info.api_domain == "https://api.lemma.work"
    assert app_info.website_domain == "https://auth.lemma.work"
    assert app_info.website_base_path == "/auth"


def test_e2e_reset_allows_repeated_supertokens_initialization(monkeypatch):
    """Every SDK-owned singleton must be reset between function-scoped apps."""

    monkeypatch.setenv("SUPERTOKENS_ENV", "testing")
    _reset_supertokens_testing_state()
    try:
        initialize_supertokens()
        _reset_supertokens_testing_state()
        initialize_supertokens()
    finally:
        _reset_supertokens_testing_state()


def test_build_supertokens_app_info_uses_origin_when_gateway_contains_api_prefix():
    original_api_url = settings.api_url
    original_gateway_path = settings.supertokens_api_gateway_path

    settings.api_url = "http://localhost:8000/api"
    settings.supertokens_api_gateway_path = "/api/st"

    try:
        app_info = build_supertokens_app_info()
    finally:
        settings.api_url = original_api_url
        settings.supertokens_api_gateway_path = original_gateway_path

    assert app_info.api_domain == "http://localhost:8000"
    assert app_info.api_gateway_path == "/api/st"


def test_get_allowed_cors_origins_normalises_and_deduplicates():
    original_frontend_url = settings.frontend_url
    original_auth_frontend_url = settings.auth_frontend_url
    original_cors_origins = settings.cors_origins

    settings.frontend_url = "https://app.lemma.work/dashboard"
    settings.auth_frontend_url = "https://auth.lemma.work/auth"
    settings.cors_origins = [
        "https://app.lemma.work",
        "https://app.lemma.work",
        "http://127.0.0.1:5173/path",
        "http://127.0.0.1:5173",
        "not-an-origin",
    ]

    try:
        origins = get_allowed_cors_origins()
    finally:
        settings.frontend_url = original_frontend_url
        settings.auth_frontend_url = original_auth_frontend_url
        settings.cors_origins = original_cors_origins

    assert origins == [
        "https://app.lemma.work",
        "https://auth.lemma.work",
        "http://127.0.0.1:5173",
    ]


def test_get_allowed_cors_origin_regex_passthrough():
    original_cors_origin_regex = settings.cors_origin_regex
    original_app_base_domain = settings.app_base_domain
    settings.cors_origin_regex = r"^https://([a-z0-9-]+\.)*lemma\.work$"
    # With no app base domain there is nothing to OR in: the configured regex
    # passes through verbatim.
    settings.app_base_domain = ""

    try:
        assert (
            get_allowed_cors_origin_regex()
            == r"^https://([a-z0-9-]+\.)*lemma\.work$"
        )
    finally:
        settings.cors_origin_regex = original_cors_origin_regex
        settings.app_base_domain = original_app_base_domain


def test_build_thirdparty_providers_includes_google_and_microsoft_when_configured():
    original_google_client_id = settings.google_client_id
    original_google_client_secret = settings.google_client_secret
    original_microsoft_client_id = settings.microsoft_client_id
    original_microsoft_client_secret = settings.microsoft_client_secret
    original_microsoft_tenant_id = settings.microsoft_tenant_id

    settings.google_client_id = "google-client-id"
    settings.google_client_secret = "google-client-secret"
    settings.microsoft_client_id = "microsoft-client-id"
    settings.microsoft_client_secret = "microsoft-client-secret"
    settings.microsoft_tenant_id = "tenant-123"

    try:
        providers = build_thirdparty_providers()
    finally:
        settings.google_client_id = original_google_client_id
        settings.google_client_secret = original_google_client_secret
        settings.microsoft_client_id = original_microsoft_client_id
        settings.microsoft_client_secret = original_microsoft_client_secret
        settings.microsoft_tenant_id = original_microsoft_tenant_id

    assert [provider.config.third_party_id for provider in providers] == [
        "google",
        "active-directory",
    ]
    microsoft_provider = providers[1].config
    assert microsoft_provider.name == "Microsoft"
    assert (
        microsoft_provider.authorization_endpoint
        == "https://login.microsoftonline.com/tenant-123/oauth2/v2.0/authorize"
    )
    assert (
        microsoft_provider.token_endpoint
        == "https://login.microsoftonline.com/tenant-123/oauth2/v2.0/token"
    )
    assert microsoft_provider.user_info_endpoint == "https://graph.microsoft.com/oidc/userinfo"


def test_build_thirdparty_providers_uses_common_tenant_for_microsoft_by_default():
    original_google_client_id = settings.google_client_id
    original_google_client_secret = settings.google_client_secret
    original_microsoft_client_id = settings.microsoft_client_id
    original_microsoft_client_secret = settings.microsoft_client_secret
    original_microsoft_tenant_id = settings.microsoft_tenant_id

    settings.google_client_id = None
    settings.google_client_secret = None
    settings.microsoft_client_id = "microsoft-client-id"
    settings.microsoft_client_secret = "microsoft-client-secret"
    settings.microsoft_tenant_id = None

    try:
        providers = build_thirdparty_providers()
    finally:
        settings.google_client_id = original_google_client_id
        settings.google_client_secret = original_google_client_secret
        settings.microsoft_client_id = original_microsoft_client_id
        settings.microsoft_client_secret = original_microsoft_client_secret
        settings.microsoft_tenant_id = original_microsoft_tenant_id

    assert [provider.config.third_party_id for provider in providers] == [
        "active-directory"
    ]
    microsoft_provider = providers[0].config
    assert (
        microsoft_provider.authorization_endpoint
        == "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    )


def test_build_thirdparty_providers_skips_unconfigured_providers():
    original_google_client_id = settings.google_client_id
    original_google_client_secret = settings.google_client_secret
    original_microsoft_client_id = settings.microsoft_client_id
    original_microsoft_client_secret = settings.microsoft_client_secret
    original_microsoft_tenant_id = settings.microsoft_tenant_id

    settings.google_client_id = "google-client-id"
    settings.google_client_secret = None
    settings.microsoft_client_id = "microsoft-client-id"
    settings.microsoft_client_secret = None
    settings.microsoft_tenant_id = "tenant-123"

    try:
        providers = build_thirdparty_providers()
    finally:
        settings.google_client_id = original_google_client_id
        settings.google_client_secret = original_google_client_secret
        settings.microsoft_client_id = original_microsoft_client_id
        settings.microsoft_client_secret = original_microsoft_client_secret
        settings.microsoft_tenant_id = original_microsoft_tenant_id

    assert providers == []
