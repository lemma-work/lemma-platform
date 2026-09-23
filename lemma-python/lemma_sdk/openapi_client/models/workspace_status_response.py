from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.workspace_status_response_state import WorkspaceStatusResponseState
from ..types import UNSET, Unset

T = TypeVar("T", bound="WorkspaceStatusResponse")


@_attrs_define
class WorkspaceStatusResponse:
    """
    Attributes:
        state (WorkspaceStatusResponseState): `ready`: running. `downloading`: fetching its image, which the first start
            after an update does. `starting`: coming up. `asleep`: not running, and starts on first use. `unavailable`:
            could not be asked.
        detail (None | str | Unset): A sentence for a person.
    """

    state: WorkspaceStatusResponseState
    detail: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        state = self.state.value

        detail: None | str | Unset
        if isinstance(self.detail, Unset):
            detail = UNSET
        else:
            detail = self.detail

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "state": state,
            }
        )
        if detail is not UNSET:
            field_dict["detail"] = detail

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        state = WorkspaceStatusResponseState(d.pop("state"))

        def _parse_detail(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        detail = _parse_detail(d.pop("detail", UNSET))

        workspace_status_response = cls(
            state=state,
            detail=detail,
        )

        workspace_status_response.additional_properties = d
        return workspace_status_response

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
