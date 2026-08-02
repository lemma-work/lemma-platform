from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.step_status import StepStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="StepRecordResponse")


@_attrs_define
class StepRecordResponse:
    """
    Attributes:
        node_id (str):
        started_at (datetime.datetime):
        status (StepStatus):
        step_index (int):
        completed_at (datetime.datetime | None | Unset):
        error (None | str | Unset):
        external_ref (None | str | Unset):
        output_data (Any | None | Unset):
    """

    node_id: str
    started_at: datetime.datetime
    status: StepStatus
    step_index: int
    completed_at: datetime.datetime | None | Unset = UNSET
    error: None | str | Unset = UNSET
    external_ref: None | str | Unset = UNSET
    output_data: Any | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        node_id = self.node_id

        started_at = self.started_at.isoformat()

        status = self.status.value

        step_index = self.step_index

        completed_at: None | str | Unset
        if isinstance(self.completed_at, Unset):
            completed_at = UNSET
        elif isinstance(self.completed_at, datetime.datetime):
            completed_at = self.completed_at.isoformat()
        else:
            completed_at = self.completed_at

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        external_ref: None | str | Unset
        if isinstance(self.external_ref, Unset):
            external_ref = UNSET
        else:
            external_ref = self.external_ref

        output_data: Any | None | Unset
        if isinstance(self.output_data, Unset):
            output_data = UNSET
        else:
            output_data = self.output_data

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "node_id": node_id,
                "started_at": started_at,
                "status": status,
                "step_index": step_index,
            }
        )
        if completed_at is not UNSET:
            field_dict["completed_at"] = completed_at
        if error is not UNSET:
            field_dict["error"] = error
        if external_ref is not UNSET:
            field_dict["external_ref"] = external_ref
        if output_data is not UNSET:
            field_dict["output_data"] = output_data

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        node_id = d.pop("node_id")

        started_at = isoparse(d.pop("started_at"))

        status = StepStatus(d.pop("status"))

        step_index = d.pop("step_index")

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
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        completed_at = _parse_completed_at(d.pop("completed_at", UNSET))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_external_ref(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        external_ref = _parse_external_ref(d.pop("external_ref", UNSET))

        def _parse_output_data(data: object) -> Any | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Any | None | Unset, data)

        output_data = _parse_output_data(d.pop("output_data", UNSET))

        step_record_response = cls(
            node_id=node_id,
            started_at=started_at,
            status=status,
            step_index=step_index,
            completed_at=completed_at,
            error=error,
            external_ref=external_ref,
            output_data=output_data,
        )

        step_record_response.additional_properties = d
        return step_record_response

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
