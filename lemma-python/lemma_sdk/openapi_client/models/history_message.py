from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="HistoryMessage")


@_attrs_define
class HistoryMessage:
    """
    Attributes:
        role (str):
        sequence (int):
        text (str):
        client_nonce (None | str | Unset):
    """

    role: str
    sequence: int
    text: str
    client_nonce: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        role = self.role

        sequence = self.sequence

        text = self.text

        client_nonce: None | str | Unset
        if isinstance(self.client_nonce, Unset):
            client_nonce = UNSET
        else:
            client_nonce = self.client_nonce

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "role": role,
                "sequence": sequence,
                "text": text,
            }
        )
        if client_nonce is not UNSET:
            field_dict["client_nonce"] = client_nonce

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        role = d.pop("role")

        sequence = d.pop("sequence")

        text = d.pop("text")

        def _parse_client_nonce(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        client_nonce = _parse_client_nonce(d.pop("client_nonce", UNSET))

        history_message = cls(
            role=role,
            sequence=sequence,
            text=text,
            client_nonce=client_nonce,
        )

        history_message.additional_properties = d
        return history_message

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
