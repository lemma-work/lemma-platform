from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

T = TypeVar("T", bound="PendingPairingResponse")


@_attrs_define
class PendingPairingResponse:
    """
    Attributes:
        approved (bool):
        display_name (str): What the machine asking calls itself.
        expires_at (datetime.datetime):
        mode (str):
        platform (str):
    """

    approved: bool
    display_name: str
    expires_at: datetime.datetime
    mode: str
    platform: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        approved = self.approved

        display_name = self.display_name

        expires_at = self.expires_at.isoformat()

        mode = self.mode

        platform = self.platform

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "approved": approved,
                "display_name": display_name,
                "expires_at": expires_at,
                "mode": mode,
                "platform": platform,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        approved = d.pop("approved")

        display_name = d.pop("display_name")

        expires_at = isoparse(d.pop("expires_at"))

        mode = d.pop("mode")

        platform = d.pop("platform")

        pending_pairing_response = cls(
            approved=approved,
            display_name=display_name,
            expires_at=expires_at,
            mode=mode,
            platform=platform,
        )

        pending_pairing_response.additional_properties = d
        return pending_pairing_response

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
