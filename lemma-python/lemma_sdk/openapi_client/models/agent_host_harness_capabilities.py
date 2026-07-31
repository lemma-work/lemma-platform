from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="AgentHostHarnessCapabilities")


@_attrs_define
class AgentHostHarnessCapabilities:
    """Harness capabilities the server actually branches on.

    Only ``images`` changes server behaviour today (it adds the vision
    capability to the runtime picker). Anything else a host reports is kept
    verbatim by ``extra: allow`` rather than typed here, so the wire format
    stays open without inventing fields no code reads.

        Attributes:
            images (bool | Unset):  Default: False.
    """

    images: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        images = self.images

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if images is not UNSET:
            field_dict["images"] = images

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        images = d.pop("images", UNSET)

        agent_host_harness_capabilities = cls(
            images=images,
        )

        agent_host_harness_capabilities.additional_properties = d
        return agent_host_harness_capabilities

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
