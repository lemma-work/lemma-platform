from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.surface_platform import SurfacePlatform
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.surface_connector_setup_guide import SurfaceConnectorSetupGuide


T = TypeVar("T", bound="SurfacePlatformSetupGuide")


@_attrs_define
class SurfacePlatformSetupGuide:
    """
    Attributes:
        docs_path (str):
        platform (SurfacePlatform): The platforms a pod can be reached on.

            Email is Resend, and only Resend. Gmail and Outlook were here as
            Composio-backed mailboxes, which made "an email surface" mean three
            different transports with three attachment strategies between them -- bytes,
            Graph drafts, and a signed URL the provider downloads server-side. Reaching
            a Gmail *account* is still something an agent does, through the connector;
            it is just not a surface.
        summary (str):
        title (str):
        connectors (list[SurfaceConnectorSetupGuide] | Unset):
    """

    docs_path: str
    platform: SurfacePlatform
    summary: str
    title: str
    connectors: list[SurfaceConnectorSetupGuide] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        docs_path = self.docs_path

        platform = self.platform.value

        summary = self.summary

        title = self.title

        connectors: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.connectors, Unset):
            connectors = []
            for connectors_item_data in self.connectors:
                connectors_item = connectors_item_data.to_dict()
                connectors.append(connectors_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "docs_path": docs_path,
                "platform": platform,
                "summary": summary,
                "title": title,
            }
        )
        if connectors is not UNSET:
            field_dict["connectors"] = connectors

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.surface_connector_setup_guide import SurfaceConnectorSetupGuide

        d = dict(src_dict)
        docs_path = d.pop("docs_path")

        platform = SurfacePlatform(d.pop("platform"))

        summary = d.pop("summary")

        title = d.pop("title")

        _connectors = d.pop("connectors", UNSET)
        connectors: list[SurfaceConnectorSetupGuide] | Unset = UNSET
        if _connectors is not UNSET:
            connectors = []
            for connectors_item_data in _connectors:
                connectors_item = SurfaceConnectorSetupGuide.from_dict(
                    connectors_item_data
                )

                connectors.append(connectors_item)

        surface_platform_setup_guide = cls(
            docs_path=docs_path,
            platform=platform,
            summary=summary,
            title=title,
            connectors=connectors,
        )

        surface_platform_setup_guide.additional_properties = d
        return surface_platform_setup_guide

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
