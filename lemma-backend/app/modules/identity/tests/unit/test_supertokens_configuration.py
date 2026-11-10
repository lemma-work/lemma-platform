from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError
from supertokens_python.recipe import emailverification

from app.core.cors import get_allowed_cors_origin_regex, get_allowed_cors_origins
from app.core.config import settings
from app.modules.identity.infrastructure.supertokens_auth.initialization import (
    apple_provider,
    build_supertokens_app_info,
    build_thirdparty_providers,
    email_verification_mode,
    initialize_supertokens,
)
from app.modules.test_support.e2e_base import (
    _reset_supertokens_testing_state,
    supertokens_state_restored,
)
from app.modules.identity.config import IdentitySettings, identity_settings


def test_build_supertokens_app_info_uses_full_urls():
    original_api_url = settings.api_url
    original_auth_frontend_url = settings.auth_frontend_url
    original_auth_website_base_path = identity_settings.auth_website_base_path

    settings.api_url = "https://api.lemma.work"
    settings.auth_frontend_url = "https://auth.lemma.work"
    identity_settings.auth_website_base_path = "/auth"

    try:
        app_info = build_supertokens_app_info()
    finally:
        settings.api_url = original_api_url
        settings.auth_frontend_url = original_auth_frontend_url
        identity_settings.auth_website_base_path = original_auth_website_base_path

    assert app_info.api_domain == "https://api.lemma.work"
    assert app_info.website_domain == "https://auth.lemma.work"
    assert app_info.website_base_path == "/auth"


def test_auth_website_base_path_defaults_to_auth_and_is_normalised():
    # The field and its normalising validator moved to `IdentitySettings`
    # together; asserted here rather than on core, which no longer has either.
    default_settings = IdentitySettings(_env_file=None)
    explicit_settings = IdentitySettings(_env_file=None, auth_website_base_path="auth/")

    assert default_settings.auth_website_base_path == "/auth"
    assert explicit_settings.auth_website_base_path == "/auth"


@pytest.mark.parametrize(
    "value",
    ["https://lemma.work/auth", "/auth?next=/", "/auth#fragment", "/auth/../admin"],
)
def test_auth_website_base_path_rejects_non_path_values(value: str):
    with pytest.raises(ValidationError):
        IdentitySettings(_env_file=None, auth_website_base_path=value)


def test_email_verification_mode_follows_configuration(monkeypatch):
    monkeypatch.setattr(settings, "auth_email_verification_required", True)
    assert email_verification_mode() == "REQUIRED"

    monkeypatch.setattr(settings, "auth_email_verification_required", False)
    assert email_verification_mode() is None


def test_disabled_email_verification_recipe_is_not_registered(monkeypatch):
    monkeypatch.setenv("SUPERTOKENS_ENV", "testing")
    monkeypatch.setattr(settings, "auth_email_verification_required", False)
    monkeypatch.setattr(
        emailverification,
        "init",
        lambda **_kwargs: pytest.fail(
            "disabled local auth must not initialise email verification"
        ),
    )

    with supertokens_state_restored():
        initialize_supertokens()


def test_e2e_reset_allows_repeated_supertokens_initialization(monkeypatch):
    """Every SDK-owned singleton must be reset between function-scoped apps."""

    monkeypatch.setenv("SUPERTOKENS_ENV", "testing")
    with supertokens_state_restored():
        initialize_supertokens()
        _reset_supertokens_testing_state()
        initialize_supertokens()


def test_reset_inside_a_test_leaves_the_process_initialised_as_it_found_it(
    monkeypatch,
):
    """Importing the app initialises the SDK once for the whole worker.

    Unit tests that call the app without its lifespan depend on that, so a test
    that resets the SDK must hand the same instance back -- otherwise whichever
    test xdist schedules next in this worker gets "Initialisation not done".
    """
    from supertokens_python.supertokens import Supertokens

    import app.app  # noqa: F401 -- initialises the SDK, as any app test would

    monkeypatch.setenv("SUPERTOKENS_ENV", "testing")
    before = Supertokens.get_instance()

    with supertokens_state_restored():
        initialize_supertokens()
        assert Supertokens.get_instance() is not before

    assert Supertokens.get_instance() is before


def test_the_querier_configuration_a_test_installs_is_taken_back(monkeypatch):
    """``network_interceptor`` and ``api_version`` are plain class attributes,
    not name-mangled ones, so a snapshot of only the mangled state left a
    test's interceptor installed for every later request in the worker."""
    from supertokens_python.querier import Querier

    monkeypatch.setenv("SUPERTOKENS_ENV", "testing")
    interceptor_before = Querier.network_interceptor
    version_before = Querier.api_version

    def interceptor(*args: object) -> tuple[object, ...]:
        return args

    with supertokens_state_restored():
        Querier.network_interceptor = interceptor
        Querier.api_version = "0.0-test"

    assert Querier.network_interceptor is interceptor_before
    assert Querier.api_version == version_before


def test_build_supertokens_app_info_uses_origin_when_gateway_contains_api_prefix():
    original_api_url = settings.api_url
    original_gateway_path = identity_settings.supertokens_api_gateway_path

    settings.api_url = "http://localhost:8000/api"
    identity_settings.supertokens_api_gateway_path = "/api/st"

    try:
        app_info = build_supertokens_app_info()
    finally:
        settings.api_url = original_api_url
        identity_settings.supertokens_api_gateway_path = original_gateway_path

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
            get_allowed_cors_origin_regex() == r"^https://([a-z0-9-]+\.)*lemma\.work$"
        )
    finally:
        settings.cors_origin_regex = original_cors_origin_regex
        settings.app_base_domain = original_app_base_domain


def test_build_thirdparty_providers_includes_google_and_microsoft_when_configured():
    original_google_client_id = identity_settings.google_client_id
    original_google_client_secret = identity_settings.google_client_secret
    original_microsoft_client_id = identity_settings.microsoft_client_id
    original_microsoft_client_secret = identity_settings.microsoft_client_secret
    original_microsoft_tenant_id = identity_settings.microsoft_tenant_id

    identity_settings.google_client_id = "google-client-id"
    identity_settings.google_client_secret = "google-client-secret"
    identity_settings.microsoft_client_id = "microsoft-client-id"
    identity_settings.microsoft_client_secret = "microsoft-client-secret"
    identity_settings.microsoft_tenant_id = "tenant-123"

    try:
        providers = build_thirdparty_providers()
    finally:
        identity_settings.google_client_id = original_google_client_id
        identity_settings.google_client_secret = original_google_client_secret
        identity_settings.microsoft_client_id = original_microsoft_client_id
        identity_settings.microsoft_client_secret = original_microsoft_client_secret
        identity_settings.microsoft_tenant_id = original_microsoft_tenant_id

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
    assert (
        microsoft_provider.user_info_endpoint
        == "https://graph.microsoft.com/oidc/userinfo"
    )


def test_build_thirdparty_providers_uses_common_tenant_for_microsoft_by_default():
    original_google_client_id = identity_settings.google_client_id
    original_google_client_secret = identity_settings.google_client_secret
    original_microsoft_client_id = identity_settings.microsoft_client_id
    original_microsoft_client_secret = identity_settings.microsoft_client_secret
    original_microsoft_tenant_id = identity_settings.microsoft_tenant_id

    identity_settings.google_client_id = None
    identity_settings.google_client_secret = None
    identity_settings.microsoft_client_id = "microsoft-client-id"
    identity_settings.microsoft_client_secret = "microsoft-client-secret"
    identity_settings.microsoft_tenant_id = None

    try:
        providers = build_thirdparty_providers()
    finally:
        identity_settings.google_client_id = original_google_client_id
        identity_settings.google_client_secret = original_google_client_secret
        identity_settings.microsoft_client_id = original_microsoft_client_id
        identity_settings.microsoft_client_secret = original_microsoft_client_secret
        identity_settings.microsoft_tenant_id = original_microsoft_tenant_id

    assert [provider.config.third_party_id for provider in providers] == [
        "active-directory"
    ]
    microsoft_provider = providers[0].config
    assert (
        microsoft_provider.authorization_endpoint
        == "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    )


def test_build_thirdparty_providers_skips_unconfigured_providers():
    original_google_client_id = identity_settings.google_client_id
    original_google_client_secret = identity_settings.google_client_secret
    original_microsoft_client_id = identity_settings.microsoft_client_id
    original_microsoft_client_secret = identity_settings.microsoft_client_secret
    original_microsoft_tenant_id = identity_settings.microsoft_tenant_id

    identity_settings.google_client_id = "google-client-id"
    identity_settings.google_client_secret = None
    identity_settings.microsoft_client_id = "microsoft-client-id"
    identity_settings.microsoft_client_secret = None
    identity_settings.microsoft_tenant_id = "tenant-123"

    try:
        providers = build_thirdparty_providers()
    finally:
        identity_settings.google_client_id = original_google_client_id
        identity_settings.google_client_secret = original_google_client_secret
        identity_settings.microsoft_client_id = original_microsoft_client_id
        identity_settings.microsoft_client_secret = original_microsoft_client_secret
        identity_settings.microsoft_tenant_id = original_microsoft_tenant_id

    assert providers == []


def _p8_key() -> str:
    """A throwaway key in the shape Apple issues: PKCS#8 PEM on P-256."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    return (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )


def _apple_settings(**values: str | None) -> IdentitySettings:
    configured: dict[str, str | SecretStr | None] = {
        "apple_client_id": "com.example.signin",
        "apple_team_id": "TEAM123456",
        "apple_key_id": "KEY1234567",
        "apple_private_key": _p8_key(),
    }
    configured.update(values)
    key = configured["apple_private_key"]
    configured["apple_private_key"] = SecretStr(key) if isinstance(key, str) else key
    return IdentitySettings.model_construct(**configured)


async def test_apple_provider_signs_its_client_secret_from_the_configured_key():
    import jwt
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from supertokens_python.recipe.thirdparty.providers.apple import Apple

    pem = _p8_key()
    # How a key usually reaches the environment: one line, newlines escaped.
    provider = apple_provider(
        _apple_settings(apple_private_key=pem.replace("\n", "\\n"))
    )

    assert provider is not None
    assert provider.config.third_party_id == "apple"
    [client] = provider.config.clients or []
    assert client.client_id == "com.example.signin"
    assert client.scope == ["openid", "email", "name"]

    # The client secret is minted by SuperTokens' own Apple implementation, so
    # sign one: that is the proof team, key id and key reach it in the shape
    # it reads them.
    config = await Apple(provider).get_config_for_client_type(None, {})
    assert config.client_secret
    assert jwt.get_unverified_header(config.client_secret)["kid"] == "KEY1234567"
    public_key = load_pem_private_key(pem.encode(), password=None).public_key()
    claims = jwt.decode(
        config.client_secret,
        public_key,  # type: ignore[arg-type]  # an EC public key; jwt's union is wider
        algorithms=["ES256"],
        audience="https://appleid.apple.com",
    )
    assert claims["iss"] == "TEAM123456"
    assert claims["sub"] == "com.example.signin"


@pytest.mark.parametrize(
    "missing",
    ["apple_client_id", "apple_team_id", "apple_key_id", "apple_private_key"],
)
def test_apple_provider_is_absent_without_all_four_values(missing: str):
    config = _apple_settings(**{missing: None})

    assert config.is_apple_oauth_configured() is False
    assert apple_provider(config) is None


def test_apple_provider_is_absent_when_the_key_is_unusable():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    truncated = _p8_key()[:120]
    rsa_key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )

    for unusable in (truncated, rsa_key, "/secrets/AuthKey_KEY1234567.p8"):
        config = _apple_settings(apple_private_key=unusable)
        assert config.is_apple_oauth_configured() is True
        assert apple_provider(config) is None, unusable


def test_build_thirdparty_providers_registers_apple_after_the_others(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(identity_settings, "google_client_id", "google-client-id")
    monkeypatch.setattr(identity_settings, "google_client_secret", "google-secret")
    monkeypatch.setattr(identity_settings, "microsoft_client_id", None)
    monkeypatch.setattr(identity_settings, "apple_client_id", "com.example.signin")
    monkeypatch.setattr(identity_settings, "apple_team_id", "TEAM123456")
    monkeypatch.setattr(identity_settings, "apple_key_id", "KEY1234567")
    monkeypatch.setattr(identity_settings, "apple_private_key", SecretStr(_p8_key()))

    assert [p.config.third_party_id for p in build_thirdparty_providers()] == [
        "google",
        "apple",
    ]

    monkeypatch.setattr(identity_settings, "apple_key_id", None)
    assert [p.config.third_party_id for p in build_thirdparty_providers()] == ["google"]
