from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.identity_kind import IdentityKind
from ..models.identity_strength import IdentityStrength

T = TypeVar("T", bound="ContactIdentityResponse")


@_attrs_define
class ContactIdentityResponse:
    """
    Attributes:
        kind (IdentityKind): What sort of handle names a contact.
        strength (IdentityStrength): Who vouched for a handle.

            ``CHANNEL``: the platform the message came through, in a payload whose
            signature was checked (WhatsApp, Telegram), or the receiving mail service's
            authentication verdict (email). ``MEMBER``: a pod member added it by hand,
            which says who the member believes it is and nothing about who writes from
            it -- so it never makes a message count as that contact on its own.
        value (str):
        verified_at (datetime.datetime):
    """

    kind: IdentityKind
    strength: IdentityStrength
    value: str
    verified_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        strength = self.strength.value

        value = self.value

        verified_at = self.verified_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
                "strength": strength,
                "value": value,
                "verified_at": verified_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = IdentityKind(d.pop("kind"))

        strength = IdentityStrength(d.pop("strength"))

        value = d.pop("value")

        verified_at = isoparse(d.pop("verified_at"))

        contact_identity_response = cls(
            kind=kind,
            strength=strength,
            value=value,
            verified_at=verified_at,
        )

        contact_identity_response.additional_properties = d
        return contact_identity_response

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
