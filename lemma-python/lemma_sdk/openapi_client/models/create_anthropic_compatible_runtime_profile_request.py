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
    from ..models.create_anthropic_compatible_runtime_profile_request_headers import (
        CreateAnthropicCompatibleRuntimeProfileRequestHeaders,
    )
    from ..models.create_anthropic_compatible_runtime_profile_request_model_settings import (
        CreateAnthropicCompatibleRuntimeProfileRequestModelSettings,
    )


T = TypeVar("T", bound="CreateAnthropicCompatibleRuntimeProfileRequest")


@_attrs_define
class CreateAnthropicCompatibleRuntimeProfileRequest:
    """
    Attributes:
        api_key (str):
        name (str):
        base_url (None | str | Unset):
        default_model_name (None | str | Unset):
        description (None | str | Unset):
        headers (CreateAnthropicCompatibleRuntimeProfileRequestHeaders | Unset):
        model_names (list[str] | Unset):
        model_settings (CreateAnthropicCompatibleRuntimeProfileRequestModelSettings | Unset):
        reasoning_model_names (list[str] | Unset):
        source (Literal['ANTHROPIC_COMPATIBLE'] | Unset):  Default: 'ANTHROPIC_COMPATIBLE'.
    """

    api_key: str
    name: str
    base_url: None | str | Unset = UNSET
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    headers: CreateAnthropicCompatibleRuntimeProfileRequestHeaders | Unset = UNSET
    model_names: list[str] | Unset = UNSET
    model_settings: (
        CreateAnthropicCompatibleRuntimeProfileRequestModelSettings | Unset
    ) = UNSET
    reasoning_model_names: list[str] | Unset = UNSET
    source: Literal["ANTHROPIC_COMPATIBLE"] | Unset = "ANTHROPIC_COMPATIBLE"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        api_key = self.api_key

        name = self.name

        base_url: None | str | Unset
        if isinstance(self.base_url, Unset):
            base_url = UNSET
        else:
            base_url = self.base_url

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

        reasoning_model_names: list[str] | Unset = UNSET
        if not isinstance(self.reasoning_model_names, Unset):
            reasoning_model_names = self.reasoning_model_names

        source = self.source

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "api_key": api_key,
                "name": name,
            }
        )
        if base_url is not UNSET:
            field_dict["base_url"] = base_url
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
        if reasoning_model_names is not UNSET:
            field_dict["reasoning_model_names"] = reasoning_model_names
        if source is not UNSET:
            field_dict["source"] = source

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_anthropic_compatible_runtime_profile_request_headers import (
            CreateAnthropicCompatibleRuntimeProfileRequestHeaders,
        )
        from ..models.create_anthropic_compatible_runtime_profile_request_model_settings import (
            CreateAnthropicCompatibleRuntimeProfileRequestModelSettings,
        )

        d = dict(src_dict)
        api_key = d.pop("api_key")

        name = d.pop("name")

        def _parse_base_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        base_url = _parse_base_url(d.pop("base_url", UNSET))

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
        headers: CreateAnthropicCompatibleRuntimeProfileRequestHeaders | Unset
        if isinstance(_headers, Unset):
            headers = UNSET
        else:
            headers = CreateAnthropicCompatibleRuntimeProfileRequestHeaders.from_dict(
                _headers
            )

        model_names = cast(list[str], d.pop("model_names", UNSET))

        _model_settings = d.pop("model_settings", UNSET)
        model_settings: (
            CreateAnthropicCompatibleRuntimeProfileRequestModelSettings | Unset
        )
        if isinstance(_model_settings, Unset):
            model_settings = UNSET
        else:
            model_settings = (
                CreateAnthropicCompatibleRuntimeProfileRequestModelSettings.from_dict(
                    _model_settings
                )
            )

        reasoning_model_names = cast(list[str], d.pop("reasoning_model_names", UNSET))

        source = cast(Literal["ANTHROPIC_COMPATIBLE"] | Unset, d.pop("source", UNSET))
        if source != "ANTHROPIC_COMPATIBLE" and not isinstance(source, Unset):
            raise ValueError(
                f"source must match const 'ANTHROPIC_COMPATIBLE', got '{source}'"
            )

        create_anthropic_compatible_runtime_profile_request = cls(
            api_key=api_key,
            name=name,
            base_url=base_url,
            default_model_name=default_model_name,
            description=description,
            headers=headers,
            model_names=model_names,
            model_settings=model_settings,
            reasoning_model_names=reasoning_model_names,
            source=source,
        )

        create_anthropic_compatible_runtime_profile_request.additional_properties = d
        return create_anthropic_compatible_runtime_profile_request

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
