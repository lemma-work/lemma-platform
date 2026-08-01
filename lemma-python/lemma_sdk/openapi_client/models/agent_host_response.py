from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.agent_host_status import AgentHostStatus

if TYPE_CHECKING:
    from ..models.agent_host_response_capacity import AgentHostResponseCapacity


T = TypeVar("T", bound="AgentHostResponse")


@_attrs_define
class AgentHostResponse:
    """
    Attributes:
        capacity (AgentHostResponseCapacity):
        created_at (datetime.datetime):
        display_name (str):
        host_release (str):
        id (UUID):
        installation_id (str):
        last_seen_at (datetime.datetime | None):
        protocol_version (int | None):
        revoked_at (datetime.datetime | None):
        status (AgentHostStatus):
        updated_at (datetime.datetime):
        user_id (UUID):
    """

    capacity: AgentHostResponseCapacity
    created_at: datetime.datetime
    display_name: str
    host_release: str
    id: UUID
    installation_id: str
    last_seen_at: datetime.datetime | None
    protocol_version: int | None
    revoked_at: datetime.datetime | None
    status: AgentHostStatus
    updated_at: datetime.datetime
    user_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        capacity = self.capacity.to_dict()

        created_at = self.created_at.isoformat()

        display_name = self.display_name

        host_release = self.host_release

        id = str(self.id)

        installation_id = self.installation_id

        last_seen_at: None | str
        if isinstance(self.last_seen_at, datetime.datetime):
            last_seen_at = self.last_seen_at.isoformat()
        else:
            last_seen_at = self.last_seen_at

        protocol_version: int | None
        protocol_version = self.protocol_version

        revoked_at: None | str
        if isinstance(self.revoked_at, datetime.datetime):
            revoked_at = self.revoked_at.isoformat()
        else:
            revoked_at = self.revoked_at

        status = self.status.value

        updated_at = self.updated_at.isoformat()

        user_id = str(self.user_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "capacity": capacity,
                "created_at": created_at,
                "display_name": display_name,
                "host_release": host_release,
                "id": id,
                "installation_id": installation_id,
                "last_seen_at": last_seen_at,
                "protocol_version": protocol_version,
                "revoked_at": revoked_at,
                "status": status,
                "updated_at": updated_at,
                "user_id": user_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_host_response_capacity import AgentHostResponseCapacity

        d = dict(src_dict)
        capacity = AgentHostResponseCapacity.from_dict(d.pop("capacity"))

        created_at = isoparse(d.pop("created_at"))

        display_name = d.pop("display_name")

        host_release = d.pop("host_release")

        id = UUID(d.pop("id"))

        installation_id = d.pop("installation_id")

        def _parse_last_seen_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_seen_at_type_0 = isoparse(data)

                return last_seen_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        last_seen_at = _parse_last_seen_at(d.pop("last_seen_at"))

        def _parse_protocol_version(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        protocol_version = _parse_protocol_version(d.pop("protocol_version"))

        def _parse_revoked_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                revoked_at_type_0 = isoparse(data)

                return revoked_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        revoked_at = _parse_revoked_at(d.pop("revoked_at"))

        status = AgentHostStatus(d.pop("status"))

        updated_at = isoparse(d.pop("updated_at"))

        user_id = UUID(d.pop("user_id"))

        agent_host_response = cls(
            capacity=capacity,
            created_at=created_at,
            display_name=display_name,
            host_release=host_release,
            id=id,
            installation_id=installation_id,
            last_seen_at=last_seen_at,
            protocol_version=protocol_version,
            revoked_at=revoked_at,
            status=status,
            updated_at=updated_at,
            user_id=user_id,
        )

        agent_host_response.additional_properties = d
        return agent_host_response

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
