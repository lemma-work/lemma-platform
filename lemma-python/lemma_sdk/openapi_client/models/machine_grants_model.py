from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="MachineGrantsModel")


@_attrs_define
class MachineGrantsModel:
    """Who may use a pod machine beyond what visibility already allows.

    Agents and functions by name; people by user id. A RESTRICTED machine is
    usable only by those named here (and pod admins).

        Attributes:
            agents (list[str] | Unset):
            functions (list[str] | Unset):
            members (list[UUID] | Unset):
    """

    agents: list[str] | Unset = UNSET
    functions: list[str] | Unset = UNSET
    members: list[UUID] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agents: list[str] | Unset = UNSET
        if not isinstance(self.agents, Unset):
            agents = self.agents

        functions: list[str] | Unset = UNSET
        if not isinstance(self.functions, Unset):
            functions = self.functions

        members: list[str] | Unset = UNSET
        if not isinstance(self.members, Unset):
            members = []
            for members_item_data in self.members:
                members_item = str(members_item_data)
                members.append(members_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if agents is not UNSET:
            field_dict["agents"] = agents
        if functions is not UNSET:
            field_dict["functions"] = functions
        if members is not UNSET:
            field_dict["members"] = members

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agents = cast(list[str], d.pop("agents", UNSET))

        functions = cast(list[str], d.pop("functions", UNSET))

        _members = d.pop("members", UNSET)
        members: list[UUID] | Unset = UNSET
        if _members is not UNSET:
            members = []
            for members_item_data in _members:
                members_item = UUID(members_item_data)

                members.append(members_item)

        machine_grants_model = cls(
            agents=agents,
            functions=functions,
            members=members,
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
