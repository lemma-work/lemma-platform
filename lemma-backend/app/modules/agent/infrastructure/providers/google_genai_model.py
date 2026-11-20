"""A pydantic-ai model for a GOOGLE_GENAI profile, on either of its hosts.

The Gemini API is signed in to with an API key. Vertex AI is signed in to with
a service account: the key file stored on the profile, or -- for the
deployment's own profile only -- the identity the server already runs as
(Application Default Credentials: Workload Identity on GKE, `gcloud auth
application-default login` on a laptop).

That last fallback is the one to keep narrow. An organization's profile with no
key file must not quietly run as the server's own service account: that would
bill this deployment's Google project for someone else's provider, under a
profile that says it is theirs. So the ambient identity is offered to SYSTEM
profiles and refused everywhere else.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache

import httpx2
from google.genai.types import HttpRetryOptions
from pydantic_ai.models.google import GoogleModel

from app.modules.agent.domain.runtime_profiles import (
    GoogleGenAIBackend,
    GoogleGenAIRuntimeConfig,
    RuntimeProfileScope,
)

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com"
VERTEX_AI_BASE_URL = "https://aiplatform.googleapis.com"
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

#: One attempt per dispatch: the metered model retries, and each attempt has
#: to pass through metering to be counted.
_NO_SDK_RETRIES = HttpRetryOptions(attempts=1)


class GoogleCredentialsMissing(ValueError):
    """The profile names a host it has no way to sign in to."""


def google_genai_endpoint(config: GoogleGenAIRuntimeConfig) -> str:
    """The host a profile's requests go to: what its HTTP pool and its price
    are keyed by."""
    if config.backend is GoogleGenAIBackend.VERTEX_AI:
        return VERTEX_AI_BASE_URL
    return str(config.base_url or GEMINI_API_BASE_URL)


def google_genai_model(
    model_name: str,
    *,
    config: GoogleGenAIRuntimeConfig,
    credentials: Mapping[str, object] | None,
    scope: object,
    http_client: httpx2.AsyncClient | None,
) -> GoogleModel:
    """``model_name`` on the host ``config`` names.

    Raises ``GoogleCredentialsMissing`` when the profile has nothing to sign
    in with -- never falls through to an identity it was not given.
    """
    from pydantic_ai.providers.google import GoogleProvider
    from pydantic_ai.providers.google_cloud import GoogleCloudProvider

    given = credentials or {}
    api_key = _nonempty(given.get("api_key"))
    if config.backend is GoogleGenAIBackend.GEMINI_API:
        if api_key is None:
            raise GoogleCredentialsMissing("The Gemini API needs an API key.")
        provider: GoogleProvider | GoogleCloudProvider = GoogleProvider(
            api_key=api_key,
            base_url=str(config.base_url) if config.base_url else None,
            http_client=http_client,
            retry_options=_NO_SDK_RETRIES,
        )
    else:
        service_account_json = _nonempty(given.get("service_account_json"))
        if service_account_json is None and not _may_use_ambient_identity(scope):
            raise GoogleCredentialsMissing(
                "Vertex AI needs a service account key for this provider."
            )
        provider = GoogleCloudProvider(
            credentials=(
                service_account_credentials(service_account_json)
                if service_account_json is not None
                else None
            ),
            project=config.vertex_project,
            location=config.vertex_location,
            http_client=http_client,
            retry_options=_NO_SDK_RETRIES,
        )
    return GoogleModel(model_name, provider=provider)


def _may_use_ambient_identity(scope: object) -> bool:
    value = scope.value if isinstance(scope, RuntimeProfileScope) else scope
    return value == RuntimeProfileScope.SYSTEM.value


@lru_cache(maxsize=32)
def service_account_credentials(service_account_json: str) -> object:
    """Signed-in credentials for a key file, one object per distinct key.

    Cached because the object holds the access token it minted and refreshes
    it itself; building one per request would mint a token per request. A
    crypto credential, not data -- the reason it is not in Redis.
    """
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_info(
        json.loads(service_account_json), scopes=[_CLOUD_PLATFORM_SCOPE]
    )


def _nonempty(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


async def verify_google_genai_access(
    *,
    config: GoogleGenAIRuntimeConfig,
    credentials: Mapping[str, object] | None,
    scope: object,
    model_name: str,
) -> None:
    """Ask Google for ``model_name`` as this profile would, before it is saved.

    Free -- a model lookup, not a generation -- and it checks the two things a
    new Google provider gets wrong: credentials Google refuses (raised as
    ``ProviderKeyRejectedError``) and a model the project cannot use (as
    ``ValueError``). Anything else -- a timeout, a 5xx -- is not the owner's
    mistake and does not block the save; the connection test reports it.
    """
    from google.genai.errors import ClientError

    from app.modules.agent.infrastructure.providers.discovery import (
        ProviderKeyRejectedError,
    )

    # Its own short-lived client: one lookup when a provider is saved is not
    # worth a pooled connection, and a client left to the SDK is never closed.
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(15.0)) as http_client:
        model = google_genai_model(
            model_name,
            config=config,
            credentials=credentials,
            scope=scope,
            http_client=http_client,
        )
        try:
            await model.client.aio.models.get(model=model_name)
        except ClientError as exc:
            # Google answers a bad Gemini API key with 400 API_KEY_INVALID,
            # not 401, so the reason is read as well as the code.
            if exc.code in (401, 403) or "API_KEY_INVALID" in str(exc.details):
                raise ProviderKeyRejectedError(str(exc.code)) from None
            if exc.code == 404:
                raise ValueError(
                    f"{model_name} isn't available to this account."
                ) from None
            raise
