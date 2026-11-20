from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="PluginInstallStepResponse")


@_attrs_define
class PluginInstallStepResponse:
    """The step a bundle is on.

    Attributes:
        index (int):
        kind (str):
        name (str):
    """

    index: int
    kind: str
    name: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        index = self.index

        kind = self.kind

        name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "index": index,
                "kind": kind,
                "name": name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        index = d.pop("index")

        kind = d.pop("kind")

        name = d.pop("name")

        plugin_install_step_response = cls(
            index=index,
            kind=kind,
            name=name,
        )

        plugin_install_step_response.additional_properties = d
        return plugin_install_step_response

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
