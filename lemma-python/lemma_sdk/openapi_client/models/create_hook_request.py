from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_hook_request_fields_type_0 import CreateHookRequestFieldsType0


T = TypeVar("T", bound="CreateHookRequest")


@_attrs_define
class CreateHookRequest:
    """
    Attributes:
        name (str): Lowercase letters, digits, '-' or '_'.
        table (str): The table each delivery adds a row to.
        fields (CreateHookRequestFieldsType0 | None | Unset): Column name to a dotted path into the posted JSON
            (`email`, `form_response.hidden.ref`, `answers.0.text`; `$` is the whole body). Leave it out to write the whole
            body to the table's `payload` JSON column. Either way `received_at` and `hook` are filled when the table has
            them.
    """

    name: str
    table: str
    fields: CreateHookRequestFieldsType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.create_hook_request_fields_type_0 import (
            CreateHookRequestFieldsType0,
        )

        name = self.name

        table = self.table

        fields: dict[str, Any] | None | Unset
        if isinstance(self.fields, Unset):
            fields = UNSET
        elif isinstance(self.fields, CreateHookRequestFieldsType0):
            fields = self.fields.to_dict()
        else:
            fields = self.fields

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "table": table,
            }
        )
        if fields is not UNSET:
            field_dict["fields"] = fields

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_hook_request_fields_type_0 import (
            CreateHookRequestFieldsType0,
        )

        d = dict(src_dict)
        name = d.pop("name")

        table = d.pop("table")

        def _parse_fields(data: object) -> CreateHookRequestFieldsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                fields_type_0 = CreateHookRequestFieldsType0.from_dict(data)

                return fields_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(CreateHookRequestFieldsType0 | None | Unset, data)

        fields = _parse_fields(d.pop("fields", UNSET))

        create_hook_request = cls(
            name=name,
            table=table,
            fields=fields,
        )

        create_hook_request.additional_properties = d
        return create_hook_request

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
