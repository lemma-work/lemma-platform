from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)
from uuid import UUID

from attrs import define as _attrs_define

from ..models.runtime_profile_scope import RuntimeProfileScope
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_harness_runtime_profile_request_config_selections import (
        CreateHarnessRuntimeProfileRequestConfigSelections,
    )


T = TypeVar("T", bound="CreateHarnessRuntimeProfileRequest")


@_attrs_define
class CreateHarnessRuntimeProfileRequest:
    """
    Attributes:
        harness_id (UUID):
        harness_snapshot_revision (str):
        name (str):
        runtime_type (Literal['HARNESS']):
        config_selections (CreateHarnessRuntimeProfileRequestConfigSelections | Unset):
        default_model_name (None | str | Unset):
        description (None | str | Unset):
        fallback_profile_id (None | str | Unset):
        host_wait_timeout_seconds (int | Unset):  Default: 300.
        scope (RuntimeProfileScope | Unset):
    """

    harness_id: UUID
    harness_snapshot_revision: str
    name: str
    runtime_type: Literal["HARNESS"]
    config_selections: CreateHarnessRuntimeProfileRequestConfigSelections | Unset = (
        UNSET
    )
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    fallback_profile_id: None | str | Unset = UNSET
    host_wait_timeout_seconds: int | Unset = 300
    scope: RuntimeProfileScope | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        harness_id = str(self.harness_id)

        harness_snapshot_revision = self.harness_snapshot_revision

        name = self.name

        runtime_type = self.runtime_type

        config_selections: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config_selections, Unset):
            config_selections = self.config_selections.to_dict()

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

        fallback_profile_id: None | str | Unset
        if isinstance(self.fallback_profile_id, Unset):
            fallback_profile_id = UNSET
        else:
            fallback_profile_id = self.fallback_profile_id

        host_wait_timeout_seconds = self.host_wait_timeout_seconds

        scope: str | Unset = UNSET
        if not isinstance(self.scope, Unset):
            scope = self.scope.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "harness_id": harness_id,
                "harness_snapshot_revision": harness_snapshot_revision,
                "name": name,
                "runtime_type": runtime_type,
            }
        )
        if config_selections is not UNSET:
            field_dict["config_selections"] = config_selections
        if default_model_name is not UNSET:
            field_dict["default_model_name"] = default_model_name
        if description is not UNSET:
            field_dict["description"] = description
        if fallback_profile_id is not UNSET:
            field_dict["fallback_profile_id"] = fallback_profile_id
        if host_wait_timeout_seconds is not UNSET:
            field_dict["host_wait_timeout_seconds"] = host_wait_timeout_seconds
        if scope is not UNSET:
            field_dict["scope"] = scope

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_harness_runtime_profile_request_config_selections import (
            CreateHarnessRuntimeProfileRequestConfigSelections,
        )

        d = dict(src_dict)
        harness_id = UUID(d.pop("harness_id"))

        harness_snapshot_revision = d.pop("harness_snapshot_revision")

        name = d.pop("name")

        runtime_type = cast(Literal["HARNESS"], d.pop("runtime_type"))
        if runtime_type != "HARNESS":
            raise ValueError(
                f"runtime_type must match const 'HARNESS', got '{runtime_type}'"
            )

        _config_selections = d.pop("config_selections", UNSET)
        config_selections: CreateHarnessRuntimeProfileRequestConfigSelections | Unset
        if isinstance(_config_selections, Unset):
            config_selections = UNSET
        else:
            config_selections = (
                CreateHarnessRuntimeProfileRequestConfigSelections.from_dict(
                    _config_selections
                )
            )

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

        def _parse_fallback_profile_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        fallback_profile_id = _parse_fallback_profile_id(
            d.pop("fallback_profile_id", UNSET)
        )

        host_wait_timeout_seconds = d.pop("host_wait_timeout_seconds", UNSET)

        _scope = d.pop("scope", UNSET)
        scope: RuntimeProfileScope | Unset
        if isinstance(_scope, Unset):
            scope = UNSET
        else:
            scope = RuntimeProfileScope(_scope)

        create_harness_runtime_profile_request = cls(
            harness_id=harness_id,
            harness_snapshot_revision=harness_snapshot_revision,
            name=name,
            runtime_type=runtime_type,
            config_selections=config_selections,
            default_model_name=default_model_name,
            description=description,
            fallback_profile_id=fallback_profile_id,
            host_wait_timeout_seconds=host_wait_timeout_seconds,
            scope=scope,
        )

        return create_harness_runtime_profile_request
