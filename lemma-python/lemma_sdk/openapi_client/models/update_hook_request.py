from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.update_hook_request_fields_type_0 import UpdateHookRequestFieldsType0


T = TypeVar("T", bound="UpdateHookRequest")


@_attrs_define
class UpdateHookRequest:
    """
    Attributes:
        enabled (bool | None | Unset):
        fields (None | Unset | UpdateHookRequestFieldsType0): Column name to a dotted path into the posted JSON
            (`email`, `form_response.hidden.ref`, `answers.0.text`; `$` is the whole body). Leave it out to write the whole
            body to the table's `payload` JSON column. Either way `received_at` and `hook` are filled when the table has
            them. Send null to go back to the whole body.
    """

    enabled: bool | None | Unset = UNSET
    fields: None | Unset | UpdateHookRequestFieldsType0 = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.update_hook_request_fields_type_0 import (
            UpdateHookRequestFieldsType0,
        )

        enabled: bool | None | Unset
        if isinstance(self.enabled, Unset):
            enabled = UNSET
        else:
            enabled = self.enabled

        fields: dict[str, Any] | None | Unset
        if isinstance(self.fields, Unset):
            fields = UNSET
        elif isinstance(self.fields, UpdateHookRequestFieldsType0):
            fields = self.fields.to_dict()
        else:
            fields = self.fields

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if fields is not UNSET:
            field_dict["fields"] = fields

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.update_hook_request_fields_type_0 import (
            UpdateHookRequestFieldsType0,
        )

        d = dict(src_dict)

        def _parse_enabled(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        enabled = _parse_enabled(d.pop("enabled", UNSET))

        def _parse_fields(data: object) -> None | Unset | UpdateHookRequestFieldsType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                fields_type_0 = UpdateHookRequestFieldsType0.from_dict(data)

                return fields_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UpdateHookRequestFieldsType0, data)

        fields = _parse_fields(d.pop("fields", UNSET))

        update_hook_request = cls(
            enabled=enabled,
            fields=fields,
        )

        update_hook_request.additional_properties = d
        return update_hook_request

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
