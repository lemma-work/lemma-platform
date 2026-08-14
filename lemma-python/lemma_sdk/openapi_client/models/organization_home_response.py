from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.home_pod_response import HomePodResponse


T = TypeVar("T", bound="OrganizationHomeResponse")


@_attrs_define
class OrganizationHomeResponse:
    """One organization's landing page in a single response.

    Attributes:
        name (str):
        organization_id (UUID):
        pods (list[HomePodResponse]):
        role (str):
        slug (None | str | Unset):
    """

    name: str
    organization_id: UUID
    pods: list[HomePodResponse]
    role: str
    slug: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        organization_id = str(self.organization_id)

        pods = []
        for pods_item_data in self.pods:
            pods_item = pods_item_data.to_dict()
            pods.append(pods_item)

        role = self.role

        slug: None | str | Unset
        if isinstance(self.slug, Unset):
            slug = UNSET
        else:
            slug = self.slug

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "organization_id": organization_id,
                "pods": pods,
                "role": role,
            }
        )
        if slug is not UNSET:
            field_dict["slug"] = slug

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.home_pod_response import HomePodResponse

        d = dict(src_dict)
        name = d.pop("name")

        organization_id = UUID(d.pop("organization_id"))

        pods = []
        _pods = d.pop("pods")
        for pods_item_data in _pods:
            pods_item = HomePodResponse.from_dict(pods_item_data)

            pods.append(pods_item)

        role = d.pop("role")

        def _parse_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        slug = _parse_slug(d.pop("slug", UNSET))

        organization_home_response = cls(
            name=name,
            organization_id=organization_id,
            pods=pods,
            role=role,
            slug=slug,
        )

        organization_home_response.additional_properties = d
        return organization_home_response

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
