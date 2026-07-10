from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.agent_runtime_config import AgentRuntimeConfig
    from ..models.agent_runtime_profile_response import AgentRuntimeProfileResponse


T = TypeVar("T", bound="AgentRuntimeProfileListResponse")


@_attrs_define
class AgentRuntimeProfileListResponse:
    """
    Attributes:
        default_runtime (AgentRuntimeConfig): Select an agent runtime profile and optional catalog model.
        items (list[AgentRuntimeProfileResponse]):
    """

    default_runtime: AgentRuntimeConfig
    items: list[AgentRuntimeProfileResponse]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        default_runtime = self.default_runtime.to_dict()

        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "default_runtime": default_runtime,
                "items": items,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_runtime_config import AgentRuntimeConfig
        from ..models.agent_runtime_profile_response import AgentRuntimeProfileResponse

        d = dict(src_dict)
        default_runtime = AgentRuntimeConfig.from_dict(d.pop("default_runtime"))

        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = AgentRuntimeProfileResponse.from_dict(items_item_data)

            items.append(items_item)

        agent_runtime_profile_list_response = cls(
            default_runtime=default_runtime,
            items=items,
        )

        agent_runtime_profile_list_response.additional_properties = d
        return agent_runtime_profile_list_response

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
