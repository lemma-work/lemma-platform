from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.size_offer import SizeOffer


T = TypeVar("T", bound="MachinePolicyResponse")


@_attrs_define
class MachinePolicyResponse:
    """What a person or pod may create, for a screen to offer and no more.

    Attributes:
        default_size (str):
        templates (list[SizeOffer]):
        max_connected_machines (int | None | Unset):
        max_pod_machines (int | None | Unset):
        max_running (int | None | Unset):
    """

    default_size: str
    templates: list[SizeOffer]
    max_connected_machines: int | None | Unset = UNSET
    max_pod_machines: int | None | Unset = UNSET
    max_running: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        default_size = self.default_size

        templates = []
        for templates_item_data in self.templates:
            templates_item = templates_item_data.to_dict()
            templates.append(templates_item)

        max_connected_machines: int | None | Unset
        if isinstance(self.max_connected_machines, Unset):
            max_connected_machines = UNSET
        else:
            max_connected_machines = self.max_connected_machines

        max_pod_machines: int | None | Unset
        if isinstance(self.max_pod_machines, Unset):
            max_pod_machines = UNSET
        else:
            max_pod_machines = self.max_pod_machines

        max_running: int | None | Unset
        if isinstance(self.max_running, Unset):
            max_running = UNSET
        else:
            max_running = self.max_running

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "default_size": default_size,
                "templates": templates,
            }
        )
        if max_connected_machines is not UNSET:
            field_dict["max_connected_machines"] = max_connected_machines
        if max_pod_machines is not UNSET:
            field_dict["max_pod_machines"] = max_pod_machines
        if max_running is not UNSET:
            field_dict["max_running"] = max_running

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.size_offer import SizeOffer

        d = dict(src_dict)
        default_size = d.pop("default_size")

        templates = []
        _templates = d.pop("templates")
        for templates_item_data in _templates:
            templates_item = SizeOffer.from_dict(templates_item_data)

            templates.append(templates_item)

        def _parse_max_connected_machines(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        max_connected_machines = _parse_max_connected_machines(
            d.pop("max_connected_machines", UNSET)
        )

        def _parse_max_pod_machines(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        max_pod_machines = _parse_max_pod_machines(d.pop("max_pod_machines", UNSET))

        def _parse_max_running(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        max_running = _parse_max_running(d.pop("max_running", UNSET))

        machine_policy_response = cls(
            default_size=default_size,
            templates=templates,
            max_connected_machines=max_connected_machines,
            max_pod_machines=max_pod_machines,
            max_running=max_running,
        )

        machine_policy_response.additional_properties = d
        return machine_policy_response

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
