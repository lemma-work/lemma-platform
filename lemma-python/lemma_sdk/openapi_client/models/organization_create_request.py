from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.organization_join_policy import OrganizationJoinPolicy
from ..types import UNSET, Unset

T = TypeVar("T", bound="OrganizationCreateRequest")


@_attrs_define
class OrganizationCreateRequest:
    """Organization creation request schema.

    Attributes:
        name (str):
        email_domain (None | str | Unset):
        join_policy (OrganizationJoinPolicy | Unset): Who may self-join an organization, ordered from closed to open.
        slug (None | str | Unset):
    """

    name: str
    email_domain: None | str | Unset = UNSET
    join_policy: OrganizationJoinPolicy | Unset = UNSET
    slug: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        email_domain: None | str | Unset
        if isinstance(self.email_domain, Unset):
            email_domain = UNSET
        else:
            email_domain = self.email_domain

        join_policy: str | Unset = UNSET
        if not isinstance(self.join_policy, Unset):
            join_policy = self.join_policy.value

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
            }
        )
        if email_domain is not UNSET:
            field_dict["email_domain"] = email_domain
        if join_policy is not UNSET:
            field_dict["join_policy"] = join_policy
        if slug is not UNSET:
            field_dict["slug"] = slug

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        def _parse_email_domain(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        email_domain = _parse_email_domain(d.pop("email_domain", UNSET))

        _join_policy = d.pop("join_policy", UNSET)
        join_policy: OrganizationJoinPolicy | Unset
        if isinstance(_join_policy, Unset):
            join_policy = UNSET
        else:
            join_policy = OrganizationJoinPolicy(_join_policy)

        def _parse_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        slug = _parse_slug(d.pop("slug", UNSET))

        organization_create_request = cls(
            name=name,
            email_domain=email_domain,
            join_policy=join_policy,
            slug=slug,
        )

        organization_create_request.additional_properties = d
        return organization_create_request

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
