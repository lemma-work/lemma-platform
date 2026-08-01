from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="AgentHostPairingCompleted")


@_attrs_define
class AgentHostPairingCompleted:
    """
    Attributes:
        host_id (UUID):
        host_secret (str):
        user_id (UUID):
    """

    host_id: UUID
    host_secret: str
    user_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        host_id = str(self.host_id)

        host_secret = self.host_secret

        user_id = str(self.user_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "host_id": host_id,
                "host_secret": host_secret,
                "user_id": user_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        host_id = UUID(d.pop("host_id"))

        host_secret = d.pop("host_secret")

        user_id = UUID(d.pop("user_id"))

        agent_host_pairing_completed = cls(
            host_id=host_id,
            host_secret=host_secret,
            user_id=user_id,
        )

        agent_host_pairing_completed.additional_properties = d
        return agent_host_pairing_completed

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
