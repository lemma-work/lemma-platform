from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.create_pod_machine_request_template import CreatePodMachineRequestTemplate
from ..models.create_pod_machine_request_visibility import (
    CreatePodMachineRequestVisibility,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="CreatePodMachineRequest")


@_attrs_define
class CreatePodMachineRequest:
    """
    Attributes:
        name (str):
        display_name (None | str | Unset):
        run_slots (int | Unset):  Default: 1.
        size (None | str | Unset): Defaults to the plan's default size.
        template (CreatePodMachineRequestTemplate | Unset):  Default: CreatePodMachineRequestTemplate.WORKSPACE.
        visibility (CreatePodMachineRequestVisibility | Unset):  Default: CreatePodMachineRequestVisibility.POD.
    """

    name: str
    display_name: None | str | Unset = UNSET
    run_slots: int | Unset = 1
    size: None | str | Unset = UNSET
    template: CreatePodMachineRequestTemplate | Unset = (
        CreatePodMachineRequestTemplate.WORKSPACE
    )
    visibility: CreatePodMachineRequestVisibility | Unset = (
        CreatePodMachineRequestVisibility.POD
    )
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        display_name: None | str | Unset
        if isinstance(self.display_name, Unset):
            display_name = UNSET
        else:
            display_name = self.display_name

        run_slots = self.run_slots

        size: None | str | Unset
        if isinstance(self.size, Unset):
            size = UNSET
        else:
            size = self.size

        template: str | Unset = UNSET
        if not isinstance(self.template, Unset):
            template = self.template.value

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if run_slots is not UNSET:
            field_dict["run_slots"] = run_slots
        if size is not UNSET:
            field_dict["size"] = size
        if template is not UNSET:
            field_dict["template"] = template
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        def _parse_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        display_name = _parse_display_name(d.pop("display_name", UNSET))

        run_slots = d.pop("run_slots", UNSET)

        def _parse_size(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        size = _parse_size(d.pop("size", UNSET))

        _template = d.pop("template", UNSET)
        template: CreatePodMachineRequestTemplate | Unset
        if isinstance(_template, Unset):
            template = UNSET
        else:
            template = CreatePodMachineRequestTemplate(_template)

        _visibility = d.pop("visibility", UNSET)
        visibility: CreatePodMachineRequestVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = CreatePodMachineRequestVisibility(_visibility)

        create_pod_machine_request = cls(
            name=name,
            display_name=display_name,
            run_slots=run_slots,
            size=size,
            template=template,
            visibility=visibility,
        )

        create_pod_machine_request.additional_properties = d
        return create_pod_machine_request

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
