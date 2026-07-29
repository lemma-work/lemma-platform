from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="AgentHostHarnessCapabilities")


@_attrs_define
class AgentHostHarnessCapabilities:
    """
    Attributes:
        close_session (bool | Unset):  Default: False.
        durable_session_recovery (bool | Unset):  Default: False.
        images (bool | Unset):  Default: False.
        load_session (bool | Unset):  Default: False.
        plans (bool | Unset):  Default: False.
        resume_session (bool | Unset):  Default: False.
        usage (bool | Unset):  Default: False.
    """

    close_session: bool | Unset = False
    durable_session_recovery: bool | Unset = False
    images: bool | Unset = False
    load_session: bool | Unset = False
    plans: bool | Unset = False
    resume_session: bool | Unset = False
    usage: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        close_session = self.close_session

        durable_session_recovery = self.durable_session_recovery

        images = self.images

        load_session = self.load_session

        plans = self.plans

        resume_session = self.resume_session

        usage = self.usage

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if close_session is not UNSET:
            field_dict["close_session"] = close_session
        if durable_session_recovery is not UNSET:
            field_dict["durable_session_recovery"] = durable_session_recovery
        if images is not UNSET:
            field_dict["images"] = images
        if load_session is not UNSET:
            field_dict["load_session"] = load_session
        if plans is not UNSET:
            field_dict["plans"] = plans
        if resume_session is not UNSET:
            field_dict["resume_session"] = resume_session
        if usage is not UNSET:
            field_dict["usage"] = usage

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        close_session = d.pop("close_session", UNSET)

        durable_session_recovery = d.pop("durable_session_recovery", UNSET)

        images = d.pop("images", UNSET)

        load_session = d.pop("load_session", UNSET)

        plans = d.pop("plans", UNSET)

        resume_session = d.pop("resume_session", UNSET)

        usage = d.pop("usage", UNSET)

        agent_host_harness_capabilities = cls(
            close_session=close_session,
            durable_session_recovery=durable_session_recovery,
            images=images,
            load_session=load_session,
            plans=plans,
            resume_session=resume_session,
            usage=usage,
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
