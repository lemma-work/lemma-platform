from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.workflow_run_wait_status import WorkflowRunWaitStatus
from ..models.workflow_run_wait_type import WorkflowRunWaitType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.workflow_run_wait_response_payload import (
        WorkflowRunWaitResponsePayload,
    )


T = TypeVar("T", bound="WorkflowRunWaitResponse")


@_attrs_define
class WorkflowRunWaitResponse:
    """
    Attributes:
        id (UUID):
        node_id (str):
        pod_id (UUID):
        run_id (UUID):
        status (WorkflowRunWaitStatus):
        wait_type (WorkflowRunWaitType):
        workflow_id (UUID):
        assigned_pod_member_id (None | Unset | UUID):
        completed_at (datetime.datetime | None | Unset):
        created_at (datetime.datetime | None | Unset):
        external_ref (None | str | Unset):
        payload (WorkflowRunWaitResponsePayload | Unset):
    """

    id: UUID
    node_id: str
    pod_id: UUID
    run_id: UUID
    status: WorkflowRunWaitStatus
    wait_type: WorkflowRunWaitType
    workflow_id: UUID
    assigned_pod_member_id: None | Unset | UUID = UNSET
    completed_at: datetime.datetime | None | Unset = UNSET
    created_at: datetime.datetime | None | Unset = UNSET
    external_ref: None | str | Unset = UNSET
    payload: WorkflowRunWaitResponsePayload | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        node_id = self.node_id

        pod_id = str(self.pod_id)

        run_id = str(self.run_id)

        status = self.status.value

        wait_type = self.wait_type.value

        workflow_id = str(self.workflow_id)

        assigned_pod_member_id: None | str | Unset
        if isinstance(self.assigned_pod_member_id, Unset):
            assigned_pod_member_id = UNSET
        elif isinstance(self.assigned_pod_member_id, UUID):
            assigned_pod_member_id = str(self.assigned_pod_member_id)
        else:
            assigned_pod_member_id = self.assigned_pod_member_id

        completed_at: None | str | Unset
        if isinstance(self.completed_at, Unset):
            completed_at = UNSET
        elif isinstance(self.completed_at, datetime.datetime):
            completed_at = self.completed_at.isoformat()
        else:
            completed_at = self.completed_at

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        elif isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        external_ref: None | str | Unset
        if isinstance(self.external_ref, Unset):
            external_ref = UNSET
        else:
            external_ref = self.external_ref

        payload: dict[str, Any] | Unset = UNSET
        if not isinstance(self.payload, Unset):
            payload = self.payload.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "node_id": node_id,
                "pod_id": pod_id,
                "run_id": run_id,
                "status": status,
                "wait_type": wait_type,
                "workflow_id": workflow_id,
            }
        )
        if assigned_pod_member_id is not UNSET:
            field_dict["assigned_pod_member_id"] = assigned_pod_member_id
        if completed_at is not UNSET:
            field_dict["completed_at"] = completed_at
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if external_ref is not UNSET:
            field_dict["external_ref"] = external_ref
        if payload is not UNSET:
            field_dict["payload"] = payload

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.workflow_run_wait_response_payload import (
            WorkflowRunWaitResponsePayload,
        )

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        node_id = d.pop("node_id")

        pod_id = UUID(d.pop("pod_id"))

        run_id = UUID(d.pop("run_id"))

        status = WorkflowRunWaitStatus(d.pop("status"))

        wait_type = WorkflowRunWaitType(d.pop("wait_type"))

        workflow_id = UUID(d.pop("workflow_id"))

        def _parse_assigned_pod_member_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                assigned_pod_member_id_type_0 = UUID(data)

                return assigned_pod_member_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        assigned_pod_member_id = _parse_assigned_pod_member_id(
            d.pop("assigned_pod_member_id", UNSET)
        )

        def _parse_completed_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                completed_at_type_0 = isoparse(data)

                return completed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        completed_at = _parse_completed_at(d.pop("completed_at", UNSET))

        def _parse_created_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = isoparse(data)

                return created_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        created_at = _parse_created_at(d.pop("created_at", UNSET))

        def _parse_external_ref(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        external_ref = _parse_external_ref(d.pop("external_ref", UNSET))

        _payload = d.pop("payload", UNSET)
        payload: WorkflowRunWaitResponsePayload | Unset
        if isinstance(_payload, Unset):
            payload = UNSET
        else:
            payload = WorkflowRunWaitResponsePayload.from_dict(_payload)

        workflow_run_wait_response = cls(
            id=id,
            node_id=node_id,
            pod_id=pod_id,
            run_id=run_id,
            status=status,
            wait_type=wait_type,
            workflow_id=workflow_id,
            assigned_pod_member_id=assigned_pod_member_id,
            completed_at=completed_at,
            created_at=created_at,
            external_ref=external_ref,
            payload=payload,
        )

        workflow_run_wait_response.additional_properties = d
        return workflow_run_wait_response

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
