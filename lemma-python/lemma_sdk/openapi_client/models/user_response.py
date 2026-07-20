from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="UserResponse")


@_attrs_define
class UserResponse:
    """User response schema.

    Attributes:
        created_at (datetime.datetime):
        email (str):
        id (UUID):
        is_active (bool):
        is_superuser (bool):
        is_verified (bool):
        updated_at (datetime.datetime):
        country (None | str | Unset):
        date_of_birth (datetime.date | None | Unset):
        deactivated_at (datetime.datetime | None | Unset):
        deactivation_reason (None | str | Unset):
        email_verified_at (datetime.datetime | None | Unset):
        first_name (None | str | Unset):
        last_name (None | str | Unset):
        mobile_number (None | str | Unset):
        mobile_verified_at (datetime.datetime | None | Unset):
        telegram_username (None | str | Unset):
        timezone (None | str | Unset):
    """

    created_at: datetime.datetime
    email: str
    id: UUID
    is_active: bool
    is_superuser: bool
    is_verified: bool
    updated_at: datetime.datetime
    country: None | str | Unset = UNSET
    date_of_birth: datetime.date | None | Unset = UNSET
    deactivated_at: datetime.datetime | None | Unset = UNSET
    deactivation_reason: None | str | Unset = UNSET
    email_verified_at: datetime.datetime | None | Unset = UNSET
    first_name: None | str | Unset = UNSET
    last_name: None | str | Unset = UNSET
    mobile_number: None | str | Unset = UNSET
    mobile_verified_at: datetime.datetime | None | Unset = UNSET
    telegram_username: None | str | Unset = UNSET
    timezone: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        email = self.email

        id = str(self.id)

        is_active = self.is_active

        is_superuser = self.is_superuser

        is_verified = self.is_verified

        updated_at = self.updated_at.isoformat()

        country: None | str | Unset
        if isinstance(self.country, Unset):
            country = UNSET
        else:
            country = self.country

        date_of_birth: None | str | Unset
        if isinstance(self.date_of_birth, Unset):
            date_of_birth = UNSET
        elif isinstance(self.date_of_birth, datetime.date):
            date_of_birth = self.date_of_birth.isoformat()
        else:
            date_of_birth = self.date_of_birth

        deactivated_at: None | str | Unset
        if isinstance(self.deactivated_at, Unset):
            deactivated_at = UNSET
        elif isinstance(self.deactivated_at, datetime.datetime):
            deactivated_at = self.deactivated_at.isoformat()
        else:
            deactivated_at = self.deactivated_at

        deactivation_reason: None | str | Unset
        if isinstance(self.deactivation_reason, Unset):
            deactivation_reason = UNSET
        else:
            deactivation_reason = self.deactivation_reason

        email_verified_at: None | str | Unset
        if isinstance(self.email_verified_at, Unset):
            email_verified_at = UNSET
        elif isinstance(self.email_verified_at, datetime.datetime):
            email_verified_at = self.email_verified_at.isoformat()
        else:
            email_verified_at = self.email_verified_at

        first_name: None | str | Unset
        if isinstance(self.first_name, Unset):
            first_name = UNSET
        else:
            first_name = self.first_name

        last_name: None | str | Unset
        if isinstance(self.last_name, Unset):
            last_name = UNSET
        else:
            last_name = self.last_name

        mobile_number: None | str | Unset
        if isinstance(self.mobile_number, Unset):
            mobile_number = UNSET
        else:
            mobile_number = self.mobile_number

        mobile_verified_at: None | str | Unset
        if isinstance(self.mobile_verified_at, Unset):
            mobile_verified_at = UNSET
        elif isinstance(self.mobile_verified_at, datetime.datetime):
            mobile_verified_at = self.mobile_verified_at.isoformat()
        else:
            mobile_verified_at = self.mobile_verified_at

        telegram_username: None | str | Unset
        if isinstance(self.telegram_username, Unset):
            telegram_username = UNSET
        else:
            telegram_username = self.telegram_username

        timezone: None | str | Unset
        if isinstance(self.timezone, Unset):
            timezone = UNSET
        else:
            timezone = self.timezone

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "email": email,
                "id": id,
                "is_active": is_active,
                "is_superuser": is_superuser,
                "is_verified": is_verified,
                "updated_at": updated_at,
            }
        )
        if country is not UNSET:
            field_dict["country"] = country
        if date_of_birth is not UNSET:
            field_dict["date_of_birth"] = date_of_birth
        if deactivated_at is not UNSET:
            field_dict["deactivated_at"] = deactivated_at
        if deactivation_reason is not UNSET:
            field_dict["deactivation_reason"] = deactivation_reason
        if email_verified_at is not UNSET:
            field_dict["email_verified_at"] = email_verified_at
        if first_name is not UNSET:
            field_dict["first_name"] = first_name
        if last_name is not UNSET:
            field_dict["last_name"] = last_name
        if mobile_number is not UNSET:
            field_dict["mobile_number"] = mobile_number
        if mobile_verified_at is not UNSET:
            field_dict["mobile_verified_at"] = mobile_verified_at
        if telegram_username is not UNSET:
            field_dict["telegram_username"] = telegram_username
        if timezone is not UNSET:
            field_dict["timezone"] = timezone

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        email = d.pop("email")

        id = UUID(d.pop("id"))

        is_active = d.pop("is_active")

        is_superuser = d.pop("is_superuser")

        is_verified = d.pop("is_verified")

        updated_at = isoparse(d.pop("updated_at"))

        def _parse_country(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        country = _parse_country(d.pop("country", UNSET))

        def _parse_date_of_birth(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                date_of_birth_type_0 = isoparse(data).date()

                return date_of_birth_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        date_of_birth = _parse_date_of_birth(d.pop("date_of_birth", UNSET))

        def _parse_deactivated_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                deactivated_at_type_0 = isoparse(data)

                return deactivated_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        deactivated_at = _parse_deactivated_at(d.pop("deactivated_at", UNSET))

        def _parse_deactivation_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        deactivation_reason = _parse_deactivation_reason(
            d.pop("deactivation_reason", UNSET)
        )

        def _parse_email_verified_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                email_verified_at_type_0 = isoparse(data)

                return email_verified_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        email_verified_at = _parse_email_verified_at(d.pop("email_verified_at", UNSET))

        def _parse_first_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        first_name = _parse_first_name(d.pop("first_name", UNSET))

        def _parse_last_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_name = _parse_last_name(d.pop("last_name", UNSET))

        def _parse_mobile_number(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        mobile_number = _parse_mobile_number(d.pop("mobile_number", UNSET))

        def _parse_mobile_verified_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                mobile_verified_at_type_0 = isoparse(data)

                return mobile_verified_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        mobile_verified_at = _parse_mobile_verified_at(
            d.pop("mobile_verified_at", UNSET)
        )

        def _parse_telegram_username(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        telegram_username = _parse_telegram_username(d.pop("telegram_username", UNSET))

        def _parse_timezone(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        timezone = _parse_timezone(d.pop("timezone", UNSET))

        user_response = cls(
            created_at=created_at,
            email=email,
            id=id,
            is_active=is_active,
            is_superuser=is_superuser,
            is_verified=is_verified,
            updated_at=updated_at,
            country=country,
            date_of_birth=date_of_birth,
            deactivated_at=deactivated_at,
            deactivation_reason=deactivation_reason,
            email_verified_at=email_verified_at,
            first_name=first_name,
            last_name=last_name,
            mobile_number=mobile_number,
            mobile_verified_at=mobile_verified_at,
            telegram_username=telegram_username,
            timezone=timezone,
        )

        user_response.additional_properties = d
        return user_response

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
