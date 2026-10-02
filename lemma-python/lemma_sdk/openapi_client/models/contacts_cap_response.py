from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ContactsCapResponse")


@_attrs_define
class ContactsCapResponse:
    """What the organization lets its bots spend answering contacts, a month.

    Attributes:
        organization_id (UUID):
        spent_this_month_usd (float): Spent this calendar month (UTC) answering contacts and people outside the pod in
            groups, on models Lemma provides.
        monthly_limit_usd (float | None | Unset): No cap of the organization's own when absent.
    """

    organization_id: UUID
    spent_this_month_usd: float
    monthly_limit_usd: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        organization_id = str(self.organization_id)

        spent_this_month_usd = self.spent_this_month_usd

        monthly_limit_usd: float | None | Unset
        if isinstance(self.monthly_limit_usd, Unset):
            monthly_limit_usd = UNSET
        else:
            monthly_limit_usd = self.monthly_limit_usd

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "organization_id": organization_id,
                "spent_this_month_usd": spent_this_month_usd,
            }
        )
        if monthly_limit_usd is not UNSET:
            field_dict["monthly_limit_usd"] = monthly_limit_usd

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        organization_id = UUID(d.pop("organization_id"))

        spent_this_month_usd = d.pop("spent_this_month_usd")

        def _parse_monthly_limit_usd(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        monthly_limit_usd = _parse_monthly_limit_usd(d.pop("monthly_limit_usd", UNSET))

        contacts_cap_response = cls(
            organization_id=organization_id,
            spent_this_month_usd=spent_this_month_usd,
            monthly_limit_usd=monthly_limit_usd,
        )

        contacts_cap_response.additional_properties = d
        return contacts_cap_response

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
