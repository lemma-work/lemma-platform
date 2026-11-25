from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.agent_run_status import AgentRunStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ledger_totals import LedgerTotals


T = TypeVar("T", bound="RunUsageResponse")


@_attrs_define
class RunUsageResponse:
    """One run of a conversation and what it cost.

    Attributes:
        agent_run_id (UUID):
        started_at (datetime.datetime):
        status (AgentRunStatus): Internal lifecycle for one harness execution.
        finished_at (datetime.datetime | None | Unset):
        usage (LedgerTotals | None | Unset): What the run cost and the tokens it used; null when nothing was recorded
            against it, which is not the same as nothing spent. A run on an Agent Host is recorded only if its harness
            reports usage, and only as each turn ends. A run still working reads what has been recorded so far.
    """

    agent_run_id: UUID
    started_at: datetime.datetime
    status: AgentRunStatus
    finished_at: datetime.datetime | None | Unset = UNSET
    usage: LedgerTotals | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.ledger_totals import LedgerTotals

        agent_run_id = str(self.agent_run_id)

        started_at = self.started_at.isoformat()

        status = self.status.value

        finished_at: None | str | Unset
        if isinstance(self.finished_at, Unset):
            finished_at = UNSET
        elif isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        usage: dict[str, Any] | None | Unset
        if isinstance(self.usage, Unset):
            usage = UNSET
        elif isinstance(self.usage, LedgerTotals):
            usage = self.usage.to_dict()
        else:
            usage = self.usage

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent_run_id": agent_run_id,
                "started_at": started_at,
                "status": status,
            }
        )
        if finished_at is not UNSET:
            field_dict["finished_at"] = finished_at
        if usage is not UNSET:
            field_dict["usage"] = usage

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ledger_totals import LedgerTotals

        d = dict(src_dict)
        agent_run_id = UUID(d.pop("agent_run_id"))

        started_at = isoparse(d.pop("started_at"))

        status = AgentRunStatus(d.pop("status"))

        def _parse_finished_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                finished_at_type_0 = isoparse(data)

                return finished_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        finished_at = _parse_finished_at(d.pop("finished_at", UNSET))

        def _parse_usage(data: object) -> LedgerTotals | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                usage_type_0 = LedgerTotals.from_dict(data)

                return usage_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(LedgerTotals | None | Unset, data)

        usage = _parse_usage(d.pop("usage", UNSET))

        run_usage_response = cls(
            agent_run_id=agent_run_id,
            started_at=started_at,
            status=status,
            finished_at=finished_at,
            usage=usage,
        )

        run_usage_response.additional_properties = d
        return run_usage_response

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
