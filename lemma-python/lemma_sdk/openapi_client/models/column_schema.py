from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.datastore_data_type import DatastoreDataType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.column_schema_type_params_type_0 import ColumnSchemaTypeParamsType0
    from ..models.foreign_key_spec import ForeignKeySpec


T = TypeVar("T", bound="ColumnSchema")


@_attrs_define
class ColumnSchema:
    """Schema for a datastore table column.

    Attributes:
        name (str): Column name
        type_ (DatastoreDataType): Supported data types for datastore columns.
        auto (bool | Unset): Whether the column is auto-generated Default: False.
        computed (bool | Unset): Whether this is a computed column Default: False.
        default (Any | None | Unset): Default value for the column
        description (None | str | Unset): Column description
        expression (None | str | Unset): SQL expression for computed columns
        foreign_key (ForeignKeySpec | None | Unset): Foreign key specification
        max_length (int | None | Unset): Maximum length for TEXT columns
        options (list[str] | None | Unset): Allowed options for ENUM columns
        required (bool | Unset): Whether the column is required (NOT NULL) Default: False.
        system (bool | Unset): Whether the column is system-managed by the backend. Default: False.
        type_params (ColumnSchemaTypeParamsType0 | None | Unset): Additional type-specific parameters
        unique (bool | Unset): Whether the column must have unique values Default: False.
    """

    name: str
    type_: DatastoreDataType
    auto: bool | Unset = False
    computed: bool | Unset = False
    default: Any | None | Unset = UNSET
    description: None | str | Unset = UNSET
    expression: None | str | Unset = UNSET
    foreign_key: ForeignKeySpec | None | Unset = UNSET
    max_length: int | None | Unset = UNSET
    options: list[str] | None | Unset = UNSET
    required: bool | Unset = False
    system: bool | Unset = False
    type_params: ColumnSchemaTypeParamsType0 | None | Unset = UNSET
    unique: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.column_schema_type_params_type_0 import (
            ColumnSchemaTypeParamsType0,
        )
        from ..models.foreign_key_spec import ForeignKeySpec

        name = self.name

        type_ = self.type_.value

        auto = self.auto

        computed = self.computed

        default: Any | None | Unset
        if isinstance(self.default, Unset):
            default = UNSET
        else:
            default = self.default

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        expression: None | str | Unset
        if isinstance(self.expression, Unset):
            expression = UNSET
        else:
            expression = self.expression

        foreign_key: dict[str, Any] | None | Unset
        if isinstance(self.foreign_key, Unset):
            foreign_key = UNSET
        elif isinstance(self.foreign_key, ForeignKeySpec):
            foreign_key = self.foreign_key.to_dict()
        else:
            foreign_key = self.foreign_key

        max_length: int | None | Unset
        if isinstance(self.max_length, Unset):
            max_length = UNSET
        else:
            max_length = self.max_length

        options: list[str] | None | Unset
        if isinstance(self.options, Unset):
            options = UNSET
        elif isinstance(self.options, list):
            options = self.options

        else:
            options = self.options

        required = self.required

        system = self.system

        type_params: dict[str, Any] | None | Unset
        if isinstance(self.type_params, Unset):
            type_params = UNSET
        elif isinstance(self.type_params, ColumnSchemaTypeParamsType0):
            type_params = self.type_params.to_dict()
        else:
            type_params = self.type_params

        unique = self.unique

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "type": type_,
            }
        )
        if auto is not UNSET:
            field_dict["auto"] = auto
        if computed is not UNSET:
            field_dict["computed"] = computed
        if default is not UNSET:
            field_dict["default"] = default
        if description is not UNSET:
            field_dict["description"] = description
        if expression is not UNSET:
            field_dict["expression"] = expression
        if foreign_key is not UNSET:
            field_dict["foreign_key"] = foreign_key
        if max_length is not UNSET:
            field_dict["max_length"] = max_length
        if options is not UNSET:
            field_dict["options"] = options
        if required is not UNSET:
            field_dict["required"] = required
        if system is not UNSET:
            field_dict["system"] = system
        if type_params is not UNSET:
            field_dict["type_params"] = type_params
        if unique is not UNSET:
            field_dict["unique"] = unique

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.column_schema_type_params_type_0 import (
            ColumnSchemaTypeParamsType0,
        )
        from ..models.foreign_key_spec import ForeignKeySpec

        d = dict(src_dict)
        name = d.pop("name")

        type_ = DatastoreDataType(d.pop("type"))

        auto = d.pop("auto", UNSET)

        computed = d.pop("computed", UNSET)

        def _parse_default(data: object) -> Any | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Any | None | Unset, data)

        default = _parse_default(d.pop("default", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_expression(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        expression = _parse_expression(d.pop("expression", UNSET))

        def _parse_foreign_key(data: object) -> ForeignKeySpec | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                foreign_key_type_0 = ForeignKeySpec.from_dict(data)

                return foreign_key_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(ForeignKeySpec | None | Unset, data)

        foreign_key = _parse_foreign_key(d.pop("foreign_key", UNSET))

        def _parse_max_length(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        max_length = _parse_max_length(d.pop("max_length", UNSET))

        def _parse_options(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                options_type_0 = cast(list[str], data)

                return options_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        options = _parse_options(d.pop("options", UNSET))

        required = d.pop("required", UNSET)

        system = d.pop("system", UNSET)

        def _parse_type_params(
            data: object,
        ) -> ColumnSchemaTypeParamsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                type_params_type_0 = ColumnSchemaTypeParamsType0.from_dict(data)

                return type_params_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(ColumnSchemaTypeParamsType0 | None | Unset, data)

        type_params = _parse_type_params(d.pop("type_params", UNSET))

        unique = d.pop("unique", UNSET)

        column_schema = cls(
            name=name,
            type_=type_,
            auto=auto,
            computed=computed,
            default=default,
            description=description,
            expression=expression,
            foreign_key=foreign_key,
            max_length=max_length,
            options=options,
            required=required,
            system=system,
            type_params=type_params,
            unique=unique,
        )

        column_schema.additional_properties = d
        return column_schema

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
