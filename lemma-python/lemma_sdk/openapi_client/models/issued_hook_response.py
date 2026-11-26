from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

if TYPE_CHECKING:
    from ..models.issued_hook_response_fields_type_0 import (
        IssuedHookResponseFieldsType0,
    )


T = TypeVar("T", bound="IssuedHookResponse")


@_attrs_define
class IssuedHookResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        created_by (UUID): The member each row is added as.
        enabled (bool):
        fields (IssuedHookResponseFieldsType0 | None): Column name to a dotted path into the posted JSON (`email`,
            `form_response.hidden.ref`, `answers.0.text`; `$` is the whole body). Leave it out to write the whole body to
            the table's `payload` JSON column. Either way `received_at` and `hook` are filled when the table has them.
        id (UUID):
        last_error (None | str): Why the last refused delivery was refused, as its sender was told, or that a duplicate
            added no row. Cleared when a row next arrives.
        last_error_at (datetime.datetime | None):
        last_received_at (datetime.datetime | None): When a delivery last added a row.
        name (str):
        secret (str): Shown now and never again.
        table (str):
        url (str): The address to give the service. It carries the secret.
    """

    created_at: datetime.datetime
    created_by: UUID
    enabled: bool
    fields: IssuedHookResponseFieldsType0 | None
    id: UUID
    last_error: None | str
    last_error_at: datetime.datetime | None
    last_received_at: datetime.datetime | None
    name: str
    secret: str
    table: str
    url: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.issued_hook_response_fields_type_0 import (
            IssuedHookResponseFieldsType0,
        )

        created_at = self.created_at.isoformat()

        created_by = str(self.created_by)

        enabled = self.enabled

        fields: dict[str, Any] | None
        if isinstance(self.fields, IssuedHookResponseFieldsType0):
            fields = self.fields.to_dict()
        else:
            fields = self.fields

        id = str(self.id)

        last_error: None | str
        last_error = self.last_error

        last_error_at: None | str
        if isinstance(self.last_error_at, datetime.datetime):
            last_error_at = self.last_error_at.isoformat()
        else:
            last_error_at = self.last_error_at

        last_received_at: None | str
        if isinstance(self.last_received_at, datetime.datetime):
            last_received_at = self.last_received_at.isoformat()
        else:
            last_received_at = self.last_received_at

        name = self.name

        secret = self.secret

        table = self.table

        url = self.url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "created_by": created_by,
                "enabled": enabled,
                "fields": fields,
                "id": id,
                "last_error": last_error,
                "last_error_at": last_error_at,
                "last_received_at": last_received_at,
                "name": name,
                "secret": secret,
                "table": table,
                "url": url,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.issued_hook_response_fields_type_0 import (
            IssuedHookResponseFieldsType0,
        )

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        created_by = UUID(d.pop("created_by"))

        enabled = d.pop("enabled")

        def _parse_fields(data: object) -> IssuedHookResponseFieldsType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                fields_type_0 = IssuedHookResponseFieldsType0.from_dict(data)

                return fields_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(IssuedHookResponseFieldsType0 | None, data)

        fields = _parse_fields(d.pop("fields"))

        id = UUID(d.pop("id"))

        def _parse_last_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        last_error = _parse_last_error(d.pop("last_error"))

        def _parse_last_error_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_error_at_type_0 = isoparse(data)

                return last_error_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        last_error_at = _parse_last_error_at(d.pop("last_error_at"))

        def _parse_last_received_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_received_at_type_0 = isoparse(data)

                return last_received_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        last_received_at = _parse_last_received_at(d.pop("last_received_at"))

        name = d.pop("name")

        secret = d.pop("secret")

        table = d.pop("table")

        url = d.pop("url")

        issued_hook_response = cls(
            created_at=created_at,
            created_by=created_by,
            enabled=enabled,
            fields=fields,
            id=id,
            last_error=last_error,
            last_error_at=last_error_at,
            last_received_at=last_received_at,
            name=name,
            secret=secret,
            table=table,
            url=url,
        )

        issued_hook_response.additional_properties = d
        return issued_hook_response

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
