from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.public_audience import PublicAudience

if TYPE_CHECKING:
    from ..models.public_column_response import PublicColumnResponse


T = TypeVar("T", bound="TableOpeningResponse")


@_attrs_define
class TableOpeningResponse:
    """
    Attributes:
        audience (None | PublicAudience): Who outside may add rows; null when the table is closed.
        columns (list[str]): The open columns, in order.
        contact_owned (bool): A confirmed contact's row names them in contact_id.
        offered (list[PublicColumnResponse]): The columns people outside could be asked to fill.
        per_user (bool): Each member sees only their own rows, so it can't be opened.
        table (str):
    """

    audience: None | PublicAudience
    columns: list[str]
    contact_owned: bool
    offered: list[PublicColumnResponse]
    per_user: bool
    table: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        audience: None | str
        if isinstance(self.audience, PublicAudience):
            audience = self.audience.value
        else:
            audience = self.audience

        columns = self.columns

        contact_owned = self.contact_owned

        offered = []
        for offered_item_data in self.offered:
            offered_item = offered_item_data.to_dict()
            offered.append(offered_item)

        per_user = self.per_user

        table = self.table

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "audience": audience,
                "columns": columns,
                "contact_owned": contact_owned,
                "offered": offered,
                "per_user": per_user,
                "table": table,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.public_column_response import PublicColumnResponse

        d = dict(src_dict)

        def _parse_audience(data: object) -> None | PublicAudience:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                audience_type_0 = PublicAudience(data)

                return audience_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PublicAudience, data)

        audience = _parse_audience(d.pop("audience"))

        columns = cast(list[str], d.pop("columns"))

        contact_owned = d.pop("contact_owned")

        offered = []
        _offered = d.pop("offered")
        for offered_item_data in _offered:
            offered_item = PublicColumnResponse.from_dict(offered_item_data)

            offered.append(offered_item)

        per_user = d.pop("per_user")

        table = d.pop("table")

        table_opening_response = cls(
            audience=audience,
            columns=columns,
            contact_owned=contact_owned,
            offered=offered,
            per_user=per_user,
            table=table,
        )

        table_opening_response.additional_properties = d
        return table_opening_response

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
