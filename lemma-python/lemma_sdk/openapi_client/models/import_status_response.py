from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.import_status import ImportStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.export_progress_response import ExportProgressResponse
    from ..models.import_plan_response import ImportPlanResponse


T = TypeVar("T", bound="ImportStatusResponse")


@_attrs_define
class ImportStatusResponse:
    """Status of a pod import job (pure Redis read).

    Attributes:
        events_url (str):
        import_id (UUID):
        pod_id (UUID):
        source_kind (str):
        status (ImportStatus):
        error (None | str | Unset):
        plan (ImportPlanResponse | None | Unset):
        progress (ExportProgressResponse | Unset):
    """

    events_url: str
    import_id: UUID
    pod_id: UUID
    source_kind: str
    status: ImportStatus
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
