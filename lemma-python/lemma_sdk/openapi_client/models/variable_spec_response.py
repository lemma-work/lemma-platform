from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="VariableSpecResponse")


@_attrs_define
class VariableSpecResponse:
    """
    Attributes:
        kind (str):
        name (str):
        connector (None | str | Unset): For a connector account variable, the connector the account must belong to (e.g.
            'slack'), so the importer can connect the right connector. Null for non-connector variables.
        default (None | str | Unset):
        description (None | str | Unset):
        provider (None | str | Unset): For a connector account variable, the auth provider backing the connector
            ('LEMMA' or 'COMPOSIO'), so the importer connects/selects an account through the right provider. Null for non-
            connector variables.
        required (bool | Unset):  Default: False.
    """

    kind: str
    name: str
    connector: None | str | Unset = UNSET
    default: None | str | Unset = UNSET
    description: None | str | Unset = UNSET
    provider: None | str | Unset = UNSET
    required: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind

        name = self.name

        connector: None | str | Unset
        if isinstance(self.connector, Unset):
            connector = UNSET
        else:
            connector = self.connector

        default: None | str | Unset
        if isinstance(self.default, Unset):
            default = UNSET
        else:
            default = self.default

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        provider: None | str | Unset
        if isinstance(self.provider, Unset):
            provider = UNSET
        else:
            provider = self.provider

        required = self.required

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
                "name": name,
            }
        )
        if connector is not UNSET:
            field_dict["connector"] = connector
        if default is not UNSET:
            field_dict["default"] = default
        if description is not UNSET:
            field_dict["description"] = description
        if provider is not UNSET:
            field_dict["provider"] = provider
        if required is not UNSET:
            field_dict["required"] = required

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = d.pop("kind")

        name = d.pop("name")

        def _parse_connector(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        connector = _parse_connector(d.pop("connector", UNSET))

        def _parse_default(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        default = _parse_default(d.pop("default", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_provider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider = _parse_provider(d.pop("provider", UNSET))

        required = d.pop("required", UNSET)

        variable_spec_response = cls(
            kind=kind,
            name=name,
            connector=connector,
            default=default,
            description=description,
            provider=provider,
            required=required,
        )

        variable_spec_response.additional_properties = d
        return variable_spec_response

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
