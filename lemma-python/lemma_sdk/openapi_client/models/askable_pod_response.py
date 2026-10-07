from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="AskablePodResponse")


@_attrs_define
class AskablePodResponse:
    """
    Attributes:
        connected (bool): It linked itself to this pod, so it can be asked with nobody present.
        name (str):
        pod_id (UUID):
        through_you (bool): The caller is in it too, so it can be asked as them.
        description (None | str | Unset): What the pod is for.
        icon_url (None | str | Unset):
    """

    connected: bool
    name: str
    pod_id: UUID
    through_you: bool
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connected = self.connected

        name = self.name

        pod_id = str(self.pod_id)

        through_you = self.through_you

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
                "connected": connected,
                "name": name,
                "pod_id": pod_id,
                "through_you": through_you,
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
        connected = d.pop("connected")

        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        through_you = d.pop("through_you")

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

        askable_pod_response = cls(
            connected=connected,
            name=name,
            pod_id=pod_id,
            through_you=through_you,
            description=description,
            icon_url=icon_url,
        )

        askable_pod_response.additional_properties = d
        return askable_pod_response

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
