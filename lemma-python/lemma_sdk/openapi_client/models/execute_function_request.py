from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.json_object import JsonObject


T = TypeVar("T", bound="ExecuteFunctionRequest")


@_attrs_define
class ExecuteFunctionRequest:
    """Request to execute a function.

    Attributes:
        input_data (JsonObject | Unset):
    """

    input_data: JsonObject | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        input_data: dict[str, Any] | Unset = UNSET
        if not isinstance(self.input_data, Unset):
            input_data = self.input_data.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if input_data is not UNSET:
            field_dict["input_data"] = input_data

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.json_object import JsonObject

        d = dict(src_dict)
        _input_data = d.pop("input_data", UNSET)
        input_data: JsonObject | Unset
        if isinstance(_input_data, Unset):
            input_data = UNSET
        else:
            input_data = JsonObject.from_dict(_input_data)

        execute_function_request = cls(
            input_data=input_data,
        )

        return execute_function_request
