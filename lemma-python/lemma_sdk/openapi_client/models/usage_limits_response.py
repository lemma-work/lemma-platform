from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.usage_limit_scope_response import UsageLimitScopeResponse


T = TypeVar("T", bound="UsageLimitsResponse")


@_attrs_define
class UsageLimitsResponse:
    """
    Attributes:
        allowed (bool):
        org_monthly (UsageLimitScopeResponse):
        organization_id (None | UUID):
        user_id (UUID):
        user_monthly (UsageLimitScopeResponse):
        user_weekly (UsageLimitScopeResponse):
    """

    allowed: bool
    org_monthly: UsageLimitScopeResponse
    organization_id: None | UUID
    user_id: UUID
    user_monthly: UsageLimitScopeResponse
    user_weekly: UsageLimitScopeResponse
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        allowed = self.allowed

        org_monthly = self.org_monthly.to_dict()

        organization_id: None | str
        if isinstance(self.organization_id, UUID):
            organization_id = str(self.organization_id)
        else:
            organization_id = self.organization_id

        user_id = str(self.user_id)

        user_monthly = self.user_monthly.to_dict()

        user_weekly = self.user_weekly.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "allowed": allowed,
                "org_monthly": org_monthly,
                "organization_id": organization_id,
                "user_id": user_id,
                "user_monthly": user_monthly,
                "user_weekly": user_weekly,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.usage_limit_scope_response import UsageLimitScopeResponse

        d = dict(src_dict)
        allowed = d.pop("allowed")

        org_monthly = UsageLimitScopeResponse.from_dict(d.pop("org_monthly"))

        def _parse_organization_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                organization_id_type_0 = UUID(data)

                return organization_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        organization_id = _parse_organization_id(d.pop("organization_id"))

        user_id = UUID(d.pop("user_id"))

        user_monthly = UsageLimitScopeResponse.from_dict(d.pop("user_monthly"))

        user_weekly = UsageLimitScopeResponse.from_dict(d.pop("user_weekly"))

        usage_limits_response = cls(
            allowed=allowed,
            org_monthly=org_monthly,
            organization_id=organization_id,
            user_id=user_id,
            user_monthly=user_monthly,
            user_weekly=user_weekly,
        )

        usage_limits_response.additional_properties = d
        return usage_limits_response

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
