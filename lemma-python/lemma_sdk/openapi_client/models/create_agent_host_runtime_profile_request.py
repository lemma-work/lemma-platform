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
from attrs import field as _attrs_field

from ..models.runtime_profile_scope import RuntimeProfileScope
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_agent_host_runtime_profile_request_config_selections import (
        CreateAgentHostRuntimeProfileRequestConfigSelections,
    )


T = TypeVar("T", bound="CreateAgentHostRuntimeProfileRequest")


@_attrs_define
class CreateAgentHostRuntimeProfileRequest:
    """
    Attributes:
        host_integration_id (UUID):
        integration_snapshot_revision (str):
        name (str):
        config_selections (CreateAgentHostRuntimeProfileRequestConfigSelections | Unset):
        description (None | str | Unset):
        fallback_profile_id (None | str | Unset):
        host_wait_timeout_seconds (int | Unset):  Default: 300.
        scope (RuntimeProfileScope | Unset):
        source (Literal['AGENT_HOST'] | Unset):  Default: 'AGENT_HOST'.
    """

    host_integration_id: UUID
    integration_snapshot_revision: str
    name: str
    config_selections: CreateAgentHostRuntimeProfileRequestConfigSelections | Unset = (
        UNSET
    )
    description: None | str | Unset = UNSET
    fallback_profile_id: None | str | Unset = UNSET
    host_wait_timeout_seconds: int | Unset = 300
    scope: RuntimeProfileScope | Unset = UNSET
    source: Literal["AGENT_HOST"] | Unset = "AGENT_HOST"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        host_integration_id = str(self.host_integration_id)

        integration_snapshot_revision = self.integration_snapshot_revision

        name = self.name

        config_selections: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config_selections, Unset):
            config_selections = self.config_selections.to_dict()

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

        source = self.source

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "host_integration_id": host_integration_id,
                "integration_snapshot_revision": integration_snapshot_revision,
                "name": name,
            }
        )
        if config_selections is not UNSET:
            field_dict["config_selections"] = config_selections
        if description is not UNSET:
            field_dict["description"] = description
        if fallback_profile_id is not UNSET:
            field_dict["fallback_profile_id"] = fallback_profile_id
        if host_wait_timeout_seconds is not UNSET:
            field_dict["host_wait_timeout_seconds"] = host_wait_timeout_seconds
        if scope is not UNSET:
            field_dict["scope"] = scope
        if source is not UNSET:
            field_dict["source"] = source

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_agent_host_runtime_profile_request_config_selections import (
            CreateAgentHostRuntimeProfileRequestConfigSelections,
        )

        d = dict(src_dict)
        host_integration_id = UUID(d.pop("host_integration_id"))

        integration_snapshot_revision = d.pop("integration_snapshot_revision")

        name = d.pop("name")

        _config_selections = d.pop("config_selections", UNSET)
        config_selections: CreateAgentHostRuntimeProfileRequestConfigSelections | Unset
        if isinstance(_config_selections, Unset):
            config_selections = UNSET
        else:
            config_selections = (
                CreateAgentHostRuntimeProfileRequestConfigSelections.from_dict(
                    _config_selections
                )
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

        source = cast(Literal["AGENT_HOST"] | Unset, d.pop("source", UNSET))
        if source != "AGENT_HOST" and not isinstance(source, Unset):
            raise ValueError(f"source must match const 'AGENT_HOST', got '{source}'")

        create_agent_host_runtime_profile_request = cls(
            host_integration_id=host_integration_id,
            integration_snapshot_revision=integration_snapshot_revision,
            name=name,
            config_selections=config_selections,
            description=description,
            fallback_profile_id=fallback_profile_id,
            host_wait_timeout_seconds=host_wait_timeout_seconds,
            scope=scope,
            source=source,
        )

        create_agent_host_runtime_profile_request.additional_properties = d
        return create_agent_host_runtime_profile_request

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
