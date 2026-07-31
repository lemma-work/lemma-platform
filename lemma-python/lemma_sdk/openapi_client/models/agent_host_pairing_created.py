from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

T = TypeVar("T", bound="AgentHostPairingCreated")


@_attrs_define
class AgentHostPairingCreated:
    """
    Attributes:
        expires_at (datetime.datetime):
        pairing_code (str):
        pairing_id (UUID):
    """

    expires_at: datetime.datetime
    pairing_code: str
    pairing_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        expires_at = self.expires_at.isoformat()

        pairing_code = self.pairing_code

        pairing_id = str(self.pairing_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "expires_at": expires_at,
                "pairing_code": pairing_code,
                "pairing_id": pairing_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expires_at = isoparse(d.pop("expires_at"))

        pairing_code = d.pop("pairing_code")

        pairing_id = UUID(d.pop("pairing_id"))

        agent_host_pairing_created = cls(
            expires_at=expires_at,
            pairing_code=pairing_code,
            pairing_id=pairing_id,
        )

        agent_host_pairing_created.additional_properties = d
        return agent_host_pairing_created

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
