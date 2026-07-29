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

from ..models.runtime_profile_scope import RuntimeProfileScope
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_azure_open_ai_runtime_profile_request_model_settings import (
        CreateAzureOpenAIRuntimeProfileRequestModelSettings,
    )


T = TypeVar("T", bound="CreateAzureOpenAIRuntimeProfileRequest")


@_attrs_define
class CreateAzureOpenAIRuntimeProfileRequest:
    """
    Attributes:
        api_key (str):
        azure_endpoint (str):
        default_model_name (str):
        model_names (list[str]):
        name (str):
        runtime_type (Literal['AZURE_OPENAI']):
        api_version (None | str | Unset):
        description (None | str | Unset):
        model_settings (CreateAzureOpenAIRuntimeProfileRequestModelSettings | Unset):
        scope (RuntimeProfileScope | Unset):
    """

    api_key: str
    azure_endpoint: str
    default_model_name: str
    model_names: list[str]
    name: str
    runtime_type: Literal["AZURE_OPENAI"]
    api_version: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    model_settings: CreateAzureOpenAIRuntimeProfileRequestModelSettings | Unset = UNSET
    scope: RuntimeProfileScope | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        api_key = self.api_key

        azure_endpoint = self.azure_endpoint

        default_model_name = self.default_model_name

        model_names = self.model_names

        name = self.name

        runtime_type = self.runtime_type

        api_version: None | str | Unset
        if isinstance(self.api_version, Unset):
            api_version = UNSET
        else:
            api_version = self.api_version

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        model_settings: dict[str, Any] | Unset = UNSET
        if not isinstance(self.model_settings, Unset):
            model_settings = self.model_settings.to_dict()

        scope: str | Unset = UNSET
        if not isinstance(self.scope, Unset):
            scope = self.scope.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "api_key": api_key,
                "azure_endpoint": azure_endpoint,
                "default_model_name": default_model_name,
                "model_names": model_names,
                "name": name,
                "runtime_type": runtime_type,
            }
        )
        if api_version is not UNSET:
            field_dict["api_version"] = api_version
        if description is not UNSET:
            field_dict["description"] = description
        if model_settings is not UNSET:
            field_dict["model_settings"] = model_settings
        if scope is not UNSET:
            field_dict["scope"] = scope

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_azure_open_ai_runtime_profile_request_model_settings import (
            CreateAzureOpenAIRuntimeProfileRequestModelSettings,
        )

        d = dict(src_dict)
        api_key = d.pop("api_key")

        azure_endpoint = d.pop("azure_endpoint")

        default_model_name = d.pop("default_model_name")

        model_names = cast(list[str], d.pop("model_names"))

        name = d.pop("name")

        runtime_type = cast(Literal["AZURE_OPENAI"], d.pop("runtime_type"))
        if runtime_type != "AZURE_OPENAI":
            raise ValueError(
                f"runtime_type must match const 'AZURE_OPENAI', got '{runtime_type}'"
            )

        def _parse_api_version(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        api_version = _parse_api_version(d.pop("api_version", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        _model_settings = d.pop("model_settings", UNSET)
        model_settings: CreateAzureOpenAIRuntimeProfileRequestModelSettings | Unset
        if isinstance(_model_settings, Unset):
            model_settings = UNSET
        else:
            model_settings = (
                CreateAzureOpenAIRuntimeProfileRequestModelSettings.from_dict(
                    _model_settings
                )
            )

        _scope = d.pop("scope", UNSET)
        scope: RuntimeProfileScope | Unset
        if isinstance(_scope, Unset):
            scope = UNSET
        else:
            scope = RuntimeProfileScope(_scope)

        create_azure_open_ai_runtime_profile_request = cls(
            api_key=api_key,
            azure_endpoint=azure_endpoint,
            default_model_name=default_model_name,
            model_names=model_names,
            name=name,
            runtime_type=runtime_type,
            api_version=api_version,
            description=description,
            model_settings=model_settings,
            scope=scope,
        )

        return create_azure_open_ai_runtime_profile_request
