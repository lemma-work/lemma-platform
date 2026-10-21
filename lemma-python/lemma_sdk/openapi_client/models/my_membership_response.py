from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.member_role import MemberRole
from ..models.notify_level import NotifyLevel
from ..types import UNSET, Unset

T = TypeVar("T", bound="MyMembershipResponse")


@_attrs_define
class MyMembershipResponse:
    """
    Attributes:
        can_manage (bool | Unset): Whether the reader may change this channel and its members. Default: False.
        joined (bool | Unset):  Default: False.
        muted_until (datetime.datetime | None | Unset):
        notify (NotifyLevel | Unset): Which lines of a channel reach a member outside the app.
        notify_via (list[str] | None | Unset): Where lines reach them outside the app; absent for the pod's rule.
        read_seq (int | Unset):  Default: 0.
        role (MemberRole | None | Unset):
    """

    can_manage: bool | Unset = False
    joined: bool | Unset = False
    muted_until: datetime.datetime | None | Unset = UNSET
    notify: NotifyLevel | Unset = UNSET
    notify_via: list[str] | None | Unset = UNSET
    read_seq: int | Unset = 0
    role: MemberRole | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        can_manage = self.can_manage

        joined = self.joined

        muted_until: None | str | Unset
        if isinstance(self.muted_until, Unset):
            muted_until = UNSET
        elif isinstance(self.muted_until, datetime.datetime):
            muted_until = self.muted_until.isoformat()
        else:
            muted_until = self.muted_until

        notify: str | Unset = UNSET
        if not isinstance(self.notify, Unset):
            notify = self.notify.value

        notify_via: list[str] | None | Unset
        if isinstance(self.notify_via, Unset):
            notify_via = UNSET
        elif isinstance(self.notify_via, list):
            notify_via = self.notify_via

        else:
            notify_via = self.notify_via

        read_seq = self.read_seq

        role: None | str | Unset
        if isinstance(self.role, Unset):
            role = UNSET
        elif isinstance(self.role, MemberRole):
            role = self.role.value
        else:
            role = self.role

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if can_manage is not UNSET:
            field_dict["can_manage"] = can_manage
        if joined is not UNSET:
            field_dict["joined"] = joined
        if muted_until is not UNSET:
            field_dict["muted_until"] = muted_until
        if notify is not UNSET:
            field_dict["notify"] = notify
        if notify_via is not UNSET:
            field_dict["notify_via"] = notify_via
        if read_seq is not UNSET:
            field_dict["read_seq"] = read_seq
        if role is not UNSET:
            field_dict["role"] = role

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        can_manage = d.pop("can_manage", UNSET)

        joined = d.pop("joined", UNSET)

        def _parse_muted_until(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                muted_until_type_0 = isoparse(data)

                return muted_until_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        muted_until = _parse_muted_until(d.pop("muted_until", UNSET))

        _notify = d.pop("notify", UNSET)
        notify: NotifyLevel | Unset
        if isinstance(_notify, Unset):
            notify = UNSET
        else:
            notify = NotifyLevel(_notify)

        def _parse_notify_via(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                notify_via_type_0 = cast(list[str], data)

                return notify_via_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        notify_via = _parse_notify_via(d.pop("notify_via", UNSET))

        read_seq = d.pop("read_seq", UNSET)

        def _parse_role(data: object) -> MemberRole | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                role_type_0 = MemberRole(data)

                return role_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(MemberRole | None | Unset, data)

        role = _parse_role(d.pop("role", UNSET))

        my_membership_response = cls(
            can_manage=can_manage,
            joined=joined,
            muted_until=muted_until,
            notify=notify,
            notify_via=notify_via,
            read_seq=read_seq,
            role=role,
        )

        my_membership_response.additional_properties = d
        return my_membership_response

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
