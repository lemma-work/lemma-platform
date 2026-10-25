from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.grantee_ref_grantee_type import GranteeRefGranteeType

T = TypeVar("T", bound="GranteeRef")


@_attrs_define
class GranteeRef:
    """
    Attributes:
        grantee_id (UUID):
        grantee_type (GranteeRefGranteeType):
    """

    grantee_id: UUID
    grantee_type: GranteeRefGranteeType
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        grantee_id = str(self.grantee_id)

        grantee_type = self.grantee_type.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "grantee_id": grantee_id,
                "grantee_type": grantee_type,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        grantee_id = UUID(d.pop("grantee_id"))

        grantee_type = GranteeRefGranteeType(d.pop("grantee_type"))

        grantee_ref = cls(
            grantee_id=grantee_id,
            grantee_type=grantee_type,
        )

        grantee_ref.additional_properties = d
        return grantee_ref

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
