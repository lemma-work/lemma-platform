from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.triage_route import TriageRoute

T = TypeVar("T", bound="TriageConfigRoutes")


@_attrs_define
class TriageConfigRoutes:
    """Every declared option of the question, mapped to act, digest, ask or ignore."""

    additional_properties: dict[str, TriageRoute] = _attrs_field(
        init=False, factory=dict
    )

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            field_dict[prop_name] = prop.value

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        triage_config_routes = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():
            additional_property = TriageRoute(prop_dict)

            additional_properties[prop_name] = additional_property

        triage_config_routes.additional_properties = additional_properties
        return triage_config_routes

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> TriageRoute:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: TriageRoute) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
