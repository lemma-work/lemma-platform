"""Build Pydantic AI models from resolved agent runtime profiles."""

from __future__ import annotations

from collections.abc import Mapping

from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.modules.agent.services.openai_schema_compat import (
    openai_compatible_model_profile,
)


def pydantic_ai_model_from_runtime_profile(
    *,
    runtime_profile: Mapping[str, object] | None,
    runtime_credentials: Mapping[str, object] | None = None,
    fallback_model_name: str | None = None,
) -> Model | None:
    """Return a Pydantic AI model for model-provider runtime profiles."""
    # e2e mock mode: every model (harness, title generation, any direct
    # agent.run) is the deterministic FunctionModel — no real provider, no key.
    # The harness layers conversation-scripted behaviour on top via its own
    # build_mock_model(conversation); here (no conversation) it's the default.
    from app.modules.agent.infrastructure.harnesses.mock_model import (
        build_mock_model,
        is_mock_llm_enabled,
    )

    if is_mock_llm_enabled():
        return build_mock_model(None)

    if not isinstance(runtime_profile, Mapping):
        return None

    runtime_type = runtime_profile.get("runtime_type")
    provider_model_name = runtime_profile.get("provider_model_name")
    model_name_value = (
        provider_model_name
        if isinstance(provider_model_name, str) and provider_model_name
        else fallback_model_name
    )
    if not isinstance(model_name_value, str) or not model_name_value:
        return None

    config = runtime_profile.get("config")
    if not isinstance(config, Mapping):
        return None

    credentials = (
        runtime_credentials if isinstance(runtime_credentials, Mapping) else {}
    )
    api_key = credentials.get("api_key")
    api_key = api_key if isinstance(api_key, str) and api_key else None

    headers = config.get("headers")
    headers = headers if isinstance(headers, Mapping) else {}

    if runtime_type == "OPENAI_COMPATIBLE":
        base_url = config.get("base_url")
        if not isinstance(base_url, str) or not base_url:
            return None
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "EMPTY",
            default_headers={str(key): str(value) for key, value in headers.items()},
        )
        return OpenAIChatModel(
            model_name_value,
            provider=OpenAIProvider(openai_client=client),
            # Inline `$defs`/`$ref` in tool schemas: some OpenAI-compatible
            # providers (e.g. Fireworks GLM) can't resolve references server-side.
            profile=openai_compatible_model_profile,
        )

    if runtime_type == "ANTHROPIC_COMPATIBLE":
        base_url = config.get("base_url")
        provider = AnthropicProvider(
            api_key=api_key,
            base_url=base_url if isinstance(base_url, str) and base_url else None,
        )
        return AnthropicModel(model_name_value, provider=provider)

    if runtime_type == "AZURE_OPENAI":
        from pydantic_ai.providers.azure import AzureProvider

        endpoint = config.get("azure_endpoint")
        if not isinstance(endpoint, str) or not endpoint or not api_key:
            return None
        api_version = config.get("api_version")
        return OpenAIChatModel(
            model_name_value,
            provider=AzureProvider(
                azure_endpoint=endpoint,
                api_version=(
                    api_version
                    if isinstance(api_version, str) and api_version
                    else None
                ),
                api_key=api_key,
            ),
        )

    if runtime_type == "GOOGLE_VERTEX":
        from google.oauth2 import service_account
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google_cloud import GoogleCloudProvider

        project_id = config.get("project_id")
        location = config.get("location")
        if (
            not isinstance(project_id, str)
            or not project_id
            or not isinstance(location, str)
            or not location
        ):
            return None
        raw_service_account = credentials.get("service_account_json")
        google_credentials = (
            service_account.Credentials.from_service_account_info(
                dict(raw_service_account)
            )
            if isinstance(raw_service_account, Mapping)
            else None
        )
        return GoogleModel(
            model_name_value,
            provider=GoogleCloudProvider(
                credentials=google_credentials,
                project=project_id,
                location=location,
            ),
        )

    return None


def require_pydantic_ai_model_from_runtime_profile(
    *,
    runtime_profile: Mapping[str, object] | None,
    runtime_credentials: Mapping[str, object] | None = None,
    fallback_model_name: str | None = None,
) -> Model:
    """Return a Pydantic AI model or raise a clear profile configuration error."""
    model = pydantic_ai_model_from_runtime_profile(
        runtime_profile=runtime_profile,
        runtime_credentials=runtime_credentials,
        fallback_model_name=fallback_model_name,
    )
    if model is None:
        profile_id = (
            runtime_profile.get("profile_id")
            if isinstance(runtime_profile, Mapping)
            else None
        )
        raise RuntimeError(
            f"Runtime profile {profile_id or '<missing>'!r} cannot build a Pydantic AI model"
        )
    return model


async def default_system_pydantic_ai_model() -> Model:
    """Build the code-defined system default profile model."""
    resolved = await default_system_runtime()
    return require_pydantic_ai_model_from_runtime_profile(
        runtime_profile=resolved.public_snapshot(),
        runtime_credentials=resolved.credentials or {},
        fallback_model_name=resolved.model_name_for_harness,
    )


async def default_system_runtime():
    """Resolve the code-defined system default runtime profile."""
    from uuid import uuid4

    from app.modules.agent.domain.value_objects import AgentRuntimeConfig
    from app.modules.agent.services.runtime_profile_service import (
        DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
        AgentRuntimeProfileService,
    )

    return await AgentRuntimeProfileService().resolve(
        runtime=AgentRuntimeConfig(profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID),
        organization_id=None,
        user_id=uuid4(),
    )
