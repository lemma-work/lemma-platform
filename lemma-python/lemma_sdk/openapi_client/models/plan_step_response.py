from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.plan_step_response_detail import PlanStepResponseDetail


T = TypeVar("T", bound="PlanStepResponse")


@_attrs_define
class PlanStepResponse:
    """
    Attributes:
        action (str):
        index (int):
        kind (str):
        name (str):
        destructive (bool | Unset):  Default: False.
        detail (PlanStepResponseDetail | Unset):
        error (None | str | Unset):
        status (str | Unset):  Default: 'PENDING'.
    """

    action: str
    index: int
    kind: str
    name: str
    destructive: bool | Unset = False
    detail: PlanStepResponseDetail | Unset = UNSET
    error: None | str | Unset = UNSET
    status: str | Unset = "PENDING"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        action = self.action

        index = self.index

        kind = self.kind

        name = self.name

        destructive = self.destructive

        detail: dict[str, Any] | Unset = UNSET
        if not isinstance(self.detail, Unset):
            detail = self.detail.to_dict()

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "action": action,
                "index": index,
                "kind": kind,
                "name": name,
            }
        )
        if destructive is not UNSET:
            field_dict["destructive"] = destructive
        if detail is not UNSET:
            field_dict["detail"] = detail
        if error is not UNSET:
            field_dict["error"] = error
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.plan_step_response_detail import PlanStepResponseDetail

        d = dict(src_dict)
        action = d.pop("action")

        index = d.pop("index")

        kind = d.pop("kind")

        name = d.pop("name")

        destructive = d.pop("destructive", UNSET)

        _detail = d.pop("detail", UNSET)
        detail: PlanStepResponseDetail | Unset
        if isinstance(_detail, Unset):
            detail = UNSET
        else:
            detail = PlanStepResponseDetail.from_dict(_detail)

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        status = d.pop("status", UNSET)

        plan_step_response = cls(
            action=action,
            index=index,
            kind=kind,
            name=name,
            destructive=destructive,
            detail=detail,
            error=error,
            status=status,
        )

        plan_step_response.additional_properties = d
        return plan_step_response

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
