from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.sign_in_request_status import SignInRequestStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="SignInRequestResponse")


@_attrs_define
class SignInRequestResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        origin (str):
        reason (str):
        status (SignInRequestStatus):
        saved (bool | Unset): Whether the login was kept for next time. Default: False.
        saved_detail (None | str | Unset): Why it was not kept, in words, when it was not.
    """

    created_at: datetime.datetime
    id: UUID
    origin: str
    reason: str
    status: SignInRequestStatus
    saved: bool | Unset = False
    saved_detail: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = str(self.id)

        origin = self.origin

        reason = self.reason

        status = self.status.value

        saved = self.saved

        saved_detail: None | str | Unset
        if isinstance(self.saved_detail, Unset):
            saved_detail = UNSET
        else:
            saved_detail = self.saved_detail

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "origin": origin,
                "reason": reason,
                "status": status,
            }
        )
        if saved is not UNSET:
            field_dict["saved"] = saved
        if saved_detail is not UNSET:
            field_dict["saved_detail"] = saved_detail

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        origin = d.pop("origin")

        reason = d.pop("reason")

        status = SignInRequestStatus(d.pop("status"))

        saved = d.pop("saved", UNSET)

        def _parse_saved_detail(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        saved_detail = _parse_saved_detail(d.pop("saved_detail", UNSET))

        sign_in_request_response = cls(
            created_at=created_at,
            id=id,
            origin=origin,
            reason=reason,
            status=status,
            saved=saved,
            saved_detail=saved_detail,
        )

        sign_in_request_response.additional_properties = d
        return sign_in_request_response

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
