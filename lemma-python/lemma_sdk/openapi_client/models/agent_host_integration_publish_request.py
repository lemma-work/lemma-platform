from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.agent_host_integration_snapshot import AgentHostIntegrationSnapshot


T = TypeVar("T", bound="AgentHostIntegrationPublishRequest")


@_attrs_define
class AgentHostIntegrationPublishRequest:
    """
    Attributes:
        integrations (list[AgentHostIntegrationSnapshot]):
    """

    integrations: list[AgentHostIntegrationSnapshot]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        integrations = []
        for integrations_item_data in self.integrations:
            integrations_item = integrations_item_data.to_dict()
            integrations.append(integrations_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "integrations": integrations,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_host_integration_snapshot import (
            AgentHostIntegrationSnapshot,
        )

        d = dict(src_dict)
        integrations = []
        _integrations = d.pop("integrations")
        for integrations_item_data in _integrations:
            integrations_item = AgentHostIntegrationSnapshot.from_dict(
                integrations_item_data
            )

            integrations.append(integrations_item)

        agent_host_integration_publish_request = cls(
            integrations=integrations,
        )

        agent_host_integration_publish_request.additional_properties = d
        return agent_host_integration_publish_request

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
