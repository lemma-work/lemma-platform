from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.update_table_request_config_type_0 import (
        UpdateTableRequestConfigType0,
    )


T = TypeVar("T", bound="UpdateTableRequest")


@_attrs_define
class UpdateTableRequest:
    """Schema for updating a table.

    Attributes:
        config (None | Unset | UpdateTableRequestConfigType0): Replacement metadata/config payload for the table.
        enable_rls (bool | None | Unset): Toggle per-user row-level security. Only allowed on an empty table: enabling
            adds the user_id ownership column and isolation policy, disabling removes the policy. Omit to leave RLS
            unchanged.
        visibility (None | str | Unset):
    """

    config: None | Unset | UpdateTableRequestConfigType0 = UNSET
    enable_rls: bool | None | Unset = UNSET
    visibility: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.update_table_request_config_type_0 import (
            UpdateTableRequestConfigType0,
        )

        config: dict[str, Any] | None | Unset
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, UpdateTableRequestConfigType0):
            config = self.config.to_dict()
        else:
            config = self.config

        enable_rls: bool | None | Unset
        if isinstance(self.enable_rls, Unset):
            enable_rls = UNSET
        else:
            enable_rls = self.enable_rls

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if config is not UNSET:
            field_dict["config"] = config
        if enable_rls is not UNSET:
            field_dict["enable_rls"] = enable_rls
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.update_table_request_config_type_0 import (
            UpdateTableRequestConfigType0,
        )

        d = dict(src_dict)

        def _parse_config(data: object) -> None | Unset | UpdateTableRequestConfigType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = UpdateTableRequestConfigType0.from_dict(data)

                return config_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UpdateTableRequestConfigType0, data)

        config = _parse_config(d.pop("config", UNSET))

        def _parse_enable_rls(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        enable_rls = _parse_enable_rls(d.pop("enable_rls", UNSET))

        def _parse_visibility(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        update_table_request = cls(
            config=config,
            enable_rls=enable_rls,
            visibility=visibility,
        )

        update_table_request.additional_properties = d
        return update_table_request

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
