from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.datastore_operation import DatastoreOperation
from ..types import UNSET, Unset

T = TypeVar("T", bound="DataStoreWorkflowStartConfigOutput")


@_attrs_define
class DataStoreWorkflowStartConfigOutput:
    """
    Attributes:
        table_name (str): Table name inside the datastore to subscribe to.
        operations (list[DatastoreOperation] | Unset): Datastore operations that should trigger this flow. One or more
            of INSERT, UPDATE, DELETE.
    """

    table_name: str
    operations: list[DatastoreOperation] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        table_name = self.table_name

        operations: list[str] | Unset = UNSET
        if not isinstance(self.operations, Unset):
            operations = []
            for operations_item_data in self.operations:
                operations_item = operations_item_data.value
                operations.append(operations_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "table_name": table_name,
            }
        )
        if operations is not UNSET:
            field_dict["operations"] = operations

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        table_name = d.pop("table_name")

        _operations = d.pop("operations", UNSET)
        operations: list[DatastoreOperation] | Unset = UNSET
        if _operations is not UNSET:
            operations = []
            for operations_item_data in _operations:
                operations_item = DatastoreOperation(operations_item_data)

                operations.append(operations_item)

        data_store_workflow_start_config_output = cls(
            table_name=table_name,
            operations=operations,
        )

        data_store_workflow_start_config_output.additional_properties = d
        return data_store_workflow_start_config_output

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
