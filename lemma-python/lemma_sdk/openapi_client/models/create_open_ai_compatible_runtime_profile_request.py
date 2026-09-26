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
    from ..models.create_open_ai_compatible_runtime_profile_request_headers import (
        CreateOpenAICompatibleRuntimeProfileRequestHeaders,
    )
    from ..models.create_open_ai_compatible_runtime_profile_request_model_settings import (
        CreateOpenAICompatibleRuntimeProfileRequestModelSettings,
    )


T = TypeVar("T", bound="CreateOpenAICompatibleRuntimeProfileRequest")


@_attrs_define
class CreateOpenAICompatibleRuntimeProfileRequest:
    """
    Attributes:
        base_url (str):
        name (str):
        api_key (None | str | Unset):
        default_model_name (None | str | Unset):
        description (None | str | Unset):
        headers (CreateOpenAICompatibleRuntimeProfileRequestHeaders | Unset):
        model_names (list[str] | Unset):
        model_settings (CreateOpenAICompatibleRuntimeProfileRequestModelSettings | Unset):
        source (Literal['OPENAI_COMPATIBLE'] | Unset):  Default: 'OPENAI_COMPATIBLE'.
        vision_model_names (list[str] | Unset):
    """

    base_url: str
    name: str
    api_key: None | str | Unset = UNSET
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    headers: CreateOpenAICompatibleRuntimeProfileRequestHeaders | Unset = UNSET
    model_names: list[str] | Unset = UNSET
    model_settings: CreateOpenAICompatibleRuntimeProfileRequestModelSettings | Unset = (
        UNSET
    )
    source: Literal["OPENAI_COMPATIBLE"] | Unset = "OPENAI_COMPATIBLE"
    vision_model_names: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base_url = self.base_url

        name = self.name

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

        headers: dict[str, Any] | Unset = UNSET
        if not isinstance(self.headers, Unset):
            headers = self.headers.to_dict()

        model_names: list[str] | Unset = UNSET
        if not isinstance(self.model_names, Unset):
            model_names = self.model_names

        model_settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model_settings, Unset):
            model_settings = self.model_settings.to_dict()

        source = self.source

        vision_model_names: list[str] | Unset = UNSET
        if not isinstance(self.vision_model_names, Unset):
            vision_model_names = self.vision_model_names

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "base_url": base_url,
                "name": name,
            }
        )
        if api_key is not UNSET:
            field_dict["api_key"] = api_key
        if default_model_name is not UNSET:
            field_dict["default_model_name"] = default_model_name
        if description is not UNSET:
            field_dict["description"] = description
        if headers is not UNSET:
            field_dict["headers"] = headers
        if model_names is not UNSET:
            field_dict["model_names"] = model_names
        if model_settings is not UNSET:
            field_dict["model_settings"] = model_settings
        if source is not UNSET:
            field_dict["source"] = source
        if vision_model_names is not UNSET:
            field_dict["vision_model_names"] = vision_model_names

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_open_ai_compatible_runtime_profile_request_headers import (
            CreateOpenAICompatibleRuntimeProfileRequestHeaders,
        )
        from ..models.create_open_ai_compatible_runtime_profile_request_model_settings import (
            CreateOpenAICompatibleRuntimeProfileRequestModelSettings,
        )

        d = dict(src_dict)
        base_url = d.pop("base_url")

        name = d.pop("name")

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

        _headers = d.pop("headers", UNSET)
        headers: CreateOpenAICompatibleRuntimeProfileRequestHeaders | Unset
        if isinstance(_headers, Unset):
            headers = UNSET
        else:
            headers = CreateOpenAICompatibleRuntimeProfileRequestHeaders.from_dict(
                _headers
            )

        model_names = cast(list[str], d.pop("model_names", UNSET))

        _model_settings = d.pop("model_settings", UNSET)
        model_settings: CreateOpenAICompatibleRuntimeProfileRequestModelSettings | Unset
        if isinstance(_model_settings, Unset):
            model_settings = UNSET
        else:
            model_settings = (
                CreateOpenAICompatibleRuntimeProfileRequestModelSettings.from_dict(
                    _model_settings
                )
            )

        source = cast(Literal["OPENAI_COMPATIBLE"] | Unset, d.pop("source", UNSET))
        if source != "OPENAI_COMPATIBLE" and not isinstance(source, Unset):
            raise ValueError(
                f"source must match const 'OPENAI_COMPATIBLE', got '{source}'"
            )

        vision_model_names = cast(list[str], d.pop("vision_model_names", UNSET))

        create_open_ai_compatible_runtime_profile_request = cls(
            base_url=base_url,
            name=name,
            api_key=api_key,
            default_model_name=default_model_name,
            description=description,
            headers=headers,
            model_names=model_names,
            model_settings=model_settings,
            source=source,
            vision_model_names=vision_model_names,
        )

        create_open_ai_compatible_runtime_profile_request.additional_properties = d
        return create_open_ai_compatible_runtime_profile_request

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
