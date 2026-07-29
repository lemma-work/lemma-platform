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
        runtime_type (Literal['OPENAI_COMPATIBLE']):
        api_key (None | str | Unset):
        default_model_name (None | str | Unset):
        description (None | str | Unset):
        headers (CreateOpenAICompatibleRuntimeProfileRequestHeaders | Unset):
        model_names (list[str] | Unset):
        model_settings (CreateOpenAICompatibleRuntimeProfileRequestModelSettings | Unset):
        scope (RuntimeProfileScope | Unset):
    """

    base_url: str
    name: str
    runtime_type: Literal["OPENAI_COMPATIBLE"]
    api_key: None | str | Unset = UNSET
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    headers: CreateOpenAICompatibleRuntimeProfileRequestHeaders | Unset = UNSET
    model_names: list[str] | Unset = UNSET
    model_settings: CreateOpenAICompatibleRuntimeProfileRequestModelSettings | Unset = (
        UNSET
    )
    scope: RuntimeProfileScope | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        base_url = self.base_url

        name = self.name

        runtime_type = self.runtime_type

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

        scope: str | Unset = UNSET
        if not isinstance(self.scope, Unset):
            scope = self.scope.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "base_url": base_url,
                "name": name,
                "runtime_type": runtime_type,
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
        if scope is not UNSET:
            field_dict["scope"] = scope

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

        runtime_type = cast(Literal["OPENAI_COMPATIBLE"], d.pop("runtime_type"))
        if runtime_type != "OPENAI_COMPATIBLE":
            raise ValueError(
                f"runtime_type must match const 'OPENAI_COMPATIBLE', got '{runtime_type}'"
            )

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

        _scope = d.pop("scope", UNSET)
        scope: RuntimeProfileScope | Unset
        if isinstance(_scope, Unset):
            scope = UNSET
        else:
            scope = RuntimeProfileScope(_scope)

        create_open_ai_compatible_runtime_profile_request = cls(
            base_url=base_url,
            name=name,
            runtime_type=runtime_type,
            api_key=api_key,
            default_model_name=default_model_name,
            description=description,
            headers=headers,
            model_names=model_names,
            model_settings=model_settings,
            scope=scope,
        )

        return create_open_ai_compatible_runtime_profile_request
