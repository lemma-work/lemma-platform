from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.function_run_status import FunctionRunStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.json_object import JsonObject


T = TypeVar("T", bound="FunctionRunResponse")


@_attrs_define
class FunctionRunResponse:
    """Function run response.

    Attributes:
        completed_at (datetime.datetime | None):
        created_at (datetime.datetime | None):
        function_id (UUID):
        id (UUID):
        started_at (datetime.datetime | None):
        status (FunctionRunStatus): Status of a function run.
        user_id (UUID):
        error (None | str | Unset):
        input_data (JsonObject | None | Unset):
        job_id (None | str | Unset):
        logs (None | str | Unset):
        output_data (JsonObject | None | Unset):
        revision_hash (None | str | Unset):
        user_email (None | str | Unset):
    """

    completed_at: datetime.datetime | None
    created_at: datetime.datetime | None
    function_id: UUID
    id: UUID
    started_at: datetime.datetime | None
    status: FunctionRunStatus
    user_id: UUID
    error: None | str | Unset = UNSET
    input_data: JsonObject | None | Unset = UNSET
    job_id: None | str | Unset = UNSET
    logs: None | str | Unset = UNSET
    output_data: JsonObject | None | Unset = UNSET
    revision_hash: None | str | Unset = UNSET
    user_email: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.json_object import JsonObject

        completed_at: None | str
        if isinstance(self.completed_at, datetime.datetime):
            completed_at = self.completed_at.isoformat()
        else:
            completed_at = self.completed_at

        created_at: None | str
        if isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        function_id = str(self.function_id)

        id = str(self.id)

        started_at: None | str
        if isinstance(self.started_at, datetime.datetime):
            started_at = self.started_at.isoformat()
        else:
            started_at = self.started_at

        status = self.status.value

        user_id = str(self.user_id)

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        input_data: dict[str, Any] | None | Unset
        if isinstance(self.input_data, Unset):
            input_data = UNSET
        elif isinstance(self.input_data, JsonObject):
            input_data = self.input_data.to_dict()
        else:
            input_data = self.input_data

        job_id: None | str | Unset
        if isinstance(self.job_id, Unset):
            job_id = UNSET
        else:
            job_id = self.job_id

        logs: None | str | Unset
        if isinstance(self.logs, Unset):
            logs = UNSET
        else:
            logs = self.logs

        output_data: dict[str, Any] | None | Unset
        if isinstance(self.output_data, Unset):
            output_data = UNSET
        elif isinstance(self.output_data, JsonObject):
            output_data = self.output_data.to_dict()
        else:
            output_data = self.output_data

        revision_hash: None | str | Unset
        if isinstance(self.revision_hash, Unset):
            revision_hash = UNSET
        else:
            revision_hash = self.revision_hash

        user_email: None | str | Unset
        if isinstance(self.user_email, Unset):
            user_email = UNSET
        else:
            user_email = self.user_email

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "completed_at": completed_at,
                "created_at": created_at,
                "function_id": function_id,
                "id": id,
                "started_at": started_at,
                "status": status,
                "user_id": user_id,
            }
        )
        if error is not UNSET:
            field_dict["error"] = error
        if input_data is not UNSET:
            field_dict["input_data"] = input_data
        if job_id is not UNSET:
            field_dict["job_id"] = job_id
        if logs is not UNSET:
            field_dict["logs"] = logs
        if output_data is not UNSET:
            field_dict["output_data"] = output_data
        if revision_hash is not UNSET:
            field_dict["revision_hash"] = revision_hash
        if user_email is not UNSET:
            field_dict["user_email"] = user_email

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.json_object import JsonObject

        d = dict(src_dict)

        def _parse_completed_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                completed_at_type_0 = isoparse(data)

                return completed_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        completed_at = _parse_completed_at(d.pop("completed_at"))

        def _parse_created_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = isoparse(data)

                return created_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        created_at = _parse_created_at(d.pop("created_at"))

        function_id = UUID(d.pop("function_id"))

        id = UUID(d.pop("id"))

        def _parse_started_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                started_at_type_0 = isoparse(data)

                return started_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        started_at = _parse_started_at(d.pop("started_at"))

        status = FunctionRunStatus(d.pop("status"))

        user_id = UUID(d.pop("user_id"))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_input_data(data: object) -> JsonObject | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_data_type_0 = JsonObject.from_dict(data)

                return input_data_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(JsonObject | None | Unset, data)

        input_data = _parse_input_data(d.pop("input_data", UNSET))

        def _parse_job_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        job_id = _parse_job_id(d.pop("job_id", UNSET))

        def _parse_logs(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        logs = _parse_logs(d.pop("logs", UNSET))

        def _parse_output_data(data: object) -> JsonObject | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_data_type_0 = JsonObject.from_dict(data)

                return output_data_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(JsonObject | None | Unset, data)

        output_data = _parse_output_data(d.pop("output_data", UNSET))

        def _parse_revision_hash(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        revision_hash = _parse_revision_hash(d.pop("revision_hash", UNSET))

        def _parse_user_email(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        user_email = _parse_user_email(d.pop("user_email", UNSET))

        function_run_response = cls(
            completed_at=completed_at,
            created_at=created_at,
            function_id=function_id,
            id=id,
            started_at=started_at,
            status=status,
            user_id=user_id,
            error=error,
            input_data=input_data,
            job_id=job_id,
            logs=logs,
            output_data=output_data,
            revision_hash=revision_hash,
            user_email=user_email,
        )

        function_run_response.additional_properties = d
        return function_run_response

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
