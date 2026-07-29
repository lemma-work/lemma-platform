from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.open_ai_compatible_runtime_config_headers import (
        OpenAICompatibleRuntimeConfigHeaders,
    )
    from ..models.open_ai_compatible_runtime_config_model_settings import (
        OpenAICompatibleRuntimeConfigModelSettings,
    )


T = TypeVar("T", bound="OpenAICompatibleRuntimeConfig")


@_attrs_define
class OpenAICompatibleRuntimeConfig:
    """
    Attributes:
        base_url (str):
        headers (OpenAICompatibleRuntimeConfigHeaders | Unset):
        model_settings (OpenAICompatibleRuntimeConfigModelSettings | Unset):
    """

    base_url: str
    headers: OpenAICompatibleRuntimeConfigHeaders | Unset = UNSET
    model_settings: OpenAICompatibleRuntimeConfigModelSettings | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        base_url = self.base_url

        headers: dict[str, Any] | Unset = UNSET
        if not isinstance(self.headers, Unset):
            headers = self.headers.to_dict()

        model_settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model_settings, Unset):
            model_settings = self.model_settings.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "base_url": base_url,
            }
        )
        if headers is not UNSET:
            field_dict["headers"] = headers
        if model_settings is not UNSET:
            field_dict["model_settings"] = model_settings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.open_ai_compatible_runtime_config_headers import (
            OpenAICompatibleRuntimeConfigHeaders,
        )
        from ..models.open_ai_compatible_runtime_config_model_settings import (
            OpenAICompatibleRuntimeConfigModelSettings,
        )

        d = dict(src_dict)
        base_url = d.pop("base_url")

        _headers = d.pop("headers", UNSET)
        headers: OpenAICompatibleRuntimeConfigHeaders | Unset
        if isinstance(_headers, Unset):
            headers = UNSET
        else:
            headers = OpenAICompatibleRuntimeConfigHeaders.from_dict(_headers)

        _model_settings = d.pop("model_settings", UNSET)
        model_settings: OpenAICompatibleRuntimeConfigModelSettings | Unset
        if isinstance(_model_settings, Unset):
            model_settings = UNSET
        else:
            model_settings = OpenAICompatibleRuntimeConfigModelSettings.from_dict(
                _model_settings
            )

        open_ai_compatible_runtime_config = cls(
            base_url=base_url,
            headers=headers,
            model_settings=model_settings,
        )

        return open_ai_compatible_runtime_config
