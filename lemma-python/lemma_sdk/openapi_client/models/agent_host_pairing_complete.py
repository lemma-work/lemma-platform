from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.host_hello import HostHello


T = TypeVar("T", bound="AgentHostPairingComplete")


@_attrs_define
class AgentHostPairingComplete:
    """
    Attributes:
        display_name (str):
        hello (HostHello):
        pairing_code (str):
    """

    display_name: str
    hello: HostHello
    pairing_code: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        display_name = self.display_name

        hello = self.hello.to_dict()

        pairing_code = self.pairing_code

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "display_name": display_name,
                "hello": hello,
                "pairing_code": pairing_code,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.host_hello import HostHello

        d = dict(src_dict)
        display_name = d.pop("display_name")

        hello = HostHello.from_dict(d.pop("hello"))

        pairing_code = d.pop("pairing_code")

        agent_host_pairing_complete = cls(
            display_name=display_name,
            hello=hello,
            pairing_code=pairing_code,
        )

        agent_host_pairing_complete.additional_properties = d
        return agent_host_pairing_complete

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
