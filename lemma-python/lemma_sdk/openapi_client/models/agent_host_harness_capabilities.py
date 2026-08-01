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

    ``images`` adds the vision capability to the runtime picker;
    ``load_session`` is what lets a conversation keep one provider session
    across turns, so it decides whether a run is dispatched with a
    ``resume_session_id``. Anything else a host reports is kept verbatim by
    ``extra: allow`` rather than typed here, so the wire format stays open
    without inventing fields no code reads.

        Attributes:
            images (bool | Unset):  Default: False.
            load_session (bool | Unset):  Default: False.
    """

    images: bool | Unset = False
    load_session: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        images = self.images

        load_session = self.load_session

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if images is not UNSET:
            field_dict["images"] = images
        if load_session is not UNSET:
            field_dict["load_session"] = load_session

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        images = d.pop("images", UNSET)

        load_session = d.pop("load_session", UNSET)

        agent_host_harness_capabilities = cls(
            images=images,
            load_session=load_session,
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
