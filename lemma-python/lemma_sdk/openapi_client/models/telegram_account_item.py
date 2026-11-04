from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="TelegramAccountItem")


@_attrs_define
class TelegramAccountItem:
    """A Telegram account the current user proved from a Lemma session.

    Attributes:
        id (UUID):
        name (None | str | Unset): The name Telegram last gave the account.
        proved_at (datetime.datetime | None | Unset):
    """

    id: UUID
    name: None | str | Unset = UNSET
    proved_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        proved_at: None | str | Unset
        if isinstance(self.proved_at, Unset):
            proved_at = UNSET
        elif isinstance(self.proved_at, datetime.datetime):
            proved_at = self.proved_at.isoformat()
        else:
            proved_at = self.proved_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
            }
        )
        if name is not UNSET:
            field_dict["name"] = name
        if proved_at is not UNSET:
            field_dict["proved_at"] = proved_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_proved_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                proved_at_type_0 = isoparse(data)

                return proved_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        proved_at = _parse_proved_at(d.pop("proved_at", UNSET))

        telegram_account_item = cls(
            id=id,
            name=name,
            proved_at=proved_at,
        )

        telegram_account_item.additional_properties = d
        return telegram_account_item

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
