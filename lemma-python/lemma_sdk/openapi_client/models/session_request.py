from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SessionRequest")


@_attrs_define
class SessionRequest:
    """
    Attributes:
        altcha (None | str | Unset): The solved challenge, when starting an anonymous session.
        host_token (None | str | Unset): A token the page's own server signed with the widget's secret.
        secret (None | str | Unset): The secret a previous answer returned, to come back to that session.
    """

    altcha: None | str | Unset = UNSET
    host_token: None | str | Unset = UNSET
    secret: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        altcha: None | str | Unset
        if isinstance(self.altcha, Unset):
            altcha = UNSET
        else:
            altcha = self.altcha

        host_token: None | str | Unset
        if isinstance(self.host_token, Unset):
            host_token = UNSET
        else:
            host_token = self.host_token

        secret: None | str | Unset
        if isinstance(self.secret, Unset):
            secret = UNSET
        else:
            secret = self.secret

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if altcha is not UNSET:
            field_dict["altcha"] = altcha
        if host_token is not UNSET:
            field_dict["host_token"] = host_token
        if secret is not UNSET:
            field_dict["secret"] = secret

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_altcha(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        altcha = _parse_altcha(d.pop("altcha", UNSET))

        def _parse_host_token(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        host_token = _parse_host_token(d.pop("host_token", UNSET))

        def _parse_secret(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        secret = _parse_secret(d.pop("secret", UNSET))

        session_request = cls(
            altcha=altcha,
            host_token=host_token,
            secret=secret,
        )

        session_request.additional_properties = d
        return session_request

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
