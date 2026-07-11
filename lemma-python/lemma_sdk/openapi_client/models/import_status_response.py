from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.import_status import ImportStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.export_progress_response import ExportProgressResponse
    from ..models.import_plan_response import ImportPlanResponse


T = TypeVar("T", bound="ImportStatusResponse")


@_attrs_define
class ImportStatusResponse:
    """Status of a durable pod import job.

    Attributes:
        events_url (str):
        import_id (UUID):
        pod_id (UUID):
        source_kind (str):
        status (ImportStatus):
        cancel_requested_at (datetime.datetime | None | Unset):
        committed_steps (list[int] | Unset):
        current_step (int | None | Unset):
        error (None | str | Unset):
        plan (ImportPlanResponse | None | Unset):
        progress (ExportProgressResponse | Unset):
    """

    events_url: str
    import_id: UUID
    pod_id: UUID
    source_kind: str
    status: ImportStatus
    cancel_requested_at: datetime.datetime | None | Unset = UNSET
    committed_steps: list[int] | Unset = UNSET
    current_step: int | None | Unset = UNSET
    error: None | str | Unset = UNSET
    plan: ImportPlanResponse | None | Unset = UNSET
    progress: ExportProgressResponse | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.import_plan_response import ImportPlanResponse

        events_url = self.events_url

        import_id = str(self.import_id)

        pod_id = str(self.pod_id)

        source_kind = self.source_kind

        status = self.status.value

        cancel_requested_at: None | str | Unset
        if isinstance(self.cancel_requested_at, Unset):
            cancel_requested_at = UNSET
        elif isinstance(self.cancel_requested_at, datetime.datetime):
            cancel_requested_at = self.cancel_requested_at.isoformat()
        else:
            cancel_requested_at = self.cancel_requested_at

        committed_steps: list[int] | Unset = UNSET
        if not isinstance(self.committed_steps, Unset):
            committed_steps = self.committed_steps

        current_step: int | None | Unset
        if isinstance(self.current_step, Unset):
            current_step = UNSET
        else:
            current_step = self.current_step

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        plan: dict[str, Any] | None | Unset
        if isinstance(self.plan, Unset):
            plan = UNSET
        elif isinstance(self.plan, ImportPlanResponse):
            plan = self.plan.to_dict()
        else:
            plan = self.plan

        progress: dict[str, Any] | Unset = UNSET
        if not isinstance(self.progress, Unset):
            progress = self.progress.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "events_url": events_url,
                "import_id": import_id,
                "pod_id": pod_id,
                "source_kind": source_kind,
                "status": status,
            }
        )
        if cancel_requested_at is not UNSET:
            field_dict["cancel_requested_at"] = cancel_requested_at
        if committed_steps is not UNSET:
            field_dict["committed_steps"] = committed_steps
        if current_step is not UNSET:
            field_dict["current_step"] = current_step
        if error is not UNSET:
            field_dict["error"] = error
        if plan is not UNSET:
            field_dict["plan"] = plan
        if progress is not UNSET:
            field_dict["progress"] = progress

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.export_progress_response import ExportProgressResponse
        from ..models.import_plan_response import ImportPlanResponse

        d = dict(src_dict)
        events_url = d.pop("events_url")

        import_id = UUID(d.pop("import_id"))

        pod_id = UUID(d.pop("pod_id"))

        source_kind = d.pop("source_kind")

        status = ImportStatus(d.pop("status"))

        def _parse_cancel_requested_at(
            data: object,
        ) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                cancel_requested_at_type_0 = isoparse(data)

                return cancel_requested_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        cancel_requested_at = _parse_cancel_requested_at(
            d.pop("cancel_requested_at", UNSET)
        )

        committed_steps = cast(list[int], d.pop("committed_steps", UNSET))

        def _parse_current_step(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        current_step = _parse_current_step(d.pop("current_step", UNSET))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_plan(data: object) -> ImportPlanResponse | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                plan_type_0 = ImportPlanResponse.from_dict(data)

                return plan_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ImportPlanResponse | None | Unset, data)

        plan = _parse_plan(d.pop("plan", UNSET))

        _progress = d.pop("progress", UNSET)
        progress: ExportProgressResponse | Unset
        if isinstance(_progress, Unset):
            progress = UNSET
        else:
            progress = ExportProgressResponse.from_dict(_progress)

        import_status_response = cls(
            events_url=events_url,
            import_id=import_id,
            pod_id=pod_id,
            source_kind=source_kind,
            status=status,
            cancel_requested_at=cancel_requested_at,
            committed_steps=committed_steps,
            current_step=current_step,
            error=error,
            plan=plan,
            progress=progress,
        )

        import_status_response.additional_properties = d
        return import_status_response

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
