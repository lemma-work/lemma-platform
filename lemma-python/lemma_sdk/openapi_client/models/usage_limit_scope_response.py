from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="UsageLimitScopeResponse")


@_attrs_define
class UsageLimitScopeResponse:
    """One spend window, as a caller outside the deployment may see it.

    **The cap is expressed as a percentage, never as an amount.** This used to
    carry `limit_usd` and `remaining_usd`, which state a dollar allowance —
    and a dollar allowance is a promise the product does not make. What a plan
    includes is set per plan and may be retuned; what a given request costs
    depends on the model it routes to. Publishing "$12.40 remaining" invites a
    customer to plan against a number that is neither fixed nor ours to
    guarantee, and turns any retune into a broken promise.

    What is published instead is how much of the window is gone. That is the
    fact a caller can act on — show a meter, warn at 80%, stop starting new
    work — and it stays true however the underlying allowance is set.

    `used_usd` and `reserved_usd` remain, and deliberately: those are what the
    customer has actually spent, which is theirs to know. It is the *boundary*
    that is percentage-only, not the consumption.

    The internal `UsageLimitScope` keeps its dollar fields — enforcement is done
    in dollars, and `usage_service` reserves against them. This is the API
    boundary, and the boundary is where the promise is made.

        Attributes:
            allowed (bool):
            reserved_usd (float):
            reset_at (datetime.datetime):
            scope (str):
            used_usd (float):
            window_start (datetime.datetime):
            used_percent (float | None | Unset):
    """

    allowed: bool
    reserved_usd: float
    reset_at: datetime.datetime
    scope: str
    used_usd: float
    window_start: datetime.datetime
    used_percent: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        allowed = self.allowed

        reserved_usd = self.reserved_usd

        reset_at = self.reset_at.isoformat()

        scope = self.scope

        used_usd = self.used_usd

        window_start = self.window_start.isoformat()

        used_percent: float | None | Unset
        if isinstance(self.used_percent, Unset):
            used_percent = UNSET
        else:
            used_percent = self.used_percent

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "allowed": allowed,
                "reserved_usd": reserved_usd,
                "reset_at": reset_at,
                "scope": scope,
                "used_usd": used_usd,
                "window_start": window_start,
            }
        )
        if used_percent is not UNSET:
            field_dict["used_percent"] = used_percent

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        allowed = d.pop("allowed")

        reserved_usd = d.pop("reserved_usd")

        reset_at = isoparse(d.pop("reset_at"))

        scope = d.pop("scope")

        used_usd = d.pop("used_usd")

        window_start = isoparse(d.pop("window_start"))

        def _parse_used_percent(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        used_percent = _parse_used_percent(d.pop("used_percent", UNSET))

        usage_limit_scope_response = cls(
            allowed=allowed,
            reserved_usd=reserved_usd,
            reset_at=reset_at,
            scope=scope,
            used_usd=used_usd,
            window_start=window_start,
            used_percent=used_percent,
        )

        usage_limit_scope_response.additional_properties = d
        return usage_limit_scope_response

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
