from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="WorkflowRunAbout")


@_attrs_define
class WorkflowRunAbout:
    """One record a run is about: a row of a table in the workflow's pod.

    Attributes:
        record_id (str): The record's primary key.
        table (str): The table's name.
    """

    record_id: str
    table: str

    def to_dict(self) -> dict[str, Any]:
        record_id = self.record_id

        table = self.table

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "record_id": record_id,
                "table": table,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        record_id = d.pop("record_id")

        table = d.pop("table")

        workflow_run_about = cls(
            record_id=record_id,
            table=table,
        )

        return workflow_run_about
