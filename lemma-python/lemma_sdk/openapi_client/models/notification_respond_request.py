from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.notification_respond_request_data_type_0 import (
        NotificationRespondRequestDataType0,
    )


T = TypeVar("T", bound="NotificationRespondRequest")


@_attrs_define
class NotificationRespondRequest:
    """
    Attributes:
        summary (str): The answer, in the person's words.
        data (None | NotificationRespondRequestDataType0 | Unset): Optional structured payload alongside the answer.
    """

    summary: str
    data: None | NotificationRespondRequestDataType0 | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.notification_respond_request_data_type_0 import (
            NotificationRespondRequestDataType0,
        )

        summary = self.summary

        data: dict[str, Any] | None | Unset
        if isinstance(self.data, Unset):
            data = UNSET
        elif isinstance(self.data, NotificationRespondRequestDataType0):
            data = self.data.to_dict()
        else:
            data = self.data

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "summary": summary,
            }
        )
        if data is not UNSET:
            field_dict["data"] = data

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.notification_respond_request_data_type_0 import (
            NotificationRespondRequestDataType0,
        )

        d = dict(src_dict)
        summary = d.pop("summary")

        def _parse_data(
            data: object,
        ) -> None | NotificationRespondRequestDataType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                data_type_0 = NotificationRespondRequestDataType0.from_dict(data)

                return data_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | NotificationRespondRequestDataType0 | Unset, data)

        data = _parse_data(d.pop("data", UNSET))

        notification_respond_request = cls(
            summary=summary,
            data=data,
        )

        return notification_respond_request
