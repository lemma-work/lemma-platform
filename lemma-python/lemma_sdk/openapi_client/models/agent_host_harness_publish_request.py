from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.agent_host_harness_snapshot import AgentHostHarnessSnapshot


T = TypeVar("T", bound="AgentHostHarnessPublishRequest")


@_attrs_define
class AgentHostHarnessPublishRequest:
    """
    Attributes:
        harnesses (list[AgentHostHarnessSnapshot]):
    """

    harnesses: list[AgentHostHarnessSnapshot]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        harnesses = []
        for harnesses_item_data in self.harnesses:
            harnesses_item = harnesses_item_data.to_dict()
            harnesses.append(harnesses_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "harnesses": harnesses,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_host_harness_snapshot import AgentHostHarnessSnapshot

        d = dict(src_dict)
        harnesses = []
        _harnesses = d.pop("harnesses")
        for harnesses_item_data in _harnesses:
            harnesses_item = AgentHostHarnessSnapshot.from_dict(harnesses_item_data)

            harnesses.append(harnesses_item)

        agent_host_harness_publish_request = cls(
            harnesses=harnesses,
        )

        agent_host_harness_publish_request.additional_properties = d
        return agent_host_harness_publish_request

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
