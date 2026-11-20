from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.pod_join_policy import PodJoinPolicy
from ..types import UNSET, Unset

T = TypeVar("T", bound="JoinablePodResponse")


@_attrs_define
class JoinablePodResponse:
    """A pod the caller may join and is not in: what to show before they do.

    Attributes:
        id (UUID):
        join_policy (PodJoinPolicy): Who may self-join a pod, ordered from closed to open.
        member_count (int): How many people are in the pod now.
        name (str):
        description (None | str | Unset):
        icon_url (None | str | Unset):
    """

    id: UUID
    join_policy: PodJoinPolicy
    member_count: int
    name: str
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        join_policy = self.join_policy.value

        member_count = self.member_count

        name = self.name

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        icon_url: None | str | Unset
        if isinstance(self.icon_url, Unset):
            icon_url = UNSET
        else:
            icon_url = self.icon_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "join_policy": join_policy,
                "member_count": member_count,
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        join_policy = PodJoinPolicy(d.pop("join_policy"))

        member_count = d.pop("member_count")

        name = d.pop("name")

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_icon_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        icon_url = _parse_icon_url(d.pop("icon_url", UNSET))

        joinable_pod_response = cls(
            id=id,
            join_policy=join_policy,
            member_count=member_count,
            name=name,
            description=description,
            icon_url=icon_url,
        )

        joinable_pod_response.additional_properties = d
        return joinable_pod_response

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
