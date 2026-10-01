from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="TriageDigest")


@_attrs_define
class TriageDigest:
    """When held events go out together.

    Attributes:
        cron (str): Five-field cron for the digest, e.g. '0 9 * * 1-5'. No more often than a TIME schedule may fire.
        timezone (None | str | Unset): IANA zone the cron is read in, e.g. 'Europe/Berlin'. Omitted means UTC.
    """

    cron: str
    timezone: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        cron = self.cron

        timezone: None | str | Unset
        if isinstance(self.timezone, Unset):
            timezone = UNSET
        else:
            timezone = self.timezone

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "cron": cron,
            }
        )
        if timezone is not UNSET:
            field_dict["timezone"] = timezone

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        cron = d.pop("cron")

        def _parse_timezone(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        timezone = _parse_timezone(d.pop("timezone", UNSET))

        triage_digest = cls(
            cron=cron,
            timezone=timezone,
        )

        return triage_digest
