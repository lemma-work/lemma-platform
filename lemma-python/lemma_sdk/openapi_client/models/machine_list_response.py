from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.machine_response import MachineResponse


T = TypeVar("T", bound="MachineListResponse")


@_attrs_define
class MachineListResponse:
    """
    Attributes:
        machines (list[MachineResponse]):
    """

    machines: list[MachineResponse]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        machines = []
        for machines_item_data in self.machines:
            machines_item = machines_item_data.to_dict()
            machines.append(machines_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "machines": machines,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.machine_response import MachineResponse

        d = dict(src_dict)
        machines = []
        _machines = d.pop("machines")
        for machines_item_data in _machines:
            machines_item = MachineResponse.from_dict(machines_item_data)

            machines.append(machines_item)

        machine_list_response = cls(
            machines=machines,
        )

        machine_list_response.additional_properties = d
        return machine_list_response

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
