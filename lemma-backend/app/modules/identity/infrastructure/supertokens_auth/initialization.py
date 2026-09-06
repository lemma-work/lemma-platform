from urllib.parse import urlparse

from supertokens_python import init, InputAppInfo, SupertokensConfig
from supertokens_python.ingredients.emaildelivery.types import EmailDeliveryConfig
from supertokens_python.recipe import (
    dashboard,
    emailpassword,
    emailverification,
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
from app.core.log.log import get_logger
from app.modules.identity.config import identity_settings

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

    return providers


def initialize_supertokens():
    # Before init, so no verification can run against the unguarded function.
    install_jwks_guard()
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
