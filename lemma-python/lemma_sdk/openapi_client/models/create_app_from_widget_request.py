from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CreateAppFromWidgetRequest")


@_attrs_define
class CreateAppFromWidgetRequest:
    """Promote a conversation widget into a persisted app.

    The widget's stored source fragment (addressed by conversation + tool call) is
    preserved, wrapped into a standalone document without embed-only chrome, and
    deployed as the app's bundle.

        Attributes:
            conversation_id (UUID):
            name (str):
            tool_call_id (str):
            description (None | str | Unset):
            public_slug (None | str | Unset):
            visibility (None | str | Unset):
    """

    conversation_id: UUID
    name: str
    tool_call_id: str
    description: None | str | Unset = UNSET
    public_slug: None | str | Unset = UNSET
    visibility: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        conversation_id = str(self.conversation_id)

        name = self.name

        tool_call_id = self.tool_call_id

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        public_slug: None | str | Unset
        if isinstance(self.public_slug, Unset):
            public_slug = UNSET
        else:
            public_slug = self.public_slug

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        else:
            visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "conversation_id": conversation_id,
                "name": name,
                "tool_call_id": tool_call_id,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if public_slug is not UNSET:
            field_dict["public_slug"] = public_slug
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        conversation_id = UUID(d.pop("conversation_id"))

        name = d.pop("name")

        tool_call_id = d.pop("tool_call_id")

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_public_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        public_slug = _parse_public_slug(d.pop("public_slug", UNSET))

        def _parse_visibility(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        create_app_from_widget_request = cls(
            conversation_id=conversation_id,
            name=name,
            tool_call_id=tool_call_id,
            description=description,
            public_slug=public_slug,
            visibility=visibility,
        )

        create_app_from_widget_request.additional_properties = d
        return create_app_from_widget_request

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
