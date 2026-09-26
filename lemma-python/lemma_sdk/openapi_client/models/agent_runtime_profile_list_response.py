from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

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
        organization_default_runtime (AgentRuntimeConfig | None | Unset):
    """

    default_runtime: AgentRuntimeConfig
    items: list[AgentRuntimeProfileResponse]
    organization_default_runtime: AgentRuntimeConfig | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_runtime_config import AgentRuntimeConfig

        default_runtime = self.default_runtime.to_dict()

        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        organization_default_runtime: dict[str, Any] | None | Unset
        if isinstance(self.organization_default_runtime, Unset):
            organization_default_runtime = UNSET
        elif isinstance(self.organization_default_runtime, AgentRuntimeConfig):
            organization_default_runtime = self.organization_default_runtime.to_dict()
        else:
            organization_default_runtime = self.organization_default_runtime

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "default_runtime": default_runtime,
                "items": items,
            }
        )
        if organization_default_runtime is not UNSET:
            field_dict["organization_default_runtime"] = organization_default_runtime

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

        def _parse_organization_default_runtime(
            data: object,
        ) -> AgentRuntimeConfig | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                organization_default_runtime_type_0 = AgentRuntimeConfig.from_dict(data)

                return organization_default_runtime_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentRuntimeConfig | None | Unset, data)

        organization_default_runtime = _parse_organization_default_runtime(
            d.pop("organization_default_runtime", UNSET)
        )

        agent_runtime_profile_list_response = cls(
            default_runtime=default_runtime,
            items=items,
            organization_default_runtime=organization_default_runtime,
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
