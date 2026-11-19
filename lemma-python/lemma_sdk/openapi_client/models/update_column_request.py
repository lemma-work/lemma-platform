from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="UpdateColumnRequest")


@_attrs_define
class UpdateColumnRequest:
    """Schema for changing an existing column.

    An ENUM column's options can be added to, and a column's foreign key
    dropped; nothing else changes. Unknown fields are refused rather than
    ignored, so a caller asking for a change this does not make hears so
    instead of getting a 200 for nothing.

        Attributes:
            drop_foreign_key (bool | Unset): Drop the column's foreign key: its values stay, and stop having to name a row
                of the other table. A column with none is left as it is. A foreign key cannot be added to an existing column.
                Default: False.
            options (list[str] | None | Unset): Every option the ENUM column should accept: all of its current options, plus
                any to add. Removing an option is refused, because rows may already hold it. Sending the current options changes
                nothing.
    """

    drop_foreign_key: bool | Unset = False
    options: list[str] | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        drop_foreign_key = self.drop_foreign_key

        options: list[str] | None | Unset
        if isinstance(self.options, Unset):
            options = UNSET
        elif isinstance(self.options, list):
            options = self.options

        else:
            options = self.options

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if drop_foreign_key is not UNSET:
            field_dict["drop_foreign_key"] = drop_foreign_key
        if options is not UNSET:
            field_dict["options"] = options

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        drop_foreign_key = d.pop("drop_foreign_key", UNSET)

        def _parse_options(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                options_type_0 = cast(list[str], data)

                return options_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        options = _parse_options(d.pop("options", UNSET))

        update_column_request = cls(
            drop_foreign_key=drop_foreign_key,
            options=options,
        )

        return update_column_request
