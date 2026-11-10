from urllib.parse import urlparse

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from supertokens_python import init, InputAppInfo, SupertokensConfig
from supertokens_python.ingredients.emaildelivery.types import EmailDeliveryConfig
from supertokens_python.recipe import (
    dashboard,
    emailpassword,
    emailverification,
    passwordless,
    session,
)
from supertokens_python.recipe.thirdparty.provider import (
    ProviderInput,
    ProviderConfig,
    ProviderClientConfig,
)
from supertokens_python.recipe import thirdparty
from app.core.config import reveal_secret, settings
from app.modules.identity.infrastructure.supertokens_auth.override_email_password import (
    override_emailpassword_functions,
)
from app.modules.identity.infrastructure.supertokens_auth.override_email_password_apis import (
    override_emailpassword_apis,
)
from app.modules.identity.infrastructure.supertokens_auth.override_thirdparty import (
    override_thirdparty_functions,
)
from app.modules.identity.infrastructure.supertokens_auth.override_session import (
    override_session_functions,
)
from app.modules.identity.infrastructure.supertokens_auth.email_delivery import (
    LemmaPasswordResetEmailService,
    LemmaVerificationEmailService,
)
from app.modules.identity.infrastructure.supertokens_auth.override_email_verification import (
    override_email_verification_apis,
    override_email_verification_functions,
)
from app.modules.identity.infrastructure.supertokens_auth.jwks_guard import (
    install_jwks_guard,
)
from app.modules.identity.infrastructure.supertokens_auth.querier_client import (
    install_shared_querier_client,
)
from app.core.log.log import get_logger
from app.modules.identity.config import IdentitySettings, identity_settings
from app.modules.identity.infrastructure.supertokens_auth.passwordless_challenges import (
    private_passwordless_apis,
)

logger = get_logger(__name__)


def email_verification_mode():
    return "REQUIRED" if settings.auth_email_verification_required else None


def _supertokens_api_domain() -> str:
    parsed_api_url = urlparse(settings.api_url)
    api_path = parsed_api_url.path.rstrip("/")
    gateway_path = identity_settings.supertokens_api_gateway_path

    if (
        parsed_api_url.scheme
        and parsed_api_url.netloc
        and api_path
        and (gateway_path == api_path or gateway_path.startswith(f"{api_path}/"))
    ):
        return f"{parsed_api_url.scheme}://{parsed_api_url.netloc}"

    return settings.api_url


def build_supertokens_app_info() -> InputAppInfo:
    return InputAppInfo(
        app_name=settings.app_name,
        api_domain=_supertokens_api_domain(),
        website_domain=settings.auth_frontend_url,
        api_gateway_path=identity_settings.supertokens_api_gateway_path,
        api_base_path=identity_settings.supertokens_api_base_path,
        website_base_path=identity_settings.auth_website_base_path,
    )


def _apple_private_key(config: IdentitySettings) -> str | None:
    """The `.p8` key as PEM text, or None when it is not a usable key.

    Read once here rather than left to SuperTokens, which loads it only when
    it signs the client secret -- on the click, as a 500. A key that did not
    survive the trip into the environment (a file path where the contents
    belong, a truncated paste) then hides the button and says why in the log.
    """
    raw = reveal_secret(config.apple_private_key) or ""
    pem = raw.strip().strip("\"'").replace("\\n", "\n")
    try:
        key = load_pem_private_key(pem.encode(), password=None)
    except ValueError, TypeError, UnsupportedAlgorithm:
        key = None
    if not isinstance(key, EllipticCurvePrivateKey):
        logger.warning(
            "identity.apple_sign_in.private_key_unusable",
            reason="APPLE_PRIVATE_KEY is not the PEM text of an EC private key",
        )
        return None
    return pem


def apple_provider(config: IdentitySettings) -> ProviderInput | None:
    """Sign in with Apple, or None until all four values are set and usable."""
    if not config.is_apple_oauth_configured():
        return None
    private_key = _apple_private_key(config)
    if private_key is None:
        return None
    assert config.apple_client_id is not None
    return ProviderInput(
        config=ProviderConfig(
            # SuperTokens picks its Apple implementation by this id: OIDC
            # discovery, `response_mode=form_post`, and the client secret
            # signed from the three values below.
            third_party_id="apple",
            clients=[
                ProviderClientConfig(
                    client_id=config.apple_client_id,
                    # SuperTokens asks for openid and email only. Apple sends
                    # the name when asked, and only on the first sign-in, so
                    # asking later cannot recover it.
                    scope=["openid", "email", "name"],
                    additional_config={
                        "teamId": config.apple_team_id,
                        "keyId": config.apple_key_id,
                        "privateKey": private_key,
                    },
                ),
            ],
        ),
    )


def build_thirdparty_providers() -> list[ProviderInput]:
    providers: list[ProviderInput] = []

    if identity_settings.is_google_oauth_configured():
        assert identity_settings.google_client_id is not None
        providers.append(
            ProviderInput(
                config=ProviderConfig(
                    third_party_id="google",
                    clients=[
                        ProviderClientConfig(
                            client_id=identity_settings.google_client_id,
                            client_secret=reveal_secret(
                                identity_settings.google_client_secret
                            ),
                        ),
                    ],
                ),
            )
        )

    if identity_settings.is_microsoft_oauth_configured():
        assert identity_settings.microsoft_client_id is not None
        tenant_id = identity_settings.microsoft_tenant_id or "common"
        microsoft_base_url = (
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0"
        )
        providers.append(
            ProviderInput(
                config=ProviderConfig(
                    third_party_id="active-directory",
                    name="Microsoft",
                    clients=[
                        ProviderClientConfig(
                            client_id=identity_settings.microsoft_client_id,
                            client_secret=reveal_secret(
                                identity_settings.microsoft_client_secret
                            ),
                            scope=["openid", "email", "profile"],
                        ),
                    ],
                    authorization_endpoint=f"{microsoft_base_url}/authorize",
                    token_endpoint=f"{microsoft_base_url}/token",
                    user_info_endpoint="https://graph.microsoft.com/oidc/userinfo",
                )
            )
        )

    apple = apple_provider(identity_settings)
    if apple is not None:
        providers.append(apple)

    return providers


def initialize_supertokens():
    # Before init, so no verification can run against the unguarded function.
    install_jwks_guard()
    # Likewise before init: the querier is what every verification goes through.
    install_shared_querier_client()
    init(
        app_info=build_supertokens_app_info(),
        supertokens_config=SupertokensConfig(
            connection_uri=settings.supertokens_core_url,
        ),
        framework="fastapi",
        recipe_list=[
            session.init(
                cookie_domain=identity_settings.session_cookie_domain,
                # The domain we are migrating *away* from, for one release.
                #
                # Changing `cookie_domain` does not replace the cookies already
                # in a browser -- it mints a second set beside them. The browser
                # then sends both, and SuperTokens answers the refresh with
                # `The request contains multiple session cookies`, a 500. The
                # SDK reads a 500 as retryable and asks again, per query, for
                # ever: an install that had crossed the host-only ->
                # `.lemma.localhost` change logged 30 of those and 17 500s.
                #
                # Set, this clears the old pair instead of colliding with it.
                older_cookie_domain=identity_settings.session_cookie_older_domain,
                cookie_secure=identity_settings.session_cookie_secure,
                cookie_same_site=identity_settings.session_cookie_same_site,
                override=session.SessionOverrideConfig(
                    functions=override_session_functions
                ),
            ),
            emailpassword.init(
                override=emailpassword.InputOverrideConfig(
                    functions=override_emailpassword_functions,
                    apis=override_emailpassword_apis,
                ),
                email_delivery=EmailDeliveryConfig(
                    service=LemmaPasswordResetEmailService()
                ),
            ),
            passwordless.init(
                contact_config=passwordless.ContactEmailOnlyConfig(),
                flow_type="USER_INPUT_CODE",
                override=passwordless.PasswordlessOverrideConfig(
                    apis=private_passwordless_apis,
                ),
            ),
            *(
                [
                    emailverification.init(
                        mode="REQUIRED",
                        email_delivery=EmailDeliveryConfig(
                            service=LemmaVerificationEmailService()
                        ),
                        override=emailverification.EmailVerificationOverrideConfig(
                            functions=override_email_verification_functions,
                            apis=override_email_verification_apis,
                        ),
                    )
                ]
                if settings.auth_email_verification_required
                else []
            ),
            dashboard.init(),
            thirdparty.init(
                override=thirdparty.InputOverrideConfig(
                    functions=override_thirdparty_functions
                ),
                sign_in_and_up_feature=thirdparty.SignInAndUpFeature(
                    providers=build_thirdparty_providers()
                ),
            ),
        ],
        mode="asgi",
    )
