from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UnreadResponse")


@_attrs_define
class UnreadResponse:
    """
    Attributes:
        lines (int | Unset): New lines since the reader last read it, in channels they joined -- and, in one they are
            not in, while a line naming them is unread, from that line; stops at 100. Default: 0.
        mentions (int | Unset): Unread lines naming the reader. Default: 0.
        read_seq (int | Unset): The last line the reader read. Default: 0.
        waiting (int | Unset): Questions here waiting on the reader's answer. Default: 0.
    """

    lines: int | Unset = 0
    mentions: int | Unset = 0
    read_seq: int | Unset = 0
    waiting: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        lines = self.lines

        mentions = self.mentions

        read_seq = self.read_seq

        waiting = self.waiting

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if lines is not UNSET:
            field_dict["lines"] = lines
        if mentions is not UNSET:
            field_dict["mentions"] = mentions
        if read_seq is not UNSET:
            field_dict["read_seq"] = read_seq
        if waiting is not UNSET:
            field_dict["waiting"] = waiting

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        lines = d.pop("lines", UNSET)

        mentions = d.pop("mentions", UNSET)

        read_seq = d.pop("read_seq", UNSET)

        waiting = d.pop("waiting", UNSET)

        unread_response = cls(
            lines=lines,
            mentions=mentions,
            read_seq=read_seq,
            waiting=waiting,
        )

        unread_response.additional_properties = d
        return unread_response

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
