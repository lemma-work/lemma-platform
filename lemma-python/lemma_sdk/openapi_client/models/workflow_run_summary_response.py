from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.workflow_run_status import WorkflowRunStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="WorkflowRunSummaryResponse")


@_attrs_define
class WorkflowRunSummaryResponse:
    """
    Attributes:
        id (UUID):
        pod_id (UUID):
        user_id (UUID):
        workflow_id (UUID):
        completed_at (datetime.datetime | None | Unset):
        created_at (datetime.datetime | None | Unset):
        current_node_id (None | str | Unset):
        error (None | str | Unset):
        failed_node_id (None | str | Unset):
        schedule_event_id (None | str | Unset):
        start_type (str | Unset):  Default: 'MANUAL'.
        started_at (datetime.datetime | None | Unset):
        status (WorkflowRunStatus | Unset): Status of a flow run.

            PENDING exists only in memory before the first advance; persisted runs
            are RUNNING, WAITING, or terminal. WAITING is reserved for human form
            waits. Runs suspended on platform work such as an agent, function job, or
            timer remain RUNNING; the active wait row records the exact wait_type.
        updated_at (datetime.datetime | None | Unset):
    """

    id: UUID
    pod_id: UUID
    user_id: UUID
    workflow_id: UUID
    completed_at: datetime.datetime | None | Unset = UNSET
    created_at: datetime.datetime | None | Unset = UNSET
    current_node_id: None | str | Unset = UNSET
    error: None | str | Unset = UNSET
    failed_node_id: None | str | Unset = UNSET
    schedule_event_id: None | str | Unset = UNSET
    start_type: str | Unset = "MANUAL"
    started_at: datetime.datetime | None | Unset = UNSET
    status: WorkflowRunStatus | Unset = UNSET
    updated_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        pod_id = str(self.pod_id)

        user_id = str(self.user_id)

        workflow_id = str(self.workflow_id)

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

        current_node_id: None | str | Unset
        if isinstance(self.current_node_id, Unset):
            current_node_id = UNSET
        else:
            current_node_id = self.current_node_id

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        failed_node_id: None | str | Unset
        if isinstance(self.failed_node_id, Unset):
            failed_node_id = UNSET
        else:
            failed_node_id = self.failed_node_id

        schedule_event_id: None | str | Unset
        if isinstance(self.schedule_event_id, Unset):
            schedule_event_id = UNSET
        else:
            schedule_event_id = self.schedule_event_id

        start_type = self.start_type

        started_at: None | str | Unset
        if isinstance(self.started_at, Unset):
            started_at = UNSET
        elif isinstance(self.started_at, datetime.datetime):
            started_at = self.started_at.isoformat()
        else:
            started_at = self.started_at

        status: str | Unset = UNSET
        if not isinstance(self.status, Unset):
            status = self.status.value

        updated_at: None | str | Unset
        if isinstance(self.updated_at, Unset):
            updated_at = UNSET
        elif isinstance(self.updated_at, datetime.datetime):
            updated_at = self.updated_at.isoformat()
        else:
            updated_at = self.updated_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "pod_id": pod_id,
                "user_id": user_id,
                "workflow_id": workflow_id,
            }
        )
        if completed_at is not UNSET:
            field_dict["completed_at"] = completed_at
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if current_node_id is not UNSET:
            field_dict["current_node_id"] = current_node_id
        if error is not UNSET:
            field_dict["error"] = error
        if failed_node_id is not UNSET:
            field_dict["failed_node_id"] = failed_node_id
        if schedule_event_id is not UNSET:
            field_dict["schedule_event_id"] = schedule_event_id
        if start_type is not UNSET:
            field_dict["start_type"] = start_type
        if started_at is not UNSET:
            field_dict["started_at"] = started_at
        if status is not UNSET:
            field_dict["status"] = status
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        pod_id = UUID(d.pop("pod_id"))

        user_id = UUID(d.pop("user_id"))

        workflow_id = UUID(d.pop("workflow_id"))

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

        def _parse_current_node_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        current_node_id = _parse_current_node_id(d.pop("current_node_id", UNSET))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_failed_node_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        failed_node_id = _parse_failed_node_id(d.pop("failed_node_id", UNSET))

        def _parse_schedule_event_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        schedule_event_id = _parse_schedule_event_id(d.pop("schedule_event_id", UNSET))

        start_type = d.pop("start_type", UNSET)

        def _parse_started_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                started_at_type_0 = isoparse(data)

                return started_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        started_at = _parse_started_at(d.pop("started_at", UNSET))

        _status = d.pop("status", UNSET)
        status: WorkflowRunStatus | Unset
        if isinstance(_status, Unset):
            status = UNSET
        else:
            status = WorkflowRunStatus(_status)

        def _parse_updated_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                updated_at_type_0 = isoparse(data)

                return updated_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        updated_at = _parse_updated_at(d.pop("updated_at", UNSET))

        workflow_run_summary_response = cls(
            id=id,
            pod_id=pod_id,
            user_id=user_id,
            workflow_id=workflow_id,
            completed_at=completed_at,
            created_at=created_at,
            current_node_id=current_node_id,
            error=error,
            failed_node_id=failed_node_id,
            schedule_event_id=schedule_event_id,
            start_type=start_type,
            started_at=started_at,
            status=status,
            updated_at=updated_at,
        )

        workflow_run_summary_response.additional_properties = d
        return workflow_run_summary_response

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
