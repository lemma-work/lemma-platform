"""How a person's Google provider is signed in to, and checked before it is saved.

Shared by create and edit, which both have to turn "an API key" or "a service
account key file" into the credentials and config the protocol takes, and both
have to ask Google whether it accepts them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import SecretStr, ValidationError

from app.modules.agent.domain.errors import RuntimeProfileValidationError
from app.modules.agent.domain.google_genai_catalog import google_genai_catalog
from app.modules.agent.domain.runtime_provider_patch import normalize_profile_name
from app.modules.agent.domain.sentinels import UNSET, UnsetType
from app.modules.agent.domain.runtime_profiles import (
    DEFAULT_VERTEX_LOCATION,
    AgentRuntimeProfile,
    ApiKeyRuntimeCredentials,
    GoogleGenAIBackend,
    GoogleGenAIRuntimeConfig,
    RuntimeModelCatalogEntry,
    RuntimeProfileScope,
    ServiceAccountRuntimeCredentials,
    describe_invalid,
    refuse_redacted_values,
    reveal_credentials,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.providers import discovery
from app.modules.agent.services.generation_settings import validated_stored_settings

GoogleCredentials = ApiKeyRuntimeCredentials | ServiceAccountRuntimeCredentials


def google_genai_credentials(
    backend: GoogleGenAIBackend,
    *,
    api_key: str | None,
    service_account_json: str | None,
) -> GoogleCredentials:
    """The one credential ``backend`` signs in with.

    The Gemini API takes a key and Vertex AI a key file; the other one, if
    sent, is refused rather than stored unused.
    """
    key = (api_key or "").strip()
    key_file = (service_account_json or "").strip()
    refuse_redacted_values(key, field="api_key")
    if backend is GoogleGenAIBackend.GEMINI_API:
        if key_file:
            raise RuntimeProfileValidationError(
                "The Gemini API signs in with an API key, not a service account."
            )
        if not key:
            raise RuntimeProfileValidationError("The Gemini API needs an API key.")
        return ApiKeyRuntimeCredentials(api_key=SecretStr(key))
    if key:
        raise RuntimeProfileValidationError(
            "Vertex AI signs in with a service account key file, not an API key."
        )
    if not key_file:
        raise RuntimeProfileValidationError(
            "Vertex AI needs a service account key file."
        )
    try:
        return ServiceAccountRuntimeCredentials(
            service_account_json=SecretStr(key_file)
        )
    except ValidationError as error:
        raise RuntimeProfileValidationError(
            describe_invalid(error).replace(
                "service_account_json", "The service account key file"
            )
        ) from None


def google_genai_config(
    backend: GoogleGenAIBackend,
    *,
    credentials: GoogleCredentials,
    vertex_project: str | None,
    vertex_location: str | None,
    model_settings: JsonObject,
) -> GoogleGenAIRuntimeConfig:
    """The config for ``backend``. A Vertex project left out is the key
    file's own, which is the project nearly everyone means."""
    if backend is GoogleGenAIBackend.GEMINI_API:
        return GoogleGenAIRuntimeConfig(backend=backend, model_settings=model_settings)
    project = (vertex_project or "").strip() or _key_file_project(credentials)
    try:
        return GoogleGenAIRuntimeConfig(
            backend=backend,
            vertex_project=project,
            vertex_location=(vertex_location or "").strip() or DEFAULT_VERTEX_LOCATION,
            model_settings=model_settings,
        )
    except ValidationError as error:
        raise RuntimeProfileValidationError(describe_invalid(error)) from None


async def verified_google_access(
    config: GoogleGenAIRuntimeConfig,
    credentials: GoogleCredentials,
    *,
    model_name: str,
    provider_name: str,
) -> None:
    """Google accepts these credentials for ``model_name``, or a 400 saying not."""
    from app.modules.agent.infrastructure.providers.google_genai_model import (
        verify_google_genai_access,
    )

    try:
        await verify_google_genai_access(
            config=config,
            credentials=reveal_credentials(credentials),
            scope=RuntimeProfileScope.ORGANIZATION,
            model_name=model_name,
        )
    except discovery.ProviderKeyRejectedError as exc:
        raise RuntimeProfileValidationError(
            f"Google rejected the credentials for {provider_name}."
        ) from exc
    except ValueError as exc:
        raise RuntimeProfileValidationError(str(exc)) from exc


def _key_file_project(credentials: GoogleCredentials) -> str | None:
    if not isinstance(credentials, ServiceAccountRuntimeCredentials):
        return None
    info = json.loads(credentials.service_account_json.get_secret_value())
    project = info.get("project_id")
    return project if isinstance(project, str) else None


@dataclass(frozen=True, slots=True)
class GoogleProfileEdit:
    """What an edit of a Google provider asked for. ``UNSET`` keeps a field.

    Nothing here is cleared by leaving it out: a credential, a project or a
    model list is replaced, never emptied. A description is cleared by None.
    """

    name: str | UnsetType = UNSET
    description: str | None | UnsetType = UNSET
    api_key: str | UnsetType = UNSET
    service_account_json: str | UnsetType = UNSET
    vertex_project: str | UnsetType = UNSET
    vertex_location: str | UnsetType = UNSET
    default_model_name: str | UnsetType = UNSET
    model_names: list[str] | UnsetType = UNSET
    image_model_names: list[str] | UnsetType = UNSET
    model_settings: JsonObject | UnsetType = UNSET


@dataclass(frozen=True, slots=True)
class GoogleProfileChanges:
    """The fields an edit changes, and what Google will be asked as after it."""

    changes: dict[str, object]
    config: GoogleGenAIRuntimeConfig
    credentials: GoogleCredentials

    @property
    def asks_google_differently(self) -> bool:
        return bool(
            {"credentials", "config", "default_model_name"} & self.changes.keys()
        )


def google_profile_changes(
    profile: AgentRuntimeProfile, edit: GoogleProfileEdit
) -> GoogleProfileChanges:
    """What ``edit`` changes on a Google provider, checked but not yet saved."""
    if not isinstance(profile.config, GoogleGenAIRuntimeConfig):
        raise RuntimeProfileValidationError(
            "This runtime profile has a different provider protocol"
        )
    changes = _named(edit)
    credentials = _signed_in(profile, edit, changes)
    config = _configured(profile.config, credentials, edit, changes)
    catalog = _catalogued(profile, edit, changes)
    _choose_default(profile, catalog, edit, changes)
    return GoogleProfileChanges(changes=changes, config=config, credentials=credentials)


def _named(edit: GoogleProfileEdit) -> dict[str, object]:
    changes: dict[str, object] = {}
    if not isinstance(edit.name, UnsetType):
        changes["name"] = normalize_profile_name(edit.name)
    if not isinstance(edit.description, UnsetType):
        changes["description"] = edit.description.strip() if edit.description else None
    return changes


def _signed_in(
    profile: AgentRuntimeProfile, edit: GoogleProfileEdit, changes: dict[str, object]
) -> GoogleCredentials:
    api_key = None if isinstance(edit.api_key, UnsetType) else edit.api_key
    key_file = (
        None
        if isinstance(edit.service_account_json, UnsetType)
        else edit.service_account_json
    )
    if api_key or key_file:
        assert isinstance(profile.config, GoogleGenAIRuntimeConfig)
        credentials = google_genai_credentials(
            profile.config.backend, api_key=api_key, service_account_json=key_file
        )
        changes["credentials"] = credentials
        return credentials
    stored = profile.credentials
    if not isinstance(
        stored, ApiKeyRuntimeCredentials | ServiceAccountRuntimeCredentials
    ):
        raise RuntimeProfileValidationError(
            "This Google provider has no credentials; enter them again."
        )
    return stored


def _configured(
    stored: GoogleGenAIRuntimeConfig,
    credentials: GoogleCredentials,
    edit: GoogleProfileEdit,
    changes: dict[str, object],
) -> GoogleGenAIRuntimeConfig:
    new_key_file = "credentials" in changes
    if (
        not new_key_file
        and isinstance(edit.vertex_project, UnsetType)
        and isinstance(edit.vertex_location, UnsetType)
        and isinstance(edit.model_settings, UnsetType)
    ):
        return stored
    # A project nobody typed came from the old key file, and must not outlive
    # it: with a new key file and no project named, the new file's own is used.
    project = (
        None
        if new_key_file and isinstance(edit.vertex_project, UnsetType)
        else _or(edit.vertex_project, stored.vertex_project)
    )
    config = google_genai_config(
        stored.backend,
        credentials=credentials,
        vertex_project=project,
        vertex_location=_or(edit.vertex_location, stored.vertex_location),
        model_settings=(
            stored.model_settings
            if isinstance(edit.model_settings, UnsetType)
            else validated_stored_settings(edit.model_settings)
        ),
    )
    if config != stored:
        changes["config"] = config
    return config


def _catalogued(
    profile: AgentRuntimeProfile, edit: GoogleProfileEdit, changes: dict[str, object]
) -> list[RuntimeModelCatalogEntry]:
    catalog = list(profile.model_catalog)
    if isinstance(edit.model_names, UnsetType) and isinstance(
        edit.image_model_names, UnsetType
    ):
        return catalog
    catalog = google_genai_catalog(
        _or(
            edit.model_names, [entry.name for entry in catalog if not entry.draws_only]
        ),
        _or(
            edit.image_model_names,
            [entry.name for entry in catalog if entry.draws_only],
        ),
    )
    if all(entry.draws_only for entry in catalog):
        raise RuntimeProfileValidationError(
            "Add at least one chat model, not only image models."
        )
    changes["model_catalog"] = catalog
    return catalog


def _choose_default(
    profile: AgentRuntimeProfile,
    catalog: list[RuntimeModelCatalogEntry],
    edit: GoogleProfileEdit,
    changes: dict[str, object],
) -> None:
    chat_models = [entry for entry in catalog if not entry.draws_only]
    if not isinstance(edit.default_model_name, UnsetType):
        changes["default_model_name"] = discovery._select_provider_default_model(
            requested_model_name=edit.default_model_name, catalog=chat_models
        )
    elif profile.default_model_name not in {entry.name for entry in chat_models}:
        changes["default_model_name"] = chat_models[0].name


def _or[T](value: T | UnsetType, stored: T) -> T:
    return stored if isinstance(value, UnsetType) else value
