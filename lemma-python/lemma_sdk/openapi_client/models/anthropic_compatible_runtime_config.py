from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.anthropic_compatible_runtime_config_headers import (
        AnthropicCompatibleRuntimeConfigHeaders,
    )
    from ..models.anthropic_compatible_runtime_config_model_settings import (
        AnthropicCompatibleRuntimeConfigModelSettings,
    )


T = TypeVar("T", bound="AnthropicCompatibleRuntimeConfig")


@_attrs_define
class AnthropicCompatibleRuntimeConfig:
    """
    Attributes:
        base_url (None | str | Unset):
        headers (AnthropicCompatibleRuntimeConfigHeaders | Unset):
        model_settings (AnthropicCompatibleRuntimeConfigModelSettings | Unset):
    """

    base_url: None | str | Unset = UNSET
    headers: AnthropicCompatibleRuntimeConfigHeaders | Unset = UNSET
    model_settings: AnthropicCompatibleRuntimeConfigModelSettings | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        base_url: None | str | Unset
        if isinstance(self.base_url, Unset):
            base_url = UNSET
        else:
            base_url = self.base_url

        headers: dict[str, Any] | Unset = UNSET
        if not isinstance(self.headers, Unset):
            headers = self.headers.to_dict()

        model_settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model_settings, Unset):
            model_settings = self.model_settings.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if base_url is not UNSET:
            field_dict["base_url"] = base_url
        if headers is not UNSET:
            field_dict["headers"] = headers
        if model_settings is not UNSET:
            field_dict["model_settings"] = model_settings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.anthropic_compatible_runtime_config_headers import (
            AnthropicCompatibleRuntimeConfigHeaders,
        )
        from ..models.anthropic_compatible_runtime_config_model_settings import (
            AnthropicCompatibleRuntimeConfigModelSettings,
        )

        d = dict(src_dict)

        def _parse_base_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        base_url = _parse_base_url(d.pop("base_url", UNSET))

        _headers = d.pop("headers", UNSET)
        headers: AnthropicCompatibleRuntimeConfigHeaders | Unset
        if isinstance(_headers, Unset):
            headers = UNSET
        else:
            headers = AnthropicCompatibleRuntimeConfigHeaders.from_dict(_headers)

        _model_settings = d.pop("model_settings", UNSET)
        model_settings: AnthropicCompatibleRuntimeConfigModelSettings | Unset
        if isinstance(_model_settings, Unset):
            model_settings = UNSET
        else:
            model_settings = AnthropicCompatibleRuntimeConfigModelSettings.from_dict(
                _model_settings
            )

        anthropic_compatible_runtime_config = cls(
            base_url=base_url,
            headers=headers,
            model_settings=model_settings,
        )

        return anthropic_compatible_runtime_config
