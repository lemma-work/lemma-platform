from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.connector_choice import ConnectorChoice


T = TypeVar("T", bound="ApplyImportRequestConnectors")


@_attrs_define
class ApplyImportRequestConnectors:
    """Choices for the plan's `connectors`, by slug: which installation (default: the plan's `auth_config_id`), or to
    create one, and for a SHARED connector which of your own accounts to share. Account ids are never in a bundle; they
    are chosen here, in your own org.

    """

    additional_properties: dict[str, ConnectorChoice] = _attrs_field(
        init=False, factory=dict
    )

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            field_dict[prop_name] = prop.to_dict()

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_choice import ConnectorChoice

        d = dict(src_dict)
        apply_import_request_connectors = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():
            additional_property = ConnectorChoice.from_dict(prop_dict)

            additional_properties[prop_name] = additional_property

        apply_import_request_connectors.additional_properties = additional_properties
        return apply_import_request_connectors

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> ConnectorChoice:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: ConnectorChoice) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
