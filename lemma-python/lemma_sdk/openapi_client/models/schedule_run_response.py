from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.schedule_run_status import ScheduleRunStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.schedule_run_response_llm_output import ScheduleRunResponseLlmOutput
    from ..models.schedule_run_response_metadata import ScheduleRunResponseMetadata
    from ..models.schedule_run_response_payload import ScheduleRunResponsePayload


T = TypeVar("T", bound="ScheduleRunResponse")


@_attrs_define
class ScheduleRunResponse:
    """
    Attributes:
        attempts (int):
        created_at (datetime.datetime):
        id (UUID):
        llm_output (ScheduleRunResponseLlmOutput):
        metadata (ScheduleRunResponseMetadata):
        payload (ScheduleRunResponsePayload):
        schedule_id (UUID):
        source_event_id (str):
        status (ScheduleRunStatus):
        target_kind (str):
        updated_at (datetime.datetime):
        completed_at (datetime.datetime | None | Unset):
        error_code (None | str | Unset):
        error_type (None | str | Unset):
        source_occurred_at (datetime.datetime | None | Unset):
        started_at (datetime.datetime | None | Unset):
        target_run_id (None | str | Unset):
    """

    attempts: int
    created_at: datetime.datetime
    id: UUID
    llm_output: ScheduleRunResponseLlmOutput
    metadata: ScheduleRunResponseMetadata
    payload: ScheduleRunResponsePayload
    schedule_id: UUID
    source_event_id: str
    status: ScheduleRunStatus
    target_kind: str
    updated_at: datetime.datetime
    completed_at: datetime.datetime | None | Unset = UNSET
    error_code: None | str | Unset = UNSET
    error_type: None | str | Unset = UNSET
    source_occurred_at: datetime.datetime | None | Unset = UNSET
    started_at: datetime.datetime | None | Unset = UNSET
    target_run_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        attempts = self.attempts

        created_at = self.created_at.isoformat()

        id = str(self.id)

        llm_output = self.llm_output.to_dict()

        metadata = self.metadata.to_dict()

        payload = self.payload.to_dict()

        schedule_id = str(self.schedule_id)

        source_event_id = self.source_event_id

        status = self.status.value

        target_kind = self.target_kind

        updated_at = self.updated_at.isoformat()

        completed_at: None | str | Unset
        if isinstance(self.completed_at, Unset):
            completed_at = UNSET
        elif isinstance(self.completed_at, datetime.datetime):
            completed_at = self.completed_at.isoformat()
        else:
            completed_at = self.completed_at

        error_code: None | str | Unset
        if isinstance(self.error_code, Unset):
            error_code = UNSET
        else:
            error_code = self.error_code

        error_type: None | str | Unset
        if isinstance(self.error_type, Unset):
            error_type = UNSET
        else:
            error_type = self.error_type

        source_occurred_at: None | str | Unset
        if isinstance(self.source_occurred_at, Unset):
            source_occurred_at = UNSET
        elif isinstance(self.source_occurred_at, datetime.datetime):
            source_occurred_at = self.source_occurred_at.isoformat()
        else:
            source_occurred_at = self.source_occurred_at

        started_at: None | str | Unset
        if isinstance(self.started_at, Unset):
            started_at = UNSET
        elif isinstance(self.started_at, datetime.datetime):
            started_at = self.started_at.isoformat()
        else:
            started_at = self.started_at

        target_run_id: None | str | Unset
        if isinstance(self.target_run_id, Unset):
            target_run_id = UNSET
        else:
            target_run_id = self.target_run_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "attempts": attempts,
                "created_at": created_at,
                "id": id,
                "llm_output": llm_output,
                "metadata": metadata,
                "payload": payload,
                "schedule_id": schedule_id,
                "source_event_id": source_event_id,
                "status": status,
                "target_kind": target_kind,
                "updated_at": updated_at,
            }
        )
        if completed_at is not UNSET:
            field_dict["completed_at"] = completed_at
        if error_code is not UNSET:
            field_dict["error_code"] = error_code
        if error_type is not UNSET:
            field_dict["error_type"] = error_type
        if source_occurred_at is not UNSET:
            field_dict["source_occurred_at"] = source_occurred_at
        if started_at is not UNSET:
            field_dict["started_at"] = started_at
        if target_run_id is not UNSET:
            field_dict["target_run_id"] = target_run_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.schedule_run_response_llm_output import (
            ScheduleRunResponseLlmOutput,
        )
        from ..models.schedule_run_response_metadata import ScheduleRunResponseMetadata
        from ..models.schedule_run_response_payload import ScheduleRunResponsePayload

        d = dict(src_dict)
        attempts = d.pop("attempts")

        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        llm_output = ScheduleRunResponseLlmOutput.from_dict(d.pop("llm_output"))

        metadata = ScheduleRunResponseMetadata.from_dict(d.pop("metadata"))

        payload = ScheduleRunResponsePayload.from_dict(d.pop("payload"))

        schedule_id = UUID(d.pop("schedule_id"))

        source_event_id = d.pop("source_event_id")

        status = ScheduleRunStatus(d.pop("status"))

        target_kind = d.pop("target_kind")

        updated_at = isoparse(d.pop("updated_at"))

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

        def _parse_error_code(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error_code = _parse_error_code(d.pop("error_code", UNSET))

        def _parse_error_type(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error_type = _parse_error_type(d.pop("error_type", UNSET))

        def _parse_source_occurred_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                source_occurred_at_type_0 = isoparse(data)

                return source_occurred_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        source_occurred_at = _parse_source_occurred_at(
            d.pop("source_occurred_at", UNSET)
        )

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

        def _parse_target_run_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        target_run_id = _parse_target_run_id(d.pop("target_run_id", UNSET))

        schedule_run_response = cls(
            attempts=attempts,
            created_at=created_at,
            id=id,
            llm_output=llm_output,
            metadata=metadata,
            payload=payload,
            schedule_id=schedule_id,
            source_event_id=source_event_id,
            status=status,
            target_kind=target_kind,
            updated_at=updated_at,
            completed_at=completed_at,
            error_code=error_code,
            error_type=error_type,
            source_occurred_at=source_occurred_at,
            started_at=started_at,
            target_run_id=target_run_id,
        )

        schedule_run_response.additional_properties = d
        return schedule_run_response

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
