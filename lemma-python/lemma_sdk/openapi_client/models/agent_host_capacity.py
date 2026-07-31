from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="AgentHostCapacity")


@_attrs_define
class AgentHostCapacity:
    """
    Attributes:
        active_runs (int | Unset):  Default: 0.
        available_runs (int | Unset):  Default: 1.
        max_runs (int | Unset):  Default: 1.
    """

    active_runs: int | Unset = 0
    available_runs: int | Unset = 1
    max_runs: int | Unset = 1
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        active_runs = self.active_runs

        available_runs = self.available_runs

        max_runs = self.max_runs

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if active_runs is not UNSET:
            field_dict["active_runs"] = active_runs
        if available_runs is not UNSET:
            field_dict["available_runs"] = available_runs
        if max_runs is not UNSET:
            field_dict["max_runs"] = max_runs

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        active_runs = d.pop("active_runs", UNSET)

        available_runs = d.pop("available_runs", UNSET)

        max_runs = d.pop("max_runs", UNSET)

        agent_host_capacity = cls(
            active_runs=active_runs,
            available_runs=available_runs,
            max_runs=max_runs,
        )

        agent_host_capacity.additional_properties = d
        return agent_host_capacity

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
