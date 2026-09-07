from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.my_usage_limits_response_plan_type_type_0 import (
    MyUsageLimitsResponsePlanTypeType0,
)

if TYPE_CHECKING:
    from ..models.usage_allowance_response import UsageAllowanceResponse


T = TypeVar("T", bound="MyUsageLimitsResponse")


@_attrs_define
class MyUsageLimitsResponse:
    """
    Attributes:
        allowed (bool):
        organization_id (None | UUID):
        plan_name (None | str):
        plan_type (MyUsageLimitsResponsePlanTypeType0 | None):
        warning_percent (float):
        windows (list[UsageAllowanceResponse]):
    """

    allowed: bool
    organization_id: None | UUID
    plan_name: None | str
    plan_type: MyUsageLimitsResponsePlanTypeType0 | None
    warning_percent: float
    windows: list[UsageAllowanceResponse]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        allowed = self.allowed

        organization_id: None | str
        if isinstance(self.organization_id, UUID):
            organization_id = str(self.organization_id)
        else:
            organization_id = self.organization_id

        plan_name: None | str
        plan_name = self.plan_name

        plan_type: None | str
        if isinstance(self.plan_type, MyUsageLimitsResponsePlanTypeType0):
            plan_type = self.plan_type.value
        else:
            plan_type = self.plan_type

        warning_percent = self.warning_percent

        windows = []
        for windows_item_data in self.windows:
            windows_item = windows_item_data.to_dict()
            windows.append(windows_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "allowed": allowed,
                "organization_id": organization_id,
                "plan_name": plan_name,
                "plan_type": plan_type,
                "warning_percent": warning_percent,
                "windows": windows,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.usage_allowance_response import UsageAllowanceResponse

        d = dict(src_dict)
        allowed = d.pop("allowed")

        def _parse_organization_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                organization_id_type_0 = UUID(data)

                return organization_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        organization_id = _parse_organization_id(d.pop("organization_id"))

        def _parse_plan_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        plan_name = _parse_plan_name(d.pop("plan_name"))

        def _parse_plan_type(data: object) -> MyUsageLimitsResponsePlanTypeType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                plan_type_type_0 = MyUsageLimitsResponsePlanTypeType0(data)

                return plan_type_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(MyUsageLimitsResponsePlanTypeType0 | None, data)

        plan_type = _parse_plan_type(d.pop("plan_type"))

        warning_percent = d.pop("warning_percent")

        windows = []
        _windows = d.pop("windows")
        for windows_item_data in _windows:
            windows_item = UsageAllowanceResponse.from_dict(windows_item_data)

            windows.append(windows_item)

        my_usage_limits_response = cls(
            allowed=allowed,
            organization_id=organization_id,
            plan_name=plan_name,
            plan_type=plan_type,
            warning_percent=warning_percent,
            windows=windows,
        )

        my_usage_limits_response.additional_properties = d
        return my_usage_limits_response

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
