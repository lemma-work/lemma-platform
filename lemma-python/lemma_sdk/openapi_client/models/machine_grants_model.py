from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="MachineGrantsModel")


@_attrs_define
class MachineGrantsModel:
    """Which agents and functions may use a pod machine, by name.

    A workload uses a pod machine only with this grant, and then only as far
    as the person it acts for may. People need none: their pod role decides.

        Attributes:
            agents (list[str] | Unset):
            functions (list[str] | Unset):
    """

    agents: list[str] | Unset = UNSET
    functions: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agents: list[str] | Unset = UNSET
        if not isinstance(self.agents, Unset):
            agents = self.agents

        functions: list[str] | Unset = UNSET
        if not isinstance(self.functions, Unset):
            functions = self.functions

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if agents is not UNSET:
            field_dict["agents"] = agents
        if functions is not UNSET:
            field_dict["functions"] = functions

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agents = cast(list[str], d.pop("agents", UNSET))

        functions = cast(list[str], d.pop("functions", UNSET))

        machine_grants_model = cls(
            agents=agents,
            functions=functions,
        )

        machine_grants_model.additional_properties = d
        return machine_grants_model

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
