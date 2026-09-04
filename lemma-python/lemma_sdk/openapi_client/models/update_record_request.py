from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.update_record_request_data import UpdateRecordRequestData


T = TypeVar("T", bound="UpdateRecordRequest")


@_attrs_define
class UpdateRecordRequest:
    """Schema for updating a record.

    Attributes:
        data (UpdateRecordRequestData): Partial record patch keyed by table column names.
        expected_updated_at (datetime.datetime | None | Unset): Optional optimistic-concurrency guard: the `updated_at`
            value the caller last read. The patch applies only while the row still carries it, and answers 409 when it does
            not — so two clients editing the same field cannot silently keep the later one. Omit it for last-writer-wins.
    """

    data: UpdateRecordRequestData
    expected_updated_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = self.data.to_dict()

        expected_updated_at: None | str | Unset
        if isinstance(self.expected_updated_at, Unset):
            expected_updated_at = UNSET
        elif isinstance(self.expected_updated_at, datetime.datetime):
            expected_updated_at = self.expected_updated_at.isoformat()
        else:
            expected_updated_at = self.expected_updated_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "data": data,
            }
        )
        if expected_updated_at is not UNSET:
            field_dict["expected_updated_at"] = expected_updated_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.update_record_request_data import UpdateRecordRequestData

        d = dict(src_dict)
        data = UpdateRecordRequestData.from_dict(d.pop("data"))

        def _parse_expected_updated_at(
            data: object,
        ) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                expected_updated_at_type_0 = isoparse(data)

                return expected_updated_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        expected_updated_at = _parse_expected_updated_at(
            d.pop("expected_updated_at", UNSET)
        )

        update_record_request = cls(
            data=data,
            expected_updated_at=expected_updated_at,
        )

        update_record_request.additional_properties = d
        return update_record_request

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
