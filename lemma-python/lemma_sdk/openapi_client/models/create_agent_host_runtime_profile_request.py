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
        harness_id (UUID):
        name (str):
        config_selections (CreateAgentHostRuntimeProfileRequestConfigSelections | Unset):
        default_model_name (None | str | Unset):
        description (None | str | Unset):
        scope (RuntimeProfileScope | Unset):
        source (Literal['AGENT_HOST'] | Unset):  Default: 'AGENT_HOST'.
    """

    harness_id: UUID
    name: str
    config_selections: CreateAgentHostRuntimeProfileRequestConfigSelections | Unset = (
        UNSET
    )
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    scope: RuntimeProfileScope | Unset = UNSET
    source: Literal["AGENT_HOST"] | Unset = "AGENT_HOST"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        harness_id = str(self.harness_id)

        name = self.name

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

        scope: str | Unset = UNSET
        if not isinstance(self.scope, Unset):
            scope = self.scope.value

        source = self.source

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "harness_id": harness_id,
                "name": name,
            }
        )
        if config_selections is not UNSET:
            field_dict["config_selections"] = config_selections
        if default_model_name is not UNSET:
            field_dict["default_model_name"] = default_model_name
        if description is not UNSET:
            field_dict["description"] = description
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
        harness_id = UUID(d.pop("harness_id"))

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
            harness_id=harness_id,
            name=name,
            config_selections=config_selections,
            default_model_name=default_model_name,
            description=description,
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
