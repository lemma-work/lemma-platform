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
    from ..models.create_google_vertex_runtime_profile_request_model_settings import (
        CreateGoogleVertexRuntimeProfileRequestModelSettings,
    )
    from ..models.create_google_vertex_runtime_profile_request_service_account_json_type_0 import (
        CreateGoogleVertexRuntimeProfileRequestServiceAccountJsonType0,
    )


T = TypeVar("T", bound="CreateGoogleVertexRuntimeProfileRequest")


@_attrs_define
class CreateGoogleVertexRuntimeProfileRequest:
    """
    Attributes:
        default_model_name (str):
        location (str):
        model_names (list[str]):
        name (str):
        project_id (str):
        runtime_type (Literal['GOOGLE_VERTEX']):
        description (None | str | Unset):
        model_settings (CreateGoogleVertexRuntimeProfileRequestModelSettings | Unset):
        scope (RuntimeProfileScope | Unset):
        service_account_json (CreateGoogleVertexRuntimeProfileRequestServiceAccountJsonType0 | None | Unset):
    """

    default_model_name: str
    location: str
    model_names: list[str]
    name: str
    project_id: str
    runtime_type: Literal["GOOGLE_VERTEX"]
    description: None | str | Unset = UNSET
    model_settings: CreateGoogleVertexRuntimeProfileRequestModelSettings | Unset = UNSET
    scope: RuntimeProfileScope | Unset = UNSET
    service_account_json: (
        CreateGoogleVertexRuntimeProfileRequestServiceAccountJsonType0 | None | Unset
    ) = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.create_google_vertex_runtime_profile_request_service_account_json_type_0 import (
            CreateGoogleVertexRuntimeProfileRequestServiceAccountJsonType0,
        )

        default_model_name = self.default_model_name

        location = self.location

        model_names = self.model_names

        name = self.name

        project_id = self.project_id

        runtime_type = self.runtime_type

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

        service_account_json: dict[str, Any] | None | Unset
        if isinstance(self.service_account_json, Unset):
            service_account_json = UNSET
        elif isinstance(
            self.service_account_json,
            CreateGoogleVertexRuntimeProfileRequestServiceAccountJsonType0,
        ):
            service_account_json = self.service_account_json.to_dict()
        else:
            service_account_json = self.service_account_json

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "default_model_name": default_model_name,
                "location": location,
                "model_names": model_names,
                "name": name,
                "project_id": project_id,
                "runtime_type": runtime_type,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if model_settings is not UNSET:
            field_dict["model_settings"] = model_settings
        if scope is not UNSET:
            field_dict["scope"] = scope
        if service_account_json is not UNSET:
            field_dict["service_account_json"] = service_account_json

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_google_vertex_runtime_profile_request_model_settings import (
            CreateGoogleVertexRuntimeProfileRequestModelSettings,
        )
        from ..models.create_google_vertex_runtime_profile_request_service_account_json_type_0 import (
            CreateGoogleVertexRuntimeProfileRequestServiceAccountJsonType0,
        )

        d = dict(src_dict)
        default_model_name = d.pop("default_model_name")

        location = d.pop("location")

        model_names = cast(list[str], d.pop("model_names"))

        name = d.pop("name")

        project_id = d.pop("project_id")

        runtime_type = cast(Literal["GOOGLE_VERTEX"], d.pop("runtime_type"))
        if runtime_type != "GOOGLE_VERTEX":
            raise ValueError(
                f"runtime_type must match const 'GOOGLE_VERTEX', got '{runtime_type}'"
            )

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        _model_settings = d.pop("model_settings", UNSET)
        model_settings: CreateGoogleVertexRuntimeProfileRequestModelSettings | Unset
        if isinstance(_model_settings, Unset):
            model_settings = UNSET
        else:
            model_settings = (
                CreateGoogleVertexRuntimeProfileRequestModelSettings.from_dict(
                    _model_settings
                )
            )

        _scope = d.pop("scope", UNSET)
        scope: RuntimeProfileScope | Unset
        if isinstance(_scope, Unset):
            scope = UNSET
        else:
            scope = RuntimeProfileScope(_scope)

        def _parse_service_account_json(
            data: object,
        ) -> (
            CreateGoogleVertexRuntimeProfileRequestServiceAccountJsonType0
            | None
            | Unset
        ):
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                service_account_json_type_0 = CreateGoogleVertexRuntimeProfileRequestServiceAccountJsonType0.from_dict(
                    data
                )

                return service_account_json_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                CreateGoogleVertexRuntimeProfileRequestServiceAccountJsonType0
                | None
                | Unset,
                data,
            )

        service_account_json = _parse_service_account_json(
            d.pop("service_account_json", UNSET)
        )

        create_google_vertex_runtime_profile_request = cls(
            default_model_name=default_model_name,
            location=location,
            model_names=model_names,
            name=name,
            project_id=project_id,
            runtime_type=runtime_type,
            description=description,
            model_settings=model_settings,
            scope=scope,
            service_account_json=service_account_json,
        )

        return create_google_vertex_runtime_profile_request
