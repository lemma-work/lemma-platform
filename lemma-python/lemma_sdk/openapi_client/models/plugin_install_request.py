from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.plugin_source_request import PluginSourceRequest


T = TypeVar("T", bound="PluginInstallRequest")


@_attrs_define
class PluginInstallRequest:
    """Body for installing several plugins into one Lemma.

    Attributes:
        plugins (list[PluginSourceRequest]): The plugins to install, in the order to install them. Each counts as one
            import against the daily import limit.
    """

    plugins: list[PluginSourceRequest]

    def to_dict(self) -> dict[str, Any]:
        plugins = []
        for plugins_item_data in self.plugins:
            plugins_item = plugins_item_data.to_dict()
            plugins.append(plugins_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "plugins": plugins,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.plugin_source_request import PluginSourceRequest

        d = dict(src_dict)
        plugins = []
        _plugins = d.pop("plugins")
        for plugins_item_data in _plugins:
            plugins_item = PluginSourceRequest.from_dict(plugins_item_data)

            plugins.append(plugins_item)

        plugin_install_request = cls(
            plugins=plugins,
        )

        return plugin_install_request
