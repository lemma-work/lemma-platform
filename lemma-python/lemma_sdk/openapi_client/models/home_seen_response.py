from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="HomeSeenResponse")


@_attrs_define
class HomeSeenResponse:
    """
    Attributes:
        previous_seen_at (datetime.datetime | None | Unset): What it was before this write. On a read, the same as
            `seen_at`.
        seen_at (datetime.datetime | None | Unset): When the member last looked at Home; null until first marked.
    """

    previous_seen_at: datetime.datetime | None | Unset = UNSET
    seen_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        previous_seen_at: None | str | Unset
        if isinstance(self.previous_seen_at, Unset):
            previous_seen_at = UNSET
        elif isinstance(self.previous_seen_at, datetime.datetime):
            previous_seen_at = self.previous_seen_at.isoformat()
        else:
            previous_seen_at = self.previous_seen_at

        seen_at: None | str | Unset
        if isinstance(self.seen_at, Unset):
            seen_at = UNSET
        elif isinstance(self.seen_at, datetime.datetime):
            seen_at = self.seen_at.isoformat()
        else:
            seen_at = self.seen_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if previous_seen_at is not UNSET:
            field_dict["previous_seen_at"] = previous_seen_at
        if seen_at is not UNSET:
            field_dict["seen_at"] = seen_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_previous_seen_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                previous_seen_at_type_0 = isoparse(data)

                return previous_seen_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        previous_seen_at = _parse_previous_seen_at(d.pop("previous_seen_at", UNSET))

        def _parse_seen_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                seen_at_type_0 = isoparse(data)

                return seen_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        seen_at = _parse_seen_at(d.pop("seen_at", UNSET))

        home_seen_response = cls(
            previous_seen_at=previous_seen_at,
            seen_at=seen_at,
        )

        home_seen_response.additional_properties = d
        return home_seen_response

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
