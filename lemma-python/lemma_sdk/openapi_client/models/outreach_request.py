from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.identity_kind import IdentityKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="OutreachRequest")


@_attrs_define
class OutreachRequest:
    """
    Attributes:
        handle (str): One of the contact's handles to write to: an email address, a phone number (WhatsApp), a Telegram
            user id.
        text (str):
        client_message_id (None | str | Unset): The same id twice is the same line: a retry sends nothing new.
        kind (IdentityKind | None | Unset): Which kind of handle it is, when the value could be two.
    """

    handle: str
    text: str
    client_message_id: None | str | Unset = UNSET
    kind: IdentityKind | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        handle = self.handle

        text = self.text

        client_message_id: None | str | Unset
        if isinstance(self.client_message_id, Unset):
            client_message_id = UNSET
        else:
            client_message_id = self.client_message_id

        kind: None | str | Unset
        if isinstance(self.kind, Unset):
            kind = UNSET
        elif isinstance(self.kind, IdentityKind):
            kind = self.kind.value
        else:
            kind = self.kind

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "handle": handle,
                "text": text,
            }
        )
        if client_message_id is not UNSET:
            field_dict["client_message_id"] = client_message_id
        if kind is not UNSET:
            field_dict["kind"] = kind

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        handle = d.pop("handle")

        text = d.pop("text")

        def _parse_client_message_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        client_message_id = _parse_client_message_id(d.pop("client_message_id", UNSET))

        def _parse_kind(data: object) -> IdentityKind | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                kind_type_0 = IdentityKind(data)

                return kind_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(IdentityKind | None | Unset, data)

        kind = _parse_kind(d.pop("kind", UNSET))

        outreach_request = cls(
            handle=handle,
            text=text,
            client_message_id=client_message_id,
            kind=kind,
        )

        outreach_request.additional_properties = d
        return outreach_request

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
