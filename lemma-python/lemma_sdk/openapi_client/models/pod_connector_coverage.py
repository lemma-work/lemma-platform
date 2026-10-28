from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.member_awaited import MemberAwaited


T = TypeVar("T", bound="PodConnectorCoverage")


@_attrs_define
class PodConnectorCoverage:
    """How many of the members who may use a connector can run it as their own.

    Attributes:
        connected (int): Of those, how many have a working account.
        eligible (int): Members of the pod who may use this connector.
        truncated (bool | Unset): The pod has more members than were counted. Default: False.
        waiting_on (list[MemberAwaited] | Unset): The rest: missing an account, or theirs needs reconnecting.
    """

    connected: int
    eligible: int
    truncated: bool | Unset = False
    waiting_on: list[MemberAwaited] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connected = self.connected

        eligible = self.eligible

        truncated = self.truncated

        waiting_on: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.waiting_on, Unset):
            waiting_on = []
            for waiting_on_item_data in self.waiting_on:
                waiting_on_item = waiting_on_item_data.to_dict()
                waiting_on.append(waiting_on_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connected": connected,
                "eligible": eligible,
            }
        )
        if truncated is not UNSET:
            field_dict["truncated"] = truncated
        if waiting_on is not UNSET:
            field_dict["waiting_on"] = waiting_on

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.member_awaited import MemberAwaited

        d = dict(src_dict)
        connected = d.pop("connected")

        eligible = d.pop("eligible")

        truncated = d.pop("truncated", UNSET)

        _waiting_on = d.pop("waiting_on", UNSET)
        waiting_on: list[MemberAwaited] | Unset = UNSET
        if _waiting_on is not UNSET:
            waiting_on = []
            for waiting_on_item_data in _waiting_on:
                waiting_on_item = MemberAwaited.from_dict(waiting_on_item_data)

                waiting_on.append(waiting_on_item)

        pod_connector_coverage = cls(
            connected=connected,
            eligible=eligible,
            truncated=truncated,
            waiting_on=waiting_on,
        )

        pod_connector_coverage.additional_properties = d
        return pod_connector_coverage

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
