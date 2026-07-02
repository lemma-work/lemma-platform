from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.apply_import_request_variables import ApplyImportRequestVariables


T = TypeVar("T", bound="ApplyImportRequest")


@_attrs_define
class ApplyImportRequest:
    """Body for applying a planned import.

    Attributes:
        confirm_destructive (bool | Unset): Required to proceed when the plan has destructive steps. Default: False.
        variables (ApplyImportRequestVariables | Unset): Resolved values for the plan's ${var} placeholders.
    """

    confirm_destructive: bool | Unset = False
    variables: ApplyImportRequestVariables | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        confirm_destructive = self.confirm_destructive

        variables: dict[str, Any] | Unset = UNSET
        if not isinstance(self.variables, Unset):
            variables = self.variables.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if confirm_destructive is not UNSET:
            field_dict["confirm_destructive"] = confirm_destructive
        if variables is not UNSET:
            field_dict["variables"] = variables

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.apply_import_request_variables import ApplyImportRequestVariables

        d = dict(src_dict)
        confirm_destructive = d.pop("confirm_destructive", UNSET)

        _variables = d.pop("variables", UNSET)
        variables: ApplyImportRequestVariables | Unset
        if isinstance(_variables, Unset):
            variables = UNSET
        else:
            variables = ApplyImportRequestVariables.from_dict(_variables)

        apply_import_request = cls(
            confirm_destructive=confirm_destructive,
            variables=variables,
        )

        apply_import_request.additional_properties = d
        return apply_import_request

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
