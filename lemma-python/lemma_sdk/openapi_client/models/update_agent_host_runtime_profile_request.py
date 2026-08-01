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
    from ..models.update_agent_host_runtime_profile_request_config_selections_type_0 import (
        UpdateAgentHostRuntimeProfileRequestConfigSelectionsType0,
    )


T = TypeVar("T", bound="UpdateAgentHostRuntimeProfileRequest")


@_attrs_define
class UpdateAgentHostRuntimeProfileRequest:
    """
    Attributes:
        config_selections (None | Unset | UpdateAgentHostRuntimeProfileRequestConfigSelectionsType0):
        default_model_name (None | str | Unset):
        description (None | str | Unset):
        host_wait_timeout_seconds (int | None | Unset):
        name (None | str | Unset):
        source (Literal['AGENT_HOST'] | Unset):  Default: 'AGENT_HOST'.
    """

    config_selections: (
        None | Unset | UpdateAgentHostRuntimeProfileRequestConfigSelectionsType0
    ) = UNSET
    default_model_name: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    host_wait_timeout_seconds: int | None | Unset = UNSET
    name: None | str | Unset = UNSET
    source: Literal["AGENT_HOST"] | Unset = "AGENT_HOST"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.update_agent_host_runtime_profile_request_config_selections_type_0 import (
            UpdateAgentHostRuntimeProfileRequestConfigSelectionsType0,
        )

        config_selections: dict[str, Any] | None | Unset
        if isinstance(self.config_selections, Unset):
            config_selections = UNSET
        elif isinstance(
            self.config_selections,
            UpdateAgentHostRuntimeProfileRequestConfigSelectionsType0,
        ):
            config_selections = self.config_selections.to_dict()
        else:
            config_selections = self.config_selections

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

        host_wait_timeout_seconds: int | None | Unset
        if isinstance(self.host_wait_timeout_seconds, Unset):
            host_wait_timeout_seconds = UNSET
        else:
            host_wait_timeout_seconds = self.host_wait_timeout_seconds

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        source = self.source

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if config_selections is not UNSET:
            field_dict["config_selections"] = config_selections
        if default_model_name is not UNSET:
            field_dict["default_model_name"] = default_model_name
        if description is not UNSET:
            field_dict["description"] = description
        if host_wait_timeout_seconds is not UNSET:
            field_dict["host_wait_timeout_seconds"] = host_wait_timeout_seconds
        if name is not UNSET:
            field_dict["name"] = name
        if source is not UNSET:
            field_dict["source"] = source

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.update_agent_host_runtime_profile_request_config_selections_type_0 import (
            UpdateAgentHostRuntimeProfileRequestConfigSelectionsType0,
        )

        d = dict(src_dict)

        def _parse_config_selections(
            data: object,
        ) -> None | Unset | UpdateAgentHostRuntimeProfileRequestConfigSelectionsType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_selections_type_0 = (
                    UpdateAgentHostRuntimeProfileRequestConfigSelectionsType0.from_dict(
                        data
                    )
                )

                return config_selections_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                None
                | Unset
                | UpdateAgentHostRuntimeProfileRequestConfigSelectionsType0,
                data,
            )

        config_selections = _parse_config_selections(d.pop("config_selections", UNSET))

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

        def _parse_host_wait_timeout_seconds(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        host_wait_timeout_seconds = _parse_host_wait_timeout_seconds(
            d.pop("host_wait_timeout_seconds", UNSET)
        )

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        source = cast(Literal["AGENT_HOST"] | Unset, d.pop("source", UNSET))
        if source != "AGENT_HOST" and not isinstance(source, Unset):
            raise ValueError(f"source must match const 'AGENT_HOST', got '{source}'")

        update_agent_host_runtime_profile_request = cls(
            config_selections=config_selections,
            default_model_name=default_model_name,
            description=description,
            host_wait_timeout_seconds=host_wait_timeout_seconds,
            name=name,
            source=source,
        )

        update_agent_host_runtime_profile_request.additional_properties = d
        return update_agent_host_runtime_profile_request

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
