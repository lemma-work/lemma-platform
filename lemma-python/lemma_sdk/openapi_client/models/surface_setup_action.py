from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.surface_setup_action_field import SurfaceSetupActionField


T = TypeVar("T", bound="SurfaceSetupAction")


@_attrs_define
class SurfaceSetupAction:
    """A concrete thing the user must do to finish wiring up a surface.

    Only emitted when the user actually has to act (custom/bring-your-own-app
    credentials, or a pending OAuth grant). Each action carries where to go
    (``link``), ordered ``steps``, and the values to paste (``fields``).

        Attributes:
            description (str):
            key (str):
            title (str):
            fields (list[SurfaceSetupActionField] | Unset):
            informational (bool | Unset):  Default: False.
            link (None | str | Unset):
            link_label (None | str | Unset):
            steps (list[str] | Unset):
    """

    description: str
    key: str
    title: str
    fields: list[SurfaceSetupActionField] | Unset = UNSET
    informational: bool | Unset = False
    link: None | str | Unset = UNSET
    link_label: None | str | Unset = UNSET
    steps: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        key = self.key

        title = self.title

        fields: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.fields, Unset):
            fields = []
            for fields_item_data in self.fields:
                fields_item = fields_item_data.to_dict()
                fields.append(fields_item)

        informational = self.informational

        link: None | str | Unset
        if isinstance(self.link, Unset):
            link = UNSET
        else:
            link = self.link

        link_label: None | str | Unset
        if isinstance(self.link_label, Unset):
            link_label = UNSET
        else:
            link_label = self.link_label

        steps: list[str] | Unset = UNSET
        if not isinstance(self.steps, Unset):
            steps = self.steps

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "key": key,
                "title": title,
            }
        )
        if fields is not UNSET:
            field_dict["fields"] = fields
        if informational is not UNSET:
            field_dict["informational"] = informational
        if link is not UNSET:
            field_dict["link"] = link
        if link_label is not UNSET:
            field_dict["link_label"] = link_label
        if steps is not UNSET:
            field_dict["steps"] = steps

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.surface_setup_action_field import SurfaceSetupActionField

        d = dict(src_dict)
        description = d.pop("description")

        key = d.pop("key")

        title = d.pop("title")

        _fields = d.pop("fields", UNSET)
        fields: list[SurfaceSetupActionField] | Unset = UNSET
        if _fields is not UNSET:
            fields = []
            for fields_item_data in _fields:
                fields_item = SurfaceSetupActionField.from_dict(fields_item_data)

                fields.append(fields_item)

        informational = d.pop("informational", UNSET)

        def _parse_link(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        link = _parse_link(d.pop("link", UNSET))

        def _parse_link_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        link_label = _parse_link_label(d.pop("link_label", UNSET))

        steps = cast(list[str], d.pop("steps", UNSET))

        surface_setup_action = cls(
            description=description,
            key=key,
            title=title,
            fields=fields,
            informational=informational,
            link=link,
            link_label=link_label,
            steps=steps,
        )

        surface_setup_action.additional_properties = d
        return surface_setup_action

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
