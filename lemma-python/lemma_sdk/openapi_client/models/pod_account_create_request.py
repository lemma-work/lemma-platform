from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.pod_account_visibility import PodAccountVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.grantee_ref import GranteeRef


T = TypeVar("T", bound="PodAccountCreateRequest")


@_attrs_define
class PodAccountCreateRequest:
    """
    Attributes:
        account_id (UUID): One of your own accounts in this organization.
        share_with (list[GranteeRef] | Unset): RESTRICTED only: the members or roles who may use it.
        visibility (PodAccountVisibility | Unset):
    """

    account_id: UUID
    share_with: list[GranteeRef] | Unset = UNSET
    visibility: PodAccountVisibility | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        account_id = str(self.account_id)

        share_with: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.share_with, Unset):
            share_with = []
            for share_with_item_data in self.share_with:
                share_with_item = share_with_item_data.to_dict()
                share_with.append(share_with_item)

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "account_id": account_id,
            }
        )
        if share_with is not UNSET:
            field_dict["share_with"] = share_with
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.grantee_ref import GranteeRef

        d = dict(src_dict)
        account_id = UUID(d.pop("account_id"))

        _share_with = d.pop("share_with", UNSET)
        share_with: list[GranteeRef] | Unset = UNSET
        if _share_with is not UNSET:
            share_with = []
            for share_with_item_data in _share_with:
                share_with_item = GranteeRef.from_dict(share_with_item_data)

                share_with.append(share_with_item)

        _visibility = d.pop("visibility", UNSET)
        visibility: PodAccountVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = PodAccountVisibility(_visibility)

        pod_account_create_request = cls(
            account_id=account_id,
            share_with=share_with,
            visibility=visibility,
        )

        pod_account_create_request.additional_properties = d
        return pod_account_create_request

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
