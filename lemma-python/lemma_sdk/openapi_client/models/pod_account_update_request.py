from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.pod_account_visibility import PodAccountVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.grantee_ref import GranteeRef


T = TypeVar("T", bound="PodAccountUpdateRequest")


@_attrs_define
class PodAccountUpdateRequest:
    """
    Attributes:
        allow_sandbox_use (bool | None | Unset): Owner only: allow this account's credential inside sandboxes, so a
            SHARED connector on it (the pod's `github`) can give other members' workspaces its raw token for git and gh,
            which they can copy. Off by default; withdrawing the account turns it off.
        is_member_default (bool | None | Unset): Owner only: make this the account your own calls use here.
        share_with (list[GranteeRef] | None | Unset): RESTRICTED only: replaces who may use it.
        visibility (None | PodAccountVisibility | Unset): Owner only. PERSONAL withdraws it: SHARED connectors using it
            need another account at once.
    """

    allow_sandbox_use: bool | None | Unset = UNSET
    is_member_default: bool | None | Unset = UNSET
    share_with: list[GranteeRef] | None | Unset = UNSET
    visibility: None | PodAccountVisibility | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        allow_sandbox_use: bool | None | Unset
        if isinstance(self.allow_sandbox_use, Unset):
            allow_sandbox_use = UNSET
        else:
            allow_sandbox_use = self.allow_sandbox_use

        is_member_default: bool | None | Unset
        if isinstance(self.is_member_default, Unset):
            is_member_default = UNSET
        else:
            is_member_default = self.is_member_default

        share_with: list[dict[str, Any]] | None | Unset
        if isinstance(self.share_with, Unset):
            share_with = UNSET
        elif isinstance(self.share_with, list):
            share_with = []
            for share_with_type_0_item_data in self.share_with:
                share_with_type_0_item = share_with_type_0_item_data.to_dict()
                share_with.append(share_with_type_0_item)

        else:
            share_with = self.share_with

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        elif isinstance(self.visibility, PodAccountVisibility):
            visibility = self.visibility.value
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if allow_sandbox_use is not UNSET:
            field_dict["allow_sandbox_use"] = allow_sandbox_use
        if is_member_default is not UNSET:
            field_dict["is_member_default"] = is_member_default
        if share_with is not UNSET:
            field_dict["share_with"] = share_with
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.grantee_ref import GranteeRef

        d = dict(src_dict)

        def _parse_allow_sandbox_use(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        allow_sandbox_use = _parse_allow_sandbox_use(d.pop("allow_sandbox_use", UNSET))

        def _parse_is_member_default(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        is_member_default = _parse_is_member_default(d.pop("is_member_default", UNSET))

        def _parse_share_with(data: object) -> list[GranteeRef] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                share_with_type_0 = []
                _share_with_type_0 = data
                for share_with_type_0_item_data in _share_with_type_0:
                    share_with_type_0_item = GranteeRef.from_dict(
                        share_with_type_0_item_data
                    )

                    share_with_type_0.append(share_with_type_0_item)

                return share_with_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[GranteeRef] | None | Unset, data)

        share_with = _parse_share_with(d.pop("share_with", UNSET))

        def _parse_visibility(data: object) -> None | PodAccountVisibility | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                visibility_type_0 = PodAccountVisibility(data)

                return visibility_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PodAccountVisibility | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        pod_account_update_request = cls(
            allow_sandbox_use=allow_sandbox_use,
            is_member_default=is_member_default,
            share_with=share_with,
            visibility=visibility,
        )

        pod_account_update_request.additional_properties = d
        return pod_account_update_request

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
