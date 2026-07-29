from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.publish_mode import PublishMode
from ..models.publish_status import PublishStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.export_progress_response import ExportProgressResponse


T = TypeVar("T", bound="PublishStatusResponse")


@_attrs_define
class PublishStatusResponse:
    """Status of a pod publish job (pure Redis read).

    Attributes:
        account_id (None | UUID):
        events_url (str):
        mode (PublishMode):
        pod_id (UUID):
        private (bool):
        publish_id (UUID):
        repo_name (str):
        status (PublishStatus):
        error (None | str | Unset):
        error_code (None | str | Unset):
        progress (ExportProgressResponse | Unset):
        repo_url (None | str | Unset):
        retryable (bool | Unset):  Default: False.
        warnings (list[str] | Unset):
    """

    account_id: None | UUID
    events_url: str
    mode: PublishMode
    pod_id: UUID
    private: bool
    publish_id: UUID
    repo_name: str
    status: PublishStatus
    error: None | str | Unset = UNSET
    error_code: None | str | Unset = UNSET
    progress: ExportProgressResponse | Unset = UNSET
    repo_url: None | str | Unset = UNSET
    retryable: bool | Unset = False
    warnings: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        account_id: None | str
        if isinstance(self.account_id, UUID):
            account_id = str(self.account_id)
        else:
            account_id = self.account_id

        events_url = self.events_url

        mode = self.mode.value

        pod_id = str(self.pod_id)

        private = self.private

        publish_id = str(self.publish_id)

        repo_name = self.repo_name

        status = self.status.value

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        error_code: None | str | Unset
        if isinstance(self.error_code, Unset):
            error_code = UNSET
        else:
            error_code = self.error_code

        progress: dict[str, Any] | Unset = UNSET
        if not isinstance(self.progress, Unset):
            progress = self.progress.to_dict()

        repo_url: None | str | Unset
        if isinstance(self.repo_url, Unset):
            repo_url = UNSET
        else:
            repo_url = self.repo_url

        retryable = self.retryable

        warnings: list[str] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = self.warnings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "account_id": account_id,
                "events_url": events_url,
                "mode": mode,
                "pod_id": pod_id,
                "private": private,
                "publish_id": publish_id,
                "repo_name": repo_name,
                "status": status,
            }
        )
        if error is not UNSET:
            field_dict["error"] = error
        if error_code is not UNSET:
            field_dict["error_code"] = error_code
        if progress is not UNSET:
            field_dict["progress"] = progress
        if repo_url is not UNSET:
            field_dict["repo_url"] = repo_url
        if retryable is not UNSET:
            field_dict["retryable"] = retryable
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.export_progress_response import ExportProgressResponse

        d = dict(src_dict)

        def _parse_account_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                account_id_type_0 = UUID(data)

                return account_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        account_id = _parse_account_id(d.pop("account_id"))

        events_url = d.pop("events_url")

        mode = PublishMode(d.pop("mode"))

        pod_id = UUID(d.pop("pod_id"))

        private = d.pop("private")

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

        def _parse_error_code(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error_code = _parse_error_code(d.pop("error_code", UNSET))

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

        retryable = d.pop("retryable", UNSET)

        warnings = cast(list[str], d.pop("warnings", UNSET))

        publish_status_response = cls(
            account_id=account_id,
            events_url=events_url,
            mode=mode,
            pod_id=pod_id,
            private=private,
            publish_id=publish_id,
            repo_name=repo_name,
            status=status,
            error=error,
            error_code=error_code,
            progress=progress,
            repo_url=repo_url,
            retryable=retryable,
            warnings=warnings,
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
