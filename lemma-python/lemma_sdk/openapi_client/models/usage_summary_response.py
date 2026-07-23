from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.usage_summary_response_total_by_kind import (
        UsageSummaryResponseTotalByKind,
    )
    from ..models.usage_summary_response_total_by_model import (
        UsageSummaryResponseTotalByModel,
    )
    from ..models.usage_summary_response_total_by_profile import (
        UsageSummaryResponseTotalByProfile,
    )


T = TypeVar("T", bound="UsageSummaryResponse")


@_attrs_define
class UsageSummaryResponse:
    """
    Attributes:
        end_date (datetime.datetime):
        period_days (int):
        start_date (datetime.datetime):
        system_cost_usd (float):
        total_by_kind (UsageSummaryResponseTotalByKind):
        total_by_model (UsageSummaryResponseTotalByModel):
        total_by_profile (UsageSummaryResponseTotalByProfile):
        total_input_tokens (int):
        total_output_tokens (int):
        total_tokens (int):
        total_units (float):
        agent_id (None | Unset | UUID):
        organization_id (None | Unset | UUID):
        pod_id (None | Unset | UUID):
        user_id (None | Unset | UUID):
    """

    end_date: datetime.datetime
    period_days: int
    start_date: datetime.datetime
    system_cost_usd: float
    total_by_kind: UsageSummaryResponseTotalByKind
    total_by_model: UsageSummaryResponseTotalByModel
    total_by_profile: UsageSummaryResponseTotalByProfile
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_units: float
    agent_id: None | Unset | UUID = UNSET
    organization_id: None | Unset | UUID = UNSET
    pod_id: None | Unset | UUID = UNSET
    user_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        end_date = self.end_date.isoformat()

        period_days = self.period_days

        start_date = self.start_date.isoformat()

        system_cost_usd = self.system_cost_usd

        total_by_kind = self.total_by_kind.to_dict()

        total_by_model = self.total_by_model.to_dict()

        total_by_profile = self.total_by_profile.to_dict()

        total_input_tokens = self.total_input_tokens

        total_output_tokens = self.total_output_tokens

        total_tokens = self.total_tokens

        total_units = self.total_units

        agent_id: None | str | Unset
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        elif isinstance(self.agent_id, UUID):
            agent_id = str(self.agent_id)
        else:
            agent_id = self.agent_id

        organization_id: None | str | Unset
        if isinstance(self.organization_id, Unset):
            organization_id = UNSET
        elif isinstance(self.organization_id, UUID):
            organization_id = str(self.organization_id)
        else:
            organization_id = self.organization_id

        pod_id: None | str | Unset
        if isinstance(self.pod_id, Unset):
            pod_id = UNSET
        elif isinstance(self.pod_id, UUID):
            pod_id = str(self.pod_id)
        else:
            pod_id = self.pod_id

        user_id: None | str | Unset
        if isinstance(self.user_id, Unset):
            user_id = UNSET
        elif isinstance(self.user_id, UUID):
            user_id = str(self.user_id)
        else:
            user_id = self.user_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "end_date": end_date,
                "period_days": period_days,
                "start_date": start_date,
                "system_cost_usd": system_cost_usd,
                "total_by_kind": total_by_kind,
                "total_by_model": total_by_model,
                "total_by_profile": total_by_profile,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_tokens": total_tokens,
                "total_units": total_units,
            }
        )
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if organization_id is not UNSET:
            field_dict["organization_id"] = organization_id
        if pod_id is not UNSET:
            field_dict["pod_id"] = pod_id
        if user_id is not UNSET:
            field_dict["user_id"] = user_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.usage_summary_response_total_by_kind import (
            UsageSummaryResponseTotalByKind,
        )
        from ..models.usage_summary_response_total_by_model import (
            UsageSummaryResponseTotalByModel,
        )
        from ..models.usage_summary_response_total_by_profile import (
            UsageSummaryResponseTotalByProfile,
        )

        d = dict(src_dict)
        end_date = isoparse(d.pop("end_date"))

        period_days = d.pop("period_days")

        start_date = isoparse(d.pop("start_date"))

        system_cost_usd = d.pop("system_cost_usd")

        total_by_kind = UsageSummaryResponseTotalByKind.from_dict(
            d.pop("total_by_kind")
        )

        total_by_model = UsageSummaryResponseTotalByModel.from_dict(
            d.pop("total_by_model")
        )

        total_by_profile = UsageSummaryResponseTotalByProfile.from_dict(
            d.pop("total_by_profile")
        )

        total_input_tokens = d.pop("total_input_tokens")

        total_output_tokens = d.pop("total_output_tokens")

        total_tokens = d.pop("total_tokens")

        total_units = d.pop("total_units")

        def _parse_agent_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                agent_id_type_0 = UUID(data)

                return agent_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        agent_id = _parse_agent_id(d.pop("agent_id", UNSET))

        def _parse_organization_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                organization_id_type_0 = UUID(data)

                return organization_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        organization_id = _parse_organization_id(d.pop("organization_id", UNSET))

        def _parse_pod_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pod_id_type_0 = UUID(data)

                return pod_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        pod_id = _parse_pod_id(d.pop("pod_id", UNSET))

        def _parse_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                user_id_type_0 = UUID(data)

                return user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        user_id = _parse_user_id(d.pop("user_id", UNSET))

        usage_summary_response = cls(
            end_date=end_date,
            period_days=period_days,
            start_date=start_date,
            system_cost_usd=system_cost_usd,
            total_by_kind=total_by_kind,
            total_by_model=total_by_model,
            total_by_profile=total_by_profile,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_tokens=total_tokens,
            total_units=total_units,
            agent_id=agent_id,
            organization_id=organization_id,
            pod_id=pod_id,
            user_id=user_id,
        )

        usage_summary_response.additional_properties = d
        return usage_summary_response

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
