from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="InputView")


@_attrs_define
class InputView:
    """The only part of the state any engine sees.

    `fields` are top-level keys or dotted paths into an object state. Leaving
    them out passes the whole state. Either way the rendered view is cut at
    `max_chars`, and the engines are told it was.

        Attributes:
            fields (list[str] | None | Unset):
            max_chars (int | Unset):  Default: 8000.
    """

    fields: list[str] | None | Unset = UNSET
    max_chars: int | Unset = 8000

    def to_dict(self) -> dict[str, Any]:
        fields: list[str] | None | Unset
        if isinstance(self.fields, Unset):
            fields = UNSET
        elif isinstance(self.fields, list):
            fields = self.fields

        else:
            fields = self.fields

        max_chars = self.max_chars

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if fields is not UNSET:
            field_dict["fields"] = fields
        if max_chars is not UNSET:
            field_dict["max_chars"] = max_chars

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_fields(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                fields_type_0 = cast(list[str], data)

                return fields_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        fields = _parse_fields(d.pop("fields", UNSET))

        max_chars = d.pop("max_chars", UNSET)

        input_view = cls(
            fields=fields,
            max_chars=max_chars,
        )

        return input_view
