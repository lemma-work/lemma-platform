from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="OutreachLine")


@_attrs_define
class OutreachLine:
    """
    Attributes:
        channel_id (UUID):
        created_at (datetime.datetime):
        id (UUID):
        seq (int):
        text (str):
        source_id (None | Unset | UUID):
    """

    channel_id: UUID
    created_at: datetime.datetime
    id: UUID
    seq: int
    text: str
    source_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        channel_id = str(self.channel_id)

        created_at = self.created_at.isoformat()

        id = str(self.id)

        seq = self.seq

        text = self.text

        source_id: None | str | Unset
        if isinstance(self.source_id, Unset):
            source_id = UNSET
        elif isinstance(self.source_id, UUID):
            source_id = str(self.source_id)
        else:
            source_id = self.source_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "channel_id": channel_id,
                "created_at": created_at,
                "id": id,
                "seq": seq,
                "text": text,
            }
        )
        if source_id is not UNSET:
            field_dict["source_id"] = source_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        channel_id = UUID(d.pop("channel_id"))

        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        seq = d.pop("seq")

        text = d.pop("text")

        def _parse_source_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                source_id_type_0 = UUID(data)

                return source_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        source_id = _parse_source_id(d.pop("source_id", UNSET))

        outreach_line = cls(
            channel_id=channel_id,
            created_at=created_at,
            id=id,
            seq=seq,
            text=text,
            source_id=source_id,
        )

        outreach_line.additional_properties = d
        return outreach_line

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
