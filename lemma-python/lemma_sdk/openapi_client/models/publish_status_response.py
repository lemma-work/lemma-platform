from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.publish_status import PublishStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.export_progress_response import ExportProgressResponse


T = TypeVar("T", bound="PublishStatusResponse")


@_attrs_define
class PublishStatusResponse:
    """Status of a pod publish job (pure Redis read).

    Attributes:
        events_url (str):
        pod_id (UUID):
        publish_id (UUID):
        repo_name (str):
        status (PublishStatus):
        error (None | str | Unset):
        progress (ExportProgressResponse | Unset):
        repo_url (None | str | Unset):
    """

    events_url: str
    pod_id: UUID
    publish_id: UUID
    repo_name: str
    status: PublishStatus
    error: None | str | Unset = UNSET
    progress: ExportProgressResponse | Unset = UNSET
    repo_url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        events_url = self.events_url

        pod_id = str(self.pod_id)

        publish_id = str(self.publish_id)

        repo_name = self.repo_name

        status = self.status.value

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        progress: dict[str, Any] | Unset = UNSET
        if not isinstance(self.progress, Unset):
            progress = self.progress.to_dict()

        repo_url: None | str | Unset
        if isinstance(self.repo_url, Unset):
            repo_url = UNSET
        else:
            repo_url = self.repo_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "events_url": events_url,
                "pod_id": pod_id,
                "publish_id": publish_id,
                "repo_name": repo_name,
                "status": status,
            }
        )
        if error is not UNSET:
            field_dict["error"] = error
        if progress is not UNSET:
            field_dict["progress"] = progress
        if repo_url is not UNSET:
            field_dict["repo_url"] = repo_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.export_progress_response import ExportProgressResponse

        d = dict(src_dict)
        events_url = d.pop("events_url")

        pod_id = UUID(d.pop("pod_id"))

        publish_id = UUID(d.pop("publish_id"))

        repo_name = d.pop("repo_name")

        status = PublishStatus(d.pop("status"))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        _progress = d.pop("progress", UNSET)
        progress: ExportProgressResponse | Unset
        if isinstance(_progress, Unset):
            progress = UNSET
        else:
            progress = ExportProgressResponse.from_dict(_progress)

        def _parse_repo_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        repo_url = _parse_repo_url(d.pop("repo_url", UNSET))

        publish_status_response = cls(
            events_url=events_url,
            pod_id=pod_id,
            publish_id=publish_id,
            repo_name=repo_name,
            status=status,
            error=error,
            progress=progress,
            repo_url=repo_url,
        )

        publish_status_response.additional_properties = d
        return publish_status_response

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
