from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.plan_step_response import PlanStepResponse
    from ..models.variable_spec_response import VariableSpecResponse


T = TypeVar("T", bound="ImportPlanResponse")


@_attrs_define
class ImportPlanResponse:
    """
    Attributes:
        format_version (int):
        bundle_name (None | str | Unset):
        has_destructive_steps (bool | Unset):  Default: False.
        steps (list[PlanStepResponse] | Unset):
        variables (list[VariableSpecResponse] | Unset):
        warnings (list[str] | Unset):
    """

    format_version: int
    bundle_name: None | str | Unset = UNSET
    has_destructive_steps: bool | Unset = False
    steps: list[PlanStepResponse] | Unset = UNSET
    variables: list[VariableSpecResponse] | Unset = UNSET
    warnings: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        format_version = self.format_version

        bundle_name: None | str | Unset
        if isinstance(self.bundle_name, Unset):
            bundle_name = UNSET
        else:
            bundle_name = self.bundle_name

        has_destructive_steps = self.has_destructive_steps

        steps: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.steps, Unset):
            steps = []
            for steps_item_data in self.steps:
                steps_item = steps_item_data.to_dict()
                steps.append(steps_item)

        variables: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.variables, Unset):
            variables = []
            for variables_item_data in self.variables:
                variables_item = variables_item_data.to_dict()
                variables.append(variables_item)

        warnings: list[str] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = self.warnings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "format_version": format_version,
            }
        )
        if bundle_name is not UNSET:
            field_dict["bundle_name"] = bundle_name
        if has_destructive_steps is not UNSET:
            field_dict["has_destructive_steps"] = has_destructive_steps
        if steps is not UNSET:
            field_dict["steps"] = steps
        if variables is not UNSET:
            field_dict["variables"] = variables
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.plan_step_response import PlanStepResponse
        from ..models.variable_spec_response import VariableSpecResponse

        d = dict(src_dict)
        format_version = d.pop("format_version")

        def _parse_bundle_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        bundle_name = _parse_bundle_name(d.pop("bundle_name", UNSET))

        has_destructive_steps = d.pop("has_destructive_steps", UNSET)

        _steps = d.pop("steps", UNSET)
        steps: list[PlanStepResponse] | Unset = UNSET
        if _steps is not UNSET:
            steps = []
            for steps_item_data in _steps:
                steps_item = PlanStepResponse.from_dict(steps_item_data)

                steps.append(steps_item)

        _variables = d.pop("variables", UNSET)
        variables: list[VariableSpecResponse] | Unset = UNSET
        if _variables is not UNSET:
            variables = []
            for variables_item_data in _variables:
                variables_item = VariableSpecResponse.from_dict(variables_item_data)

                variables.append(variables_item)

        warnings = cast(list[str], d.pop("warnings", UNSET))

        import_plan_response = cls(
            format_version=format_version,
            bundle_name=bundle_name,
            has_destructive_steps=has_destructive_steps,
            steps=steps,
            variables=variables,
            warnings=warnings,
        )

        import_plan_response.additional_properties = d
        return import_plan_response

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
