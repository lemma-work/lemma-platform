from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.public_audience import PublicAudience

if TYPE_CHECKING:
    from ..models.read_column_response import ReadColumnResponse


T = TypeVar("T", bound="ReadsOpeningResponse")


@_attrs_define
class ReadsOpeningResponse:
    """
    Attributes:
        audience (None | PublicAudience): Who outside may read it; null when it is closed.
        columns (list[str]): The open columns, in order.
        contact_owned (bool): Each row is one contact's, so it can't be opened.
        offered (list[ReadColumnResponse]): The columns people outside could be shown.
        order_by (None | str): The open column rows are read in ascending order of.
        per_user (bool): Each member sees only their own rows, so it can't be opened.
        table (str):
        takes_rows (bool): It takes rows from outside, so it can't be opened for reads.
    """

    audience: None | PublicAudience
    columns: list[str]
    contact_owned: bool
    offered: list[ReadColumnResponse]
    order_by: None | str
    per_user: bool
    table: str
    takes_rows: bool
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

        order_by: None | str
        order_by = self.order_by

        per_user = self.per_user

        table = self.table

        takes_rows = self.takes_rows

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "audience": audience,
                "columns": columns,
                "contact_owned": contact_owned,
                "offered": offered,
                "order_by": order_by,
                "per_user": per_user,
                "table": table,
                "takes_rows": takes_rows,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.read_column_response import ReadColumnResponse

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
            offered_item = ReadColumnResponse.from_dict(offered_item_data)

            offered.append(offered_item)

        def _parse_order_by(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        order_by = _parse_order_by(d.pop("order_by"))

        per_user = d.pop("per_user")

        table = d.pop("table")

        takes_rows = d.pop("takes_rows")

        reads_opening_response = cls(
            audience=audience,
            columns=columns,
            contact_owned=contact_owned,
            offered=offered,
            order_by=order_by,
            per_user=per_user,
            table=table,
            takes_rows=takes_rows,
        )

        reads_opening_response.additional_properties = d
        return reads_opening_response

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
