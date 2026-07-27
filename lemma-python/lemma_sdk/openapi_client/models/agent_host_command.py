from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.agent_host_command_kind import AgentHostCommandKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_host_command_payload import AgentHostCommandPayload


T = TypeVar("T", bound="AgentHostCommand")


@_attrs_define
class AgentHostCommand:
    """
    Attributes:
        command_id (UUID):
        created_at (datetime.datetime):
        expires_at (datetime.datetime):
        kind (AgentHostCommandKind):
        payload_sha256 (str):
        lease_epoch (int | None | Unset):
        payload (AgentHostCommandPayload | Unset):
        run_id (None | Unset | UUID):
    """

    command_id: UUID
    created_at: datetime.datetime
    expires_at: datetime.datetime
    kind: AgentHostCommandKind
    payload_sha256: str
    lease_epoch: int | None | Unset = UNSET
    payload: AgentHostCommandPayload | Unset = UNSET
    run_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        command_id = str(self.command_id)

        created_at = self.created_at.isoformat()

        expires_at = self.expires_at.isoformat()

        kind = self.kind.value

        payload_sha256 = self.payload_sha256

        lease_epoch: int | None | Unset
        if isinstance(self.lease_epoch, Unset):
            lease_epoch = UNSET
        else:
            lease_epoch = self.lease_epoch

        payload: dict[str, Any] | Unset = UNSET
        if not isinstance(self.payload, Unset):
            payload = self.payload.to_dict()

        run_id: None | str | Unset
        if isinstance(self.run_id, Unset):
            run_id = UNSET
        elif isinstance(self.run_id, UUID):
            run_id = str(self.run_id)
        else:
            run_id = self.run_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "command_id": command_id,
                "created_at": created_at,
                "expires_at": expires_at,
                "kind": kind,
                "payload_sha256": payload_sha256,
            }
        )
        if lease_epoch is not UNSET:
            field_dict["lease_epoch"] = lease_epoch
        if payload is not UNSET:
            field_dict["payload"] = payload
        if run_id is not UNSET:
            field_dict["run_id"] = run_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_host_command_payload import AgentHostCommandPayload

        d = dict(src_dict)
        command_id = UUID(d.pop("command_id"))

        created_at = isoparse(d.pop("created_at"))

        expires_at = isoparse(d.pop("expires_at"))

        kind = AgentHostCommandKind(d.pop("kind"))

        payload_sha256 = d.pop("payload_sha256")

        def _parse_lease_epoch(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        lease_epoch = _parse_lease_epoch(d.pop("lease_epoch", UNSET))

        _payload = d.pop("payload", UNSET)
        payload: AgentHostCommandPayload | Unset
        if isinstance(_payload, Unset):
            payload = UNSET
        else:
            payload = AgentHostCommandPayload.from_dict(_payload)

        def _parse_run_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                run_id_type_0 = UUID(data)

                return run_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        run_id = _parse_run_id(d.pop("run_id", UNSET))

        agent_host_command = cls(
            command_id=command_id,
            created_at=created_at,
            expires_at=expires_at,
            kind=kind,
            payload_sha256=payload_sha256,
            lease_epoch=lease_epoch,
            payload=payload,
            run_id=run_id,
        )

        agent_host_command.additional_properties = d
        return agent_host_command

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
