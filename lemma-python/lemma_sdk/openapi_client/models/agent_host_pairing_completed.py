from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="AgentHostPairingCompleted")


@_attrs_define
class AgentHostPairingCompleted:
    """
    Attributes:
        host_id (UUID):
        organization_id (None | UUID):
        public_key_fingerprint (str):
        user_id (UUID):
    """

    host_id: UUID
    organization_id: None | UUID
    public_key_fingerprint: str
    user_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        host_id = str(self.host_id)

        organization_id: None | str
        if isinstance(self.organization_id, UUID):
            organization_id = str(self.organization_id)
        else:
            organization_id = self.organization_id

        public_key_fingerprint = self.public_key_fingerprint

        user_id = str(self.user_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "host_id": host_id,
                "organization_id": organization_id,
                "public_key_fingerprint": public_key_fingerprint,
                "user_id": user_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        host_id = UUID(d.pop("host_id"))

        def _parse_organization_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                organization_id_type_0 = UUID(data)

                return organization_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        organization_id = _parse_organization_id(d.pop("organization_id"))

        public_key_fingerprint = d.pop("public_key_fingerprint")

        user_id = UUID(d.pop("user_id"))

        agent_host_pairing_completed = cls(
            host_id=host_id,
            organization_id=organization_id,
            public_key_fingerprint=public_key_fingerprint,
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
