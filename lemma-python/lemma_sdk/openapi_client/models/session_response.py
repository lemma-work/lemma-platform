from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SessionResponse")


@_attrs_define
class SessionResponse:
    """
    Attributes:
        access_token (str): Send as `Authorization: Bearer` on every other call. Never store it.
        display_name (None | str):
        expires_in (int): Seconds until the access token expires.
        is_contact (bool):
        secret (None | str): Only when new: keep it to come back. Null means keep the one you have.
        title (None | str | Unset):
    """

    access_token: str
    display_name: None | str
    expires_in: int
    is_contact: bool
    secret: None | str
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        access_token = self.access_token

        display_name: None | str
        display_name = self.display_name

        expires_in = self.expires_in

        is_contact = self.is_contact

        secret: None | str
        secret = self.secret

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "access_token": access_token,
                "display_name": display_name,
                "expires_in": expires_in,
                "is_contact": is_contact,
                "secret": secret,
            }
        )
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        access_token = d.pop("access_token")

        def _parse_display_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        display_name = _parse_display_name(d.pop("display_name"))

        expires_in = d.pop("expires_in")

        is_contact = d.pop("is_contact")

        def _parse_secret(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        secret = _parse_secret(d.pop("secret"))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        session_response = cls(
            access_token=access_token,
            display_name=display_name,
            expires_in=expires_in,
            is_contact=is_contact,
            secret=secret,
            title=title,
        )

        session_response.additional_properties = d
        return session_response

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
