from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.shared_resource_body import SharedResourceBody


T = TypeVar("T", bound="PodLinkResponse")


@_attrs_define
class PodLinkResponse:
    """
    Attributes:
        name (str):
        pod_id (UUID): The other pod.
        shared (list[SharedResourceBody]):
        description (None | str | Unset):
        icon_url (None | str | Unset):
        steward_name (None | str | Unset):
        steward_user_id (None | Unset | UUID): Who connected it, and looks after it.
    """

    name: str
    pod_id: UUID
    shared: list[SharedResourceBody]
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    steward_name: None | str | Unset = UNSET
    steward_user_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        pod_id = str(self.pod_id)

        shared = []
        for shared_item_data in self.shared:
            shared_item = shared_item_data.to_dict()
            shared.append(shared_item)

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

        steward_name: None | str | Unset
        if isinstance(self.steward_name, Unset):
            steward_name = UNSET
        else:
            steward_name = self.steward_name

        steward_user_id: None | str | Unset
        if isinstance(self.steward_user_id, Unset):
            steward_user_id = UNSET
        elif isinstance(self.steward_user_id, UUID):
            steward_user_id = str(self.steward_user_id)
        else:
            steward_user_id = self.steward_user_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "pod_id": pod_id,
                "shared": shared,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if steward_name is not UNSET:
            field_dict["steward_name"] = steward_name
        if steward_user_id is not UNSET:
            field_dict["steward_user_id"] = steward_user_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.shared_resource_body import SharedResourceBody

        d = dict(src_dict)
        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        shared = []
        _shared = d.pop("shared")
        for shared_item_data in _shared:
            shared_item = SharedResourceBody.from_dict(shared_item_data)

            shared.append(shared_item)

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

        def _parse_steward_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        steward_name = _parse_steward_name(d.pop("steward_name", UNSET))

        def _parse_steward_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                steward_user_id_type_0 = UUID(data)

                return steward_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        steward_user_id = _parse_steward_user_id(d.pop("steward_user_id", UNSET))

        pod_link_response = cls(
            name=name,
            pod_id=pod_id,
            shared=shared,
            description=description,
            icon_url=icon_url,
            steward_name=steward_name,
            steward_user_id=steward_user_id,
        )

        pod_link_response.additional_properties = d
        return pod_link_response

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
