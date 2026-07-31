from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.operation_detail_input_schema import OperationDetailInputSchema
    from ..models.operation_detail_output_schema_type_0 import (
        OperationDetailOutputSchemaType0,
    )


T = TypeVar("T", bound="OperationDetail")


@_attrs_define
class OperationDetail:
    """Full operation metadata including input and output schemas.

    Attributes:
        input_schema (OperationDetailInputSchema):
        name (str):
        description (None | str | Unset):
        output_schema (None | OperationDetailOutputSchemaType0 | Unset):
    """

    input_schema: OperationDetailInputSchema
    name: str
    description: None | str | Unset = UNSET
    output_schema: None | OperationDetailOutputSchemaType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.operation_detail_output_schema_type_0 import (
            OperationDetailOutputSchemaType0,
        )

        input_schema = self.input_schema.to_dict()

        name = self.name

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        output_schema: dict[str, Any] | None | Unset
        if isinstance(self.output_schema, Unset):
            output_schema = UNSET
        elif isinstance(self.output_schema, OperationDetailOutputSchemaType0):
            output_schema = self.output_schema.to_dict()
        else:
            output_schema = self.output_schema

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "input_schema": input_schema,
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if output_schema is not UNSET:
            field_dict["output_schema"] = output_schema

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.operation_detail_input_schema import OperationDetailInputSchema
        from ..models.operation_detail_output_schema_type_0 import (
            OperationDetailOutputSchemaType0,
        )

        d = dict(src_dict)
        input_schema = OperationDetailInputSchema.from_dict(d.pop("input_schema"))

        name = d.pop("name")

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_output_schema(
            data: object,
        ) -> None | OperationDetailOutputSchemaType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                output_schema_type_0 = OperationDetailOutputSchemaType0.from_dict(data)

                return output_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | OperationDetailOutputSchemaType0 | Unset, data)

        output_schema = _parse_output_schema(d.pop("output_schema", UNSET))

        operation_detail = cls(
            input_schema=input_schema,
            name=name,
            description=description,
            output_schema=output_schema,
        )

        operation_detail.additional_properties = d
        return operation_detail

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
