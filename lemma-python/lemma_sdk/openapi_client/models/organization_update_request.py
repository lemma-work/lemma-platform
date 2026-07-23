from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.organization_join_policy import OrganizationJoinPolicy
from ..types import UNSET, Unset

T = TypeVar("T", bound="OrganizationUpdateRequest")


@_attrs_define
class OrganizationUpdateRequest:
    """Organization update request schema (owner-only).

    Attributes:
        email_domain (None | str | Unset):
        join_policy (None | OrganizationJoinPolicy | Unset):
        name (None | str | Unset):
    """

    email_domain: None | str | Unset = UNSET
    join_policy: None | OrganizationJoinPolicy | Unset = UNSET
    name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        email_domain: None | str | Unset
        if isinstance(self.email_domain, Unset):
            email_domain = UNSET
        else:
            email_domain = self.email_domain

        join_policy: None | str | Unset
        if isinstance(self.join_policy, Unset):
            join_policy = UNSET
        elif isinstance(self.join_policy, OrganizationJoinPolicy):
            join_policy = self.join_policy.value
        else:
            join_policy = self.join_policy

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if email_domain is not UNSET:
            field_dict["email_domain"] = email_domain
        if join_policy is not UNSET:
            field_dict["join_policy"] = join_policy
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_email_domain(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        email_domain = _parse_email_domain(d.pop("email_domain", UNSET))

        def _parse_join_policy(data: object) -> None | OrganizationJoinPolicy | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                join_policy_type_0 = OrganizationJoinPolicy(data)

                return join_policy_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | OrganizationJoinPolicy | Unset, data)

        join_policy = _parse_join_policy(d.pop("join_policy", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        organization_update_request = cls(
            email_domain=email_domain,
            join_policy=join_policy,
            name=name,
        )

        organization_update_request.additional_properties = d
        return organization_update_request

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
