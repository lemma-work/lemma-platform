from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.resource_type import ResourceType
from ..types import UNSET, Unset

T = TypeVar("T", bound="ResourcePreviewResponse")


@_attrs_define
class ResourcePreviewResponse:
    """What a shared link may disclose about its target.

    Returned only when the viewer can actually read the resource, so every field
    here is something they could already see by opening it.

        Attributes:
            pod_id (UUID):
            resource_type (ResourceType):
            allowed_actions (list[str] | Unset):
            owner_user_id (None | Unset | UUID):
            resource_id (None | Unset | UUID):
            resource_name (None | str | Unset):
            visibility (None | str | Unset):
    """

    pod_id: UUID
    resource_type: ResourceType
    allowed_actions: list[str] | Unset = UNSET
    owner_user_id: None | Unset | UUID = UNSET
    resource_id: None | Unset | UUID = UNSET
    resource_name: None | str | Unset = UNSET
    visibility: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        pod_id = str(self.pod_id)

        resource_type = self.resource_type.value

        allowed_actions: list[str] | Unset = UNSET
        if not isinstance(self.allowed_actions, Unset):
            allowed_actions = self.allowed_actions

        owner_user_id: None | str | Unset
        if isinstance(self.owner_user_id, Unset):
            owner_user_id = UNSET
        elif isinstance(self.owner_user_id, UUID):
            owner_user_id = str(self.owner_user_id)
        else:
            owner_user_id = self.owner_user_id

        resource_id: None | str | Unset
        if isinstance(self.resource_id, Unset):
            resource_id = UNSET
        elif isinstance(self.resource_id, UUID):
            resource_id = str(self.resource_id)
        else:
            resource_id = self.resource_id

        resource_name: None | str | Unset
        if isinstance(self.resource_name, Unset):
            resource_name = UNSET
        else:
            resource_name = self.resource_name

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "pod_id": pod_id,
                "resource_type": resource_type,
            }
        )
        if allowed_actions is not UNSET:
            field_dict["allowed_actions"] = allowed_actions
        if owner_user_id is not UNSET:
            field_dict["owner_user_id"] = owner_user_id
        if resource_id is not UNSET:
            field_dict["resource_id"] = resource_id
        if resource_name is not UNSET:
            field_dict["resource_name"] = resource_name
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        pod_id = UUID(d.pop("pod_id"))

        resource_type = ResourceType(d.pop("resource_type"))

        allowed_actions = cast(list[str], d.pop("allowed_actions", UNSET))

        def _parse_owner_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                owner_user_id_type_0 = UUID(data)

                return owner_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        owner_user_id = _parse_owner_user_id(d.pop("owner_user_id", UNSET))

        def _parse_resource_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                resource_id_type_0 = UUID(data)

                return resource_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        resource_id = _parse_resource_id(d.pop("resource_id", UNSET))

        def _parse_resource_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        resource_name = _parse_resource_name(d.pop("resource_name", UNSET))

        def _parse_visibility(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        resource_preview_response = cls(
            pod_id=pod_id,
            resource_type=resource_type,
            allowed_actions=allowed_actions,
            owner_user_id=owner_user_id,
            resource_id=resource_id,
            resource_name=resource_name,
            visibility=visibility,
        )

        resource_preview_response.additional_properties = d
        return resource_preview_response

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
