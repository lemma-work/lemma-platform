from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="WebLoginResponse")


@_attrs_define
class WebLoginResponse:
    """
    Attributes:
        cookie_count (int): How many cookies this site has. A rough sense of scale.
        site (str): The site, as a person would name it. Cookies are grouped by registrable domain, so `lemma.work` and
            `api.lemma.work` are one login rather than two -- the second being the half nobody visited on purpose.
        expires (datetime.datetime | None | Unset): When the soonest of them lapses, which is the closest thing to 'when
            will I have to sign in again'. Null when they are all session cookies, which go when the browser does.
        signed_in (bool | Unset): True when somebody answered 'yes, I signed in' to a sign-in request for this site. The
            cookies cannot say this on their own: a real profile held two session cookies for a site that was signed in and
            six for one that merely had a video played on it, identical on every flag. False means only 'nobody said so' --
            the browser may still have a usable session. Default: False.
    """

    cookie_count: int
    site: str
    expires: datetime.datetime | None | Unset = UNSET
    signed_in: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cookie_count = self.cookie_count

        site = self.site

        expires: None | str | Unset
        if isinstance(self.expires, Unset):
            expires = UNSET
        elif isinstance(self.expires, datetime.datetime):
            expires = self.expires.isoformat()
        else:
            expires = self.expires

        signed_in = self.signed_in

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "cookie_count": cookie_count,
                "site": site,
            }
        )
        if expires is not UNSET:
            field_dict["expires"] = expires
        if signed_in is not UNSET:
            field_dict["signed_in"] = signed_in

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        cookie_count = d.pop("cookie_count")

        site = d.pop("site")

        def _parse_expires(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                expires_type_0 = isoparse(data)

                return expires_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        expires = _parse_expires(d.pop("expires", UNSET))

        signed_in = d.pop("signed_in", UNSET)

        web_login_response = cls(
            cookie_count=cookie_count,
            site=site,
            expires=expires,
            signed_in=signed_in,
        )

        web_login_response.additional_properties = d
        return web_login_response

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
