"""The deployment's own Google Gen AI profile, and the image model it draws with.

Built from `LEMMA_GOOGLE_*` the way the other protocols' system profiles are
built from theirs (`runtime_system_profiles`), which hands over to this module
when `LEMMA_DEFAULT_MODEL_TYPE=google_genai`.
"""

from __future__ import annotations

import os

from pydantic import SecretStr

from app.core.domain.errors import DomainError
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.google_genai_catalog import google_genai_catalog
from app.modules.agent.domain.runtime_profiles import (
    SYSTEM_LEMMA_PROFILE_ID,
    AgentRuntimeProfile,
    ApiKeyRuntimeCredentials,
    GoogleGenAIBackend,
    GoogleGenAIRuntimeConfig,
    RuntimeModelCapability,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    ServiceAccountRuntimeCredentials,
)
from app.modules.agent.services.context_budget import catalog_metadata_for

# The system profiles' own helpers, shared rather than copied so a Google
# deployment reads its settings exactly as the other protocols do.
from app.modules.agent.services.runtime_system_profiles import (
    _csv_setting_or_empty,
    _display_model_name,
    _env_or_setting,
    _no_models_configured,
    system_lemma_profile,
    system_profile_configured,
)


def system_lemma_google_profile() -> AgentRuntimeProfile | None:
    """The deployment's own Google Gen AI profile, on Vertex AI or the Gemini API.

    A Vertex project makes it Vertex, signed in as a service account: the
    configured key file, else the identity the server already runs as. Without
    one it is the Gemini API, and needs its key. Neither, and there is no
    profile -- the same as an unset key on the other protocols.
    """
    project = (
        os.getenv("LEMMA_GOOGLE_VERTEX_PROJECT")
        or agent_settings.lemma_google_vertex_project
        or ""
    ).strip()
    credentials: ApiKeyRuntimeCredentials | ServiceAccountRuntimeCredentials | None
    if project:
        config = GoogleGenAIRuntimeConfig(
            backend=GoogleGenAIBackend.VERTEX_AI,
            vertex_project=project,
            vertex_location=(
                os.getenv("LEMMA_GOOGLE_VERTEX_LOCATION")
                or agent_settings.lemma_google_vertex_location
            ),
        )
        key_file = _env_or_setting(
            "LEMMA_GOOGLE_SERVICE_ACCOUNT_JSON",
            agent_settings.lemma_google_service_account_json,
        )
        credentials = (
            ServiceAccountRuntimeCredentials(service_account_json=SecretStr(key_file))
            if key_file
            else None
        )
    else:
        api_key = _env_or_setting(
            "LEMMA_GOOGLE_API_KEY", agent_settings.lemma_google_api_key
        )
        if not api_key:
            return None
        config = GoogleGenAIRuntimeConfig(backend=GoogleGenAIBackend.GEMINI_API)
        credentials = ApiKeyRuntimeCredentials(api_key=SecretStr(api_key))
    model_names = _csv_setting_or_empty(
        os.getenv("LEMMA_GOOGLE_MODEL_NAMES") or agent_settings.lemma_google_model_names
    )
    default_model_name = (
        os.getenv("LEMMA_GOOGLE_DEFAULT_MODEL")
        or agent_settings.lemma_google_default_model
    ).strip()
    if default_model_name and default_model_name not in model_names:
        model_names.insert(0, default_model_name)
    if not model_names:
        raise _no_models_configured(
            credential_setting="LEMMA_GOOGLE_VERTEX_PROJECT or LEMMA_GOOGLE_API_KEY",
            names_setting="LEMMA_GOOGLE_MODEL_NAMES",
            default_setting="LEMMA_GOOGLE_DEFAULT_MODEL",
        )
    image_model_names = _csv_setting_or_empty(
        os.getenv("LEMMA_GOOGLE_IMAGE_MODEL_NAMES")
        or agent_settings.lemma_google_image_model_names
    )
    return AgentRuntimeProfile(
        id=SYSTEM_LEMMA_PROFILE_ID,
        scope=RuntimeProfileScope.SYSTEM,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=RuntimeProfileProtocol.GOOGLE_GENAI,
        name="Lemma",
        description="System Lemma model provider",
        default_model_name=default_model_name or model_names[0],
        model_catalog=google_genai_catalog(
            model_names,
            image_model_names,
            display_name=_display_model_name,
            metadata_for=catalog_metadata_for,
        ),
        config=config,
        credentials=credentials,
    )


def system_image_model_name() -> str | None:
    """The model the system provider draws with: ``IMAGE_MODEL``, else the
    first image model it lists, else ``None``."""
    configured = (os.getenv("IMAGE_MODEL") or agent_settings.image_model or "").strip()
    if configured:
        return configured
    try:
        profile = system_lemma_profile()
    except DomainError:
        return None
    if profile is None:
        return None
    for entry in profile.model_catalog:
        if RuntimeModelCapability.IMAGE_OUTPUT in entry.capabilities:
            return entry.name
    return None


def image_generation_available() -> bool:
    """Whether a run may be offered the image generator.

    Like the vision delegate: with no system provider, a workspace provider
    may have an image model, which only the database can say -- so yes, and
    the tool reports plainly when none turns up.
    """
    return system_image_model_name() is not None or not system_profile_configured()
