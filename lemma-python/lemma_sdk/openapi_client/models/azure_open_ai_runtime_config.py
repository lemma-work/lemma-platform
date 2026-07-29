from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.azure_open_ai_runtime_config_model_settings import (
        AzureOpenAIRuntimeConfigModelSettings,
    )


T = TypeVar("T", bound="AzureOpenAIRuntimeConfig")


@_attrs_define
class AzureOpenAIRuntimeConfig:
    """
    Attributes:
        azure_endpoint (str):
        api_version (None | str | Unset):
        model_settings (AzureOpenAIRuntimeConfigModelSettings | Unset):
    """

    azure_endpoint: str
    api_version: None | str | Unset = UNSET
    model_settings: AzureOpenAIRuntimeConfigModelSettings | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        azure_endpoint = self.azure_endpoint

        api_version: None | str | Unset
        if isinstance(self.api_version, Unset):
            api_version = UNSET
        else:
            api_version = self.api_version

        model_settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model_settings, Unset):
            model_settings = self.model_settings.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "azure_endpoint": azure_endpoint,
            }
        )
        if api_version is not UNSET:
            field_dict["api_version"] = api_version
        if model_settings is not UNSET:
            field_dict["model_settings"] = model_settings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.azure_open_ai_runtime_config_model_settings import (
            AzureOpenAIRuntimeConfigModelSettings,
        )

        d = dict(src_dict)
        azure_endpoint = d.pop("azure_endpoint")

        def _parse_api_version(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        api_version = _parse_api_version(d.pop("api_version", UNSET))

        _model_settings = d.pop("model_settings", UNSET)
        model_settings: AzureOpenAIRuntimeConfigModelSettings | Unset
        if isinstance(_model_settings, Unset):
            model_settings = UNSET
        else:
            model_settings = AzureOpenAIRuntimeConfigModelSettings.from_dict(
                _model_settings
            )

        azure_open_ai_runtime_config = cls(
            azure_endpoint=azure_endpoint,
            api_version=api_version,
            model_settings=model_settings,
        )

        return azure_open_ai_runtime_config
