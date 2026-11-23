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

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.update_google_gen_ai_runtime_profile_request_model_settings_type_0 import (
        UpdateGoogleGenAIRuntimeProfileRequestModelSettingsType0,
    )


T = TypeVar("T", bound="UpdateGoogleGenAIRuntimeProfileRequest")


@_attrs_define
class UpdateGoogleGenAIRuntimeProfileRequest:
    """Omitted keeps a field. The host (``backend``) is not editable: moving
    between the Gemini API and Vertex AI is a new provider.

        Attributes:
            api_key (None | str | Unset):
            default_model_name (None | str | Unset):
            description (None | str | Unset):
            image_model_names (list[str] | None | Unset):
            model_names (list[str] | None | Unset):
            model_settings (None | Unset | UpdateGoogleGenAIRuntimeProfileRequestModelSettingsType0):
            name (None | str | Unset):
            service_account_json (None | str | Unset):
            source (Literal['GOOGLE_GENAI'] | Unset):  Default: 'GOOGLE_GENAI'.
            vertex_location (None | str | Unset):
            vertex_project (None | str | Unset):
    """

    api_key: None | str | Unset = UNSET
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    image_model_names: list[str] | None | Unset = UNSET
    model_names: list[str] | None | Unset = UNSET
    model_settings: (
        None | Unset | UpdateGoogleGenAIRuntimeProfileRequestModelSettingsType0
    ) = UNSET
    name: None | str | Unset = UNSET
    service_account_json: None | str | Unset = UNSET
    source: Literal["GOOGLE_GENAI"] | Unset = "GOOGLE_GENAI"
    vertex_location: None | str | Unset = UNSET
    vertex_project: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.update_google_gen_ai_runtime_profile_request_model_settings_type_0 import (
            UpdateGoogleGenAIRuntimeProfileRequestModelSettingsType0,
        )

        api_key: None | str | Unset
        if isinstance(self.api_key, Unset):
            api_key = UNSET
        else:
            api_key = self.api_key

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

        model_names: list[str] | None | Unset
        if isinstance(self.model_names, Unset):
            model_names = UNSET
        elif isinstance(self.model_names, list):
            model_names = self.model_names

        else:
            model_names = self.model_names

        model_settings: dict[str, Any] | None | Unset
        if isinstance(self.model_settings, Unset):
            model_settings = UNSET
        elif isinstance(
            self.model_settings,
            UpdateGoogleGenAIRuntimeProfileRequestModelSettingsType0,
        ):
            model_settings = self.model_settings.to_dict()
        else:
            model_settings = self.model_settings

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

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
        field_dict.update({})
        if api_key is not UNSET:
            field_dict["api_key"] = api_key
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
        if name is not UNSET:
            field_dict["name"] = name
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
        from ..models.update_google_gen_ai_runtime_profile_request_model_settings_type_0 import (
            UpdateGoogleGenAIRuntimeProfileRequestModelSettingsType0,
        )

        d = dict(src_dict)

        def _parse_api_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        api_key = _parse_api_key(d.pop("api_key", UNSET))

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

        def _parse_model_names(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                model_names_type_0 = cast(list[str], data)

                return model_names_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        model_names = _parse_model_names(d.pop("model_names", UNSET))

        def _parse_model_settings(
            data: object,
        ) -> None | Unset | UpdateGoogleGenAIRuntimeProfileRequestModelSettingsType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                model_settings_type_0 = (
                    UpdateGoogleGenAIRuntimeProfileRequestModelSettingsType0.from_dict(
                        data
                    )
                )

                return model_settings_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                None | Unset | UpdateGoogleGenAIRuntimeProfileRequestModelSettingsType0,
                data,
            )

        model_settings = _parse_model_settings(d.pop("model_settings", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

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

        update_google_gen_ai_runtime_profile_request = cls(
            api_key=api_key,
            default_model_name=default_model_name,
            description=description,
            image_model_names=image_model_names,
            model_names=model_names,
            model_settings=model_settings,
            name=name,
            service_account_json=service_account_json,
            source=source,
            vertex_location=vertex_location,
            vertex_project=vertex_project,
        )

        update_google_gen_ai_runtime_profile_request.additional_properties = d
        return update_google_gen_ai_runtime_profile_request

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
