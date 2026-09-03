from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OperationDetailsBatchRequest")


@_attrs_define
class OperationDetailsBatchRequest:
    """Request multiple operation details in a single call.

    Attributes:
        limit (int | Unset): How many to return when `operation_names` is omitted. Ignored when names are given.
            Default: 100.
        operation_names (list[str] | None | Unset): Operation names to fetch. Omit or pass an empty list to return
            details for the first `limit` operations in the connector; read `total_operations` on the response to see
            whether that was all of them.
    """

    limit: int | Unset = 100
    operation_names: list[str] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        limit = self.limit

        operation_names: list[str] | None | Unset
        if isinstance(self.operation_names, Unset):
            operation_names = UNSET
        elif isinstance(self.operation_names, list):
            operation_names = self.operation_names

        else:
            operation_names = self.operation_names

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if limit is not UNSET:
            field_dict["limit"] = limit
        if operation_names is not UNSET:
            field_dict["operation_names"] = operation_names

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        limit = d.pop("limit", UNSET)

        def _parse_operation_names(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                operation_names_type_0 = cast(list[str], data)

                return operation_names_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        operation_names = _parse_operation_names(d.pop("operation_names", UNSET))

        operation_details_batch_request = cls(
            limit=limit,
            operation_names=operation_names,
        )

        operation_details_batch_request.additional_properties = d
        return operation_details_batch_request

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
