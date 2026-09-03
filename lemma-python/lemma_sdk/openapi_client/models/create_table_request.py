from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.column_schema import ColumnSchema
    from ..models.create_table_request_config_type_0 import (
        CreateTableRequestConfigType0,
    )


T = TypeVar("T", bound="CreateTableRequest")


@_attrs_define
class CreateTableRequest:
    """Schema for creating a new table.

    Attributes:
        columns (list[ColumnSchema]): Table column definitions. Each column name must be unique. Use `type`, `required`,
            `default`, `foreign_key`, and `computed` as needed. The backend also materializes physical system columns so
            table metadata reflects the real schema: `id` when omitted as the primary key, `created_at`, `updated_at`, and
            `user_id` when RLS is enabled.
        name (str): Table name. Use alphanumeric and underscore only, at most 63 bytes — PostgreSQL truncates longer
            names and two that share that prefix would become one table. Names prefixed with `reserved_` are system-managed
            and should not be user-created.
        config (CreateTableRequestConfigType0 | None | Unset): Optional table metadata/configuration. This updates table
            config metadata and does not directly alter physical columns.
        enable_rls (bool | Unset): Enable row-level security for this table. When enabled, API reads/writes are scoped
            by current user. Default: True.
        primary_key_column (str | Unset): Primary key column name. If not `id`, it must also be declared in `columns`.
            Default: 'id'.
        visibility (None | str | Unset):
    """

    columns: list[ColumnSchema]
    name: str
    config: CreateTableRequestConfigType0 | None | Unset = UNSET
    enable_rls: bool | Unset = True
    primary_key_column: str | Unset = "id"
    visibility: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.create_table_request_config_type_0 import (
            CreateTableRequestConfigType0,
        )

        columns = []
        for columns_item_data in self.columns:
            columns_item = columns_item_data.to_dict()
            columns.append(columns_item)

        name = self.name

        config: dict[str, Any] | None | Unset
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, CreateTableRequestConfigType0):
            config = self.config.to_dict()
        else:
            config = self.config

        enable_rls = self.enable_rls

        primary_key_column = self.primary_key_column

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "columns": columns,
                "name": name,
            }
        )
        if config is not UNSET:
            field_dict["config"] = config
        if enable_rls is not UNSET:
            field_dict["enable_rls"] = enable_rls
        if primary_key_column is not UNSET:
            field_dict["primary_key_column"] = primary_key_column
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.column_schema import ColumnSchema
        from ..models.create_table_request_config_type_0 import (
            CreateTableRequestConfigType0,
        )

        d = dict(src_dict)
        columns = []
        _columns = d.pop("columns")
        for columns_item_data in _columns:
            columns_item = ColumnSchema.from_dict(columns_item_data)

            columns.append(columns_item)

        name = d.pop("name")

        def _parse_config(data: object) -> CreateTableRequestConfigType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = CreateTableRequestConfigType0.from_dict(data)

                return config_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(CreateTableRequestConfigType0 | None | Unset, data)

        config = _parse_config(d.pop("config", UNSET))

        enable_rls = d.pop("enable_rls", UNSET)

        primary_key_column = d.pop("primary_key_column", UNSET)

        def _parse_visibility(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        create_table_request = cls(
            columns=columns,
            name=name,
            config=config,
            enable_rls=enable_rls,
            primary_key_column=primary_key_column,
            visibility=visibility,
        )

        create_table_request.additional_properties = d
        return create_table_request

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
