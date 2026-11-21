from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.plugin_install_item_response import PluginInstallItemResponse


T = TypeVar("T", bound="PluginInstallResponse")


@_attrs_define
class PluginInstallResponse:
    """A plugin install: every plugin it named, in order.

    Attributes:
        finished (bool): Every plugin has stopped moving: INSTALLED, NEEDS_ANSWER, FAILED or CANCELLED. The Lemma tells
            the person who started it in their chat with it when this turns true.
        install_id (UUID):
        plugins (list[PluginInstallItemResponse]):
        pod_id (UUID):
    """

    finished: bool
    install_id: UUID
    plugins: list[PluginInstallItemResponse]
    pod_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        finished = self.finished

        install_id = str(self.install_id)

        plugins = []
        for plugins_item_data in self.plugins:
            plugins_item = plugins_item_data.to_dict()
            plugins.append(plugins_item)

        pod_id = str(self.pod_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "finished": finished,
                "install_id": install_id,
                "plugins": plugins,
                "pod_id": pod_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.plugin_install_item_response import PluginInstallItemResponse

        d = dict(src_dict)
        finished = d.pop("finished")

        install_id = UUID(d.pop("install_id"))

        plugins = []
        _plugins = d.pop("plugins")
        for plugins_item_data in _plugins:
            plugins_item = PluginInstallItemResponse.from_dict(plugins_item_data)

            plugins.append(plugins_item)

        pod_id = UUID(d.pop("pod_id"))

        plugin_install_response = cls(
            finished=finished,
            install_id=install_id,
            plugins=plugins,
            pod_id=pod_id,
        )

        plugin_install_response.additional_properties = d
        return plugin_install_response

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
