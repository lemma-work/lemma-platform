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
    from ..models.update_anthropic_compatible_runtime_profile_request_model_settings_type_0 import (
        UpdateAnthropicCompatibleRuntimeProfileRequestModelSettingsType0,
    )


T = TypeVar("T", bound="UpdateAnthropicCompatibleRuntimeProfileRequest")


@_attrs_define
class UpdateAnthropicCompatibleRuntimeProfileRequest:
    """
    Attributes:
        api_key (None | str | Unset):
        base_url (None | str | Unset):
        default_model_name (None | str | Unset):
        description (None | str | Unset):
        model_names (list[str] | None | Unset):
        model_settings (None | Unset | UpdateAnthropicCompatibleRuntimeProfileRequestModelSettingsType0):
        name (None | str | Unset):
        refresh_models (bool | Unset):  Default: False.
        source (Literal['ANTHROPIC_COMPATIBLE'] | Unset):  Default: 'ANTHROPIC_COMPATIBLE'.
    """

    api_key: None | str | Unset = UNSET
    base_url: None | str | Unset = UNSET
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    model_names: list[str] | None | Unset = UNSET
    model_settings: (
        None | Unset | UpdateAnthropicCompatibleRuntimeProfileRequestModelSettingsType0
    ) = UNSET
    name: None | str | Unset = UNSET
    refresh_models: bool | Unset = False
    source: Literal["ANTHROPIC_COMPATIBLE"] | Unset = "ANTHROPIC_COMPATIBLE"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.update_anthropic_compatible_runtime_profile_request_model_settings_type_0 import (
            UpdateAnthropicCompatibleRuntimeProfileRequestModelSettingsType0,
        )

        api_key: None | str | Unset
        if isinstance(self.api_key, Unset):
            api_key = UNSET
        else:
            api_key = self.api_key

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
            UpdateAnthropicCompatibleRuntimeProfileRequestModelSettingsType0,
        ):
            model_settings = self.model_settings.to_dict()
        else:
            model_settings = self.model_settings

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        refresh_models = self.refresh_models

        source = self.source

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if api_key is not UNSET:
            field_dict["api_key"] = api_key
        if base_url is not UNSET:
            field_dict["base_url"] = base_url
        if default_model_name is not UNSET:
            field_dict["default_model_name"] = default_model_name
        if description is not UNSET:
            field_dict["description"] = description
        if model_names is not UNSET:
            field_dict["model_names"] = model_names
        if model_settings is not UNSET:
            field_dict["model_settings"] = model_settings
        if name is not UNSET:
            field_dict["name"] = name
        if refresh_models is not UNSET:
            field_dict["refresh_models"] = refresh_models
        if source is not UNSET:
            field_dict["source"] = source

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.update_anthropic_compatible_runtime_profile_request_model_settings_type_0 import (
            UpdateAnthropicCompatibleRuntimeProfileRequestModelSettingsType0,
        )

        d = dict(src_dict)

        def _parse_api_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        api_key = _parse_api_key(d.pop("api_key", UNSET))

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
        ) -> (
            None
            | Unset
            | UpdateAnthropicCompatibleRuntimeProfileRequestModelSettingsType0
        ):
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                model_settings_type_0 = UpdateAnthropicCompatibleRuntimeProfileRequestModelSettingsType0.from_dict(
                    data
                )

                return model_settings_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                None
                | Unset
                | UpdateAnthropicCompatibleRuntimeProfileRequestModelSettingsType0,
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

        refresh_models = d.pop("refresh_models", UNSET)

        source = cast(Literal["ANTHROPIC_COMPATIBLE"] | Unset, d.pop("source", UNSET))
        if source != "ANTHROPIC_COMPATIBLE" and not isinstance(source, Unset):
            raise ValueError(
                f"source must match const 'ANTHROPIC_COMPATIBLE', got '{source}'"
            )

        update_anthropic_compatible_runtime_profile_request = cls(
            api_key=api_key,
            base_url=base_url,
            default_model_name=default_model_name,
            description=description,
            model_names=model_names,
            model_settings=model_settings,
            name=name,
            refresh_models=refresh_models,
            source=source,
        )

        update_anthropic_compatible_runtime_profile_request.additional_properties = d
        return update_anthropic_compatible_runtime_profile_request

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
