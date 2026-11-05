from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.file_part import FilePart
    from ..models.link_part import LinkPart
    from ..models.mention_part import MentionPart


T = TypeVar("T", bound="ChannelEditRequest")


@_attrs_define
class ChannelEditRequest:
    """Absent is unchanged; ``null`` clears the parts. The text cannot be
    cleared: delete the line instead.

        Attributes:
            parts (list[FilePart | LinkPart | MentionPart] | None | Unset): Absent keeps the parts; `null` or `[]` clears
                them.
            text (None | str | Unset):
    """

    parts: list[FilePart | LinkPart | MentionPart] | None | Unset = UNSET
    text: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.file_part import FilePart
        from ..models.mention_part import MentionPart

        parts: list[dict[str, Any]] | None | Unset
        if isinstance(self.parts, Unset):
            parts = UNSET
        elif isinstance(self.parts, list):
            parts = []
            for parts_type_0_item_data in self.parts:
                parts_type_0_item: dict[str, Any]
                if isinstance(parts_type_0_item_data, MentionPart):
                    parts_type_0_item = parts_type_0_item_data.to_dict()
                elif isinstance(parts_type_0_item_data, FilePart):
                    parts_type_0_item = parts_type_0_item_data.to_dict()
                else:
                    parts_type_0_item = parts_type_0_item_data.to_dict()

                parts.append(parts_type_0_item)

        else:
            parts = self.parts

        text: None | str | Unset
        if isinstance(self.text, Unset):
            text = UNSET
        else:
            text = self.text

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if parts is not UNSET:
            field_dict["parts"] = parts
        if text is not UNSET:
            field_dict["text"] = text

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.file_part import FilePart
        from ..models.link_part import LinkPart
        from ..models.mention_part import MentionPart

        d = dict(src_dict)

        def _parse_parts(
            data: object,
        ) -> list[FilePart | LinkPart | MentionPart] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                parts_type_0 = []
                _parts_type_0 = data
                for parts_type_0_item_data in _parts_type_0:

                    def _parse_parts_type_0_item(
                        data: object,
                    ) -> FilePart | LinkPart | MentionPart:
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            parts_type_0_item_type_0 = MentionPart.from_dict(data)

                            return parts_type_0_item_type_0
                        except TypeError, ValueError, AttributeError, KeyError:
                            pass
                        try:
                            if not isinstance(data, dict):
                                raise TypeError()
                            parts_type_0_item_type_1 = FilePart.from_dict(data)

                            return parts_type_0_item_type_1
                        except TypeError, ValueError, AttributeError, KeyError:
                            pass
                        if not isinstance(data, dict):
                            raise TypeError()
                        parts_type_0_item_type_2 = LinkPart.from_dict(data)

                        return parts_type_0_item_type_2

                    parts_type_0_item = _parse_parts_type_0_item(parts_type_0_item_data)

                    parts_type_0.append(parts_type_0_item)

                return parts_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[FilePart | LinkPart | MentionPart] | None | Unset, data)

        parts = _parse_parts(d.pop("parts", UNSET))

        def _parse_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        text = _parse_text(d.pop("text", UNSET))

        channel_edit_request = cls(
            parts=parts,
            text=text,
        )

        channel_edit_request.additional_properties = d
        return channel_edit_request

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
