from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.publish_mode import PublishMode
from ..types import UNSET, Unset

T = TypeVar("T", bound="PublishStartRequest")


@_attrs_define
class PublishStartRequest:
    """Body for publishing a pod to GitHub.

    Attributes:
        account_id (UUID): GitHub connector account to publish as.
        repo_name (str): GitHub repository name (letters, numbers, dot, dash, underscore).
        ai_readme (bool | Unset): Polish the generated README with the system model. Default: False.
        mode (PublishMode | Unset):
        private (bool | Unset): Create the repo as private. Default: False.
    """

    account_id: UUID
    repo_name: str
    ai_readme: bool | Unset = False
    mode: PublishMode | Unset = UNSET
    private: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        account_id = str(self.account_id)

        repo_name = self.repo_name

        ai_readme = self.ai_readme

        mode: str | Unset = UNSET
        if not isinstance(self.mode, Unset):
            mode = self.mode.value

        private = self.private

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "account_id": account_id,
                "repo_name": repo_name,
            }
        )
        if ai_readme is not UNSET:
            field_dict["ai_readme"] = ai_readme
        if mode is not UNSET:
            field_dict["mode"] = mode
        if private is not UNSET:
            field_dict["private"] = private

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        account_id = UUID(d.pop("account_id"))

        repo_name = d.pop("repo_name")

        ai_readme = d.pop("ai_readme", UNSET)

        _mode = d.pop("mode", UNSET)
        mode: PublishMode | Unset
        if isinstance(_mode, Unset):
            mode = UNSET
        else:
            mode = PublishMode(_mode)

        private = d.pop("private", UNSET)

        publish_start_request = cls(
            account_id=account_id,
            repo_name=repo_name,
            ai_readme=ai_readme,
            mode=mode,
            private=private,
        )

        publish_start_request.additional_properties = d
        return publish_start_request

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
