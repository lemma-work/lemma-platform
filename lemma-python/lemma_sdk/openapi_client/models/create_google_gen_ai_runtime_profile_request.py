from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.google_gen_ai_backend import GoogleGenAIBackend
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_google_gen_ai_runtime_profile_request_model_settings import (
        CreateGoogleGenAIRuntimeProfileRequestModelSettings,
    )


T = TypeVar("T", bound="CreateGoogleGenAIRuntimeProfileRequest")


@_attrs_define
class CreateGoogleGenAIRuntimeProfileRequest:
    """A Google provider: the Gemini API with an API key, or Vertex AI with a
    service account key file. Never the server's own Google identity.

        Attributes:
            name (str):
            api_key (None | str | Unset):
            backend (GoogleGenAIBackend | Unset): Which host answers the Google Gen AI API, and so how it is signed in to.

                ``GEMINI_API`` takes an API key. ``VERTEX_AI`` takes a service account --
                a stored key file, or for the deployment's own profile, the identity the
                server already runs as (Application Default Credentials).
            default_model_name (None | str | Unset):
            description (None | str | Unset):
            image_model_names (list[str] | None | Unset):
            model_names (list[str] | Unset):
            model_settings (CreateGoogleGenAIRuntimeProfileRequestModelSettings | Unset):
            service_account_json (None | str | Unset):
            source (Literal['GOOGLE_GENAI'] | Unset):  Default: 'GOOGLE_GENAI'.
            vertex_location (None | str | Unset):
            vertex_project (None | str | Unset):
    """

    name: str
    api_key: None | str | Unset = UNSET
    backend: GoogleGenAIBackend | Unset = UNSET
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    image_model_names: list[str] | None | Unset = UNSET
    model_names: list[str] | Unset = UNSET
    model_settings: CreateGoogleGenAIRuntimeProfileRequestModelSettings | Unset = UNSET
    service_account_json: None | str | Unset = UNSET
    source: Literal["GOOGLE_GENAI"] | Unset = "GOOGLE_GENAI"
    vertex_location: None | str | Unset = UNSET
    vertex_project: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        api_key: None | str | Unset
        if isinstance(self.api_key, Unset):
            api_key = UNSET
        else:
            api_key = self.api_key

        backend: str | Unset = UNSET
        if not isinstance(self.backend, Unset):
            backend = self.backend.value

        default_model_name: None | str | Unset
        if isinstance(self.default_model_name, Unset):
            default_model_name = UNSET
        else:
            default_model_name = self.default_model_name

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        image_model_names: list[str] | None | Unset
        if isinstance(self.image_model_names, Unset):
            image_model_names = UNSET
        elif isinstance(self.image_model_names, list):
            image_model_names = self.image_model_names

        else:
            image_model_names = self.image_model_names

        model_names: list[str] | Unset = UNSET
        if not isinstance(self.model_names, Unset):
            model_names = self.model_names

        model_settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model_settings, Unset):
            model_settings = self.model_settings.to_dict()

        service_account_json: None | str | Unset
        if isinstance(self.service_account_json, Unset):
            service_account_json = UNSET
        else:
            service_account_json = self.service_account_json

        source = self.source

        vertex_location: None | str | Unset
        if isinstance(self.vertex_location, Unset):
            vertex_location = UNSET
        else:
            vertex_location = self.vertex_location

        vertex_project: None | str | Unset
        if isinstance(self.vertex_project, Unset):
            vertex_project = UNSET
        else:
            vertex_project = self.vertex_project

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if api_key is not UNSET:
            field_dict["api_key"] = api_key
        if backend is not UNSET:
            field_dict["backend"] = backend
        if default_model_name is not UNSET:
            field_dict["default_model_name"] = default_model_name
        if description is not UNSET:
            field_dict["description"] = description
        if image_model_names is not UNSET:
            field_dict["image_model_names"] = image_model_names
        if model_names is not UNSET:
            field_dict["model_names"] = model_names
        if model_settings is not UNSET:
            field_dict["model_settings"] = model_settings
        if service_account_json is not UNSET:
            field_dict["service_account_json"] = service_account_json
        if source is not UNSET:
            field_dict["source"] = source
        if vertex_location is not UNSET:
            field_dict["vertex_location"] = vertex_location
        if vertex_project is not UNSET:
            field_dict["vertex_project"] = vertex_project

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_google_gen_ai_runtime_profile_request_model_settings import (
            CreateGoogleGenAIRuntimeProfileRequestModelSettings,
        )

        d = dict(src_dict)
        name = d.pop("name")

        def _parse_api_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        api_key = _parse_api_key(d.pop("api_key", UNSET))

        _backend = d.pop("backend", UNSET)
        backend: GoogleGenAIBackend | Unset
        if isinstance(_backend, Unset):
            backend = UNSET
        else:
            backend = GoogleGenAIBackend(_backend)

        def _parse_default_model_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        default_model_name = _parse_default_model_name(
            d.pop("default_model_name", UNSET)
        )

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_image_model_names(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                image_model_names_type_0 = cast(list[str], data)

                return image_model_names_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        image_model_names = _parse_image_model_names(d.pop("image_model_names", UNSET))

        model_names = cast(list[str], d.pop("model_names", UNSET))

        _model_settings = d.pop("model_settings", UNSET)
        model_settings: CreateGoogleGenAIRuntimeProfileRequestModelSettings | Unset
        if isinstance(_model_settings, Unset):
            model_settings = UNSET
        else:
            model_settings = (
                CreateGoogleGenAIRuntimeProfileRequestModelSettings.from_dict(
                    _model_settings
                )
            )

        def _parse_service_account_json(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        service_account_json = _parse_service_account_json(
            d.pop("service_account_json", UNSET)
        )

        source = cast(Literal["GOOGLE_GENAI"] | Unset, d.pop("source", UNSET))
        if source != "GOOGLE_GENAI" and not isinstance(source, Unset):
            raise ValueError(f"source must match const 'GOOGLE_GENAI', got '{source}'")

        def _parse_vertex_location(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        vertex_location = _parse_vertex_location(d.pop("vertex_location", UNSET))

        def _parse_vertex_project(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        vertex_project = _parse_vertex_project(d.pop("vertex_project", UNSET))

        create_google_gen_ai_runtime_profile_request = cls(
            name=name,
            api_key=api_key,
            backend=backend,
            default_model_name=default_model_name,
            description=description,
            image_model_names=image_model_names,
            model_names=model_names,
            model_settings=model_settings,
            service_account_json=service_account_json,
            source=source,
            vertex_location=vertex_location,
            vertex_project=vertex_project,
        )

        create_google_gen_ai_runtime_profile_request.additional_properties = d
        return create_google_gen_ai_runtime_profile_request

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
