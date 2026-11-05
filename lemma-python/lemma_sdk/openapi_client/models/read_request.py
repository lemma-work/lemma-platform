from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ReadRequest")


@_attrs_define
class ReadRequest:
    """
    Attributes:
        seq (int): The newest line the reader has seen.
        thread_root_id (None | Unset | UUID): Read in this thread: its replies (and its first line) up to `seq` are
            seen. Without it, the timeline is: thread replies are not.
    """

    seq: int
    thread_root_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        seq = self.seq

        thread_root_id: None | str | Unset
        if isinstance(self.thread_root_id, Unset):
            thread_root_id = UNSET
        elif isinstance(self.thread_root_id, UUID):
            thread_root_id = str(self.thread_root_id)
        else:
            thread_root_id = self.thread_root_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "seq": seq,
            }
        )
        if thread_root_id is not UNSET:
            field_dict["thread_root_id"] = thread_root_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        seq = d.pop("seq")

        def _parse_thread_root_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                thread_root_id_type_0 = UUID(data)

                return thread_root_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        thread_root_id = _parse_thread_root_id(d.pop("thread_root_id", UNSET))

        read_request = cls(
            seq=seq,
            thread_root_id=thread_root_id,
        )

        read_request.additional_properties = d
        return read_request

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
