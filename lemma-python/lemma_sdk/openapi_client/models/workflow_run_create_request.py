from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.workflow_run_about import WorkflowRunAbout


T = TypeVar("T", bound="WorkflowRunCreateRequest")


@_attrs_define
class WorkflowRunCreateRequest:
    """What a person may give a run they start: at most the record it is about.

    Not free-form input, which a run never takes (inputs arrive through FORM
    nodes): the record is read by the platform, as the person starting the
    run, and handed to the run as ``start.payload`` -- the shape a table
    change gives it -- so one workflow serves both a table trigger and a
    button about one row.

        Attributes:
            about (None | Unset | WorkflowRunAbout): The record this run is about. It is read as you: a record you cannot
                read is a 404. Its fields become `start.payload`, and `start.metadata` carries `table_name` and `record_id`.
    """

    about: None | Unset | WorkflowRunAbout = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.workflow_run_about import WorkflowRunAbout

        about: dict[str, Any] | None | Unset
        if isinstance(self.about, Unset):
            about = UNSET
        elif isinstance(self.about, WorkflowRunAbout):
            about = self.about.to_dict()
        else:
            about = self.about

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if about is not UNSET:
            field_dict["about"] = about

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.workflow_run_about import WorkflowRunAbout

        d = dict(src_dict)

        def _parse_about(data: object) -> None | Unset | WorkflowRunAbout:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                about_type_0 = WorkflowRunAbout.from_dict(data)

                return about_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | WorkflowRunAbout, data)

        about = _parse_about(d.pop("about", UNSET))

        workflow_run_create_request = cls(
            about=about,
        )

        return workflow_run_create_request
