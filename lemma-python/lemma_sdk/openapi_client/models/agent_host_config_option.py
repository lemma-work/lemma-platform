from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_host_config_option_metadata import AgentHostConfigOptionMetadata
    from ..models.agent_host_config_option_options_item import (
        AgentHostConfigOptionOptionsItem,
    )


T = TypeVar("T", bound="AgentHostConfigOption")


@_attrs_define
class AgentHostConfigOption:
    """
    Attributes:
        category (str):
        id (str):
        name (str):
        current_value (Any | Unset):
        description (None | str | Unset):
        metadata (AgentHostConfigOptionMetadata | Unset):
        options (list[AgentHostConfigOptionOptionsItem] | Unset):
    """

    category: str
    id: str
    name: str
    current_value: Any | Unset = UNSET
    description: None | str | Unset = UNSET
    metadata: AgentHostConfigOptionMetadata | Unset = UNSET
    options: list[AgentHostConfigOptionOptionsItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        category = self.category

        id = self.id

        name = self.name

        current_value = self.current_value

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        metadata: dict[str, Any] | Unset = UNSET
        if not isinstance(self.metadata, Unset):
            metadata = self.metadata.to_dict()

        options: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.options, Unset):
            options = []
            for options_item_data in self.options:
                options_item = options_item_data.to_dict()
                options.append(options_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "category": category,
                "id": id,
                "name": name,
            }
        )
        if current_value is not UNSET:
            field_dict["current_value"] = current_value
        if description is not UNSET:
            field_dict["description"] = description
        if metadata is not UNSET:
            field_dict["metadata"] = metadata
        if options is not UNSET:
            field_dict["options"] = options

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_host_config_option_metadata import (
            AgentHostConfigOptionMetadata,
        )
        from ..models.agent_host_config_option_options_item import (
            AgentHostConfigOptionOptionsItem,
        )

        d = dict(src_dict)
        category = d.pop("category")

        id = d.pop("id")

        name = d.pop("name")

        current_value = d.pop("current_value", UNSET)

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        _metadata = d.pop("metadata", UNSET)
        metadata: AgentHostConfigOptionMetadata | Unset
        if isinstance(_metadata, Unset):
            metadata = UNSET
        else:
            metadata = AgentHostConfigOptionMetadata.from_dict(_metadata)

        _options = d.pop("options", UNSET)
        options: list[AgentHostConfigOptionOptionsItem] | Unset = UNSET
        if _options is not UNSET:
            options = []
            for options_item_data in _options:
                options_item = AgentHostConfigOptionOptionsItem.from_dict(
                    options_item_data
                )

                options.append(options_item)

        agent_host_config_option = cls(
            category=category,
            id=id,
            name=name,
            current_value=current_value,
            description=description,
            metadata=metadata,
            options=options,
        )

        agent_host_config_option.additional_properties = d
        return agent_host_config_option

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
