from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OrganizationSlugAvailabilityResponse")


@_attrs_define
class OrganizationSlugAvailabilityResponse:
    """Organization slug availability response.

    ``available`` answers for the slug, which is the handle and is unique across
    the deployment. It is the only field that can refuse a create.

    ``name_available`` is answered whenever a candidate name is passed, and is
    now always ``true``: display names are labels and two organizations may
    share one (PS-ONB-014). Kept so callers that probe both fields keep one
    response shape, and deprecated -- do not gate a create on it.

        Attributes:
            available (bool):
            slug (str):
            name (None | str | Unset):
            name_available (bool | None | Unset): Always true when a name is supplied: organization display names are not
                unique. Gate creates on `available` instead.
    """

    available: bool
    slug: str
    name: None | str | Unset = UNSET
    name_available: bool | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        available = self.available

        slug = self.slug

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        name_available: bool | None | Unset
        if isinstance(self.name_available, Unset):
            name_available = UNSET
        else:
            name_available = self.name_available

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "available": available,
                "slug": slug,
            }
        )
        if name is not UNSET:
            field_dict["name"] = name
        if name_available is not UNSET:
            field_dict["name_available"] = name_available

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        available = d.pop("available")

        slug = d.pop("slug")

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_name_available(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        name_available = _parse_name_available(d.pop("name_available", UNSET))

        organization_slug_availability_response = cls(
            available=available,
            slug=slug,
            name=name,
            name_available=name_available,
        )

        organization_slug_availability_response.additional_properties = d
        return organization_slug_availability_response

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
