from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.my_membership_update_notify_via_type_0_item import (
    MyMembershipUpdateNotifyViaType0Item,
)
from ..models.notify_level import NotifyLevel
from ..types import UNSET, Unset

T = TypeVar("T", bound="MyMembershipUpdate")


@_attrs_define
class MyMembershipUpdate:
    """Absent is unchanged; ``null`` clears ``notify_via`` (the pod's rule)
    and ``muted_until`` (unmuted).

        Attributes:
            muted_until (datetime.datetime | None | Unset):
            notify (None | NotifyLevel | Unset):
            notify_via (list[MyMembershipUpdateNotifyViaType0Item] | None | Unset):
    """

    muted_until: datetime.datetime | None | Unset = UNSET
    notify: None | NotifyLevel | Unset = UNSET
    notify_via: list[MyMembershipUpdateNotifyViaType0Item] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        muted_until: None | str | Unset
        if isinstance(self.muted_until, Unset):
            muted_until = UNSET
        elif isinstance(self.muted_until, datetime.datetime):
            muted_until = self.muted_until.isoformat()
        else:
            muted_until = self.muted_until

        notify: None | str | Unset
        if isinstance(self.notify, Unset):
            notify = UNSET
        elif isinstance(self.notify, NotifyLevel):
            notify = self.notify.value
        else:
            notify = self.notify

        notify_via: list[str] | None | Unset
        if isinstance(self.notify_via, Unset):
            notify_via = UNSET
        elif isinstance(self.notify_via, list):
            notify_via = []
            for notify_via_type_0_item_data in self.notify_via:
                notify_via_type_0_item = notify_via_type_0_item_data.value
                notify_via.append(notify_via_type_0_item)

        else:
            notify_via = self.notify_via

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if muted_until is not UNSET:
            field_dict["muted_until"] = muted_until
        if notify is not UNSET:
            field_dict["notify"] = notify
        if notify_via is not UNSET:
            field_dict["notify_via"] = notify_via

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

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

        def _parse_notify(data: object) -> None | NotifyLevel | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                notify_type_0 = NotifyLevel(data)

                return notify_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | NotifyLevel | Unset, data)

        notify = _parse_notify(d.pop("notify", UNSET))

        def _parse_notify_via(
            data: object,
        ) -> list[MyMembershipUpdateNotifyViaType0Item] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                notify_via_type_0 = []
                _notify_via_type_0 = data
                for notify_via_type_0_item_data in _notify_via_type_0:
                    notify_via_type_0_item = MyMembershipUpdateNotifyViaType0Item(
                        notify_via_type_0_item_data
                    )

                    notify_via_type_0.append(notify_via_type_0_item)

                return notify_via_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[MyMembershipUpdateNotifyViaType0Item] | None | Unset, data)

        notify_via = _parse_notify_via(d.pop("notify_via", UNSET))

        my_membership_update = cls(
            muted_until=muted_until,
            notify=notify,
            notify_via=notify_via,
        )

        my_membership_update.additional_properties = d
        return my_membership_update

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
