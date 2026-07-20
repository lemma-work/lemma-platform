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
from app.core.config import settings
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
from app.core.log.log import get_logger

logger = get_logger(__name__)


def email_verification_mode():
    return "REQUIRED" if settings.auth_email_verification_required else "OPTIONAL"


def _supertokens_api_domain() -> str:
    parsed_api_url = urlparse(settings.api_url)
    api_path = parsed_api_url.path.rstrip("/")
    gateway_path = settings.supertokens_api_gateway_path

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
        api_gateway_path=settings.supertokens_api_gateway_path,
        api_base_path=settings.supertokens_api_base_path,
        website_base_path=settings.auth_website_base_path,
    )


def build_thirdparty_providers() -> list[ProviderInput]:
    providers: list[ProviderInput] = []

    if settings.is_google_oauth_configured():
        assert settings.google_client_id is not None
        providers.append(
            ProviderInput(
                config=ProviderConfig(
                    third_party_id="google",
                    clients=[
                        ProviderClientConfig(
                            client_id=settings.google_client_id,
                            client_secret=settings.google_client_secret,
                        ),
                    ],
                ),
            )
        )

    if settings.is_microsoft_oauth_configured():
        assert settings.microsoft_client_id is not None
        tenant_id = settings.microsoft_tenant_id or "common"
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
                            client_id=settings.microsoft_client_id,
                            client_secret=settings.microsoft_client_secret,
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
    init(
        app_info=build_supertokens_app_info(),
        supertokens_config=SupertokensConfig(
            connection_uri=settings.supertokens_core_url,
        ),
        framework="fastapi",
        recipe_list=[
            session.init(
                cookie_domain=settings.session_cookie_domain,
                cookie_secure=settings.session_cookie_secure,
                cookie_same_site=settings.session_cookie_same_site,
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
            emailverification.init(
                mode=email_verification_mode(),
                email_delivery=EmailDeliveryConfig(
                    service=LemmaVerificationEmailService()
                ),
                override=emailverification.EmailVerificationOverrideConfig(
                    functions=override_email_verification_functions,
                    apis=override_email_verification_apis,
                ),
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
