from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.ledger_totals import LedgerTotals
    from ..models.run_usage_response import RunUsageResponse


T = TypeVar("T", bound="ConversationUsageResponse")


@_attrs_define
class ConversationUsageResponse:
    """What one conversation has cost its owner so far.

    Attributes:
        conversation_id (UUID):
        has_more_runs (bool): Whether older runs exist than `runs` lists; `totals` has them.
        run_count (int): Every run of the conversation.
        runs (list[RunUsageResponse]): The newest runs, newest first, each with its own usage.
        totals (LedgerTotals): What a set of ledger rows cost, and the model tokens among them.
        unreported_run_count (int): Runs with nothing recorded against them. Above zero, `totals` leaves those runs out:
            show the total as incomplete, not as everything spent.
    """

    conversation_id: UUID
    has_more_runs: bool
    run_count: int
    runs: list[RunUsageResponse]
    totals: LedgerTotals
    unreported_run_count: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        conversation_id = str(self.conversation_id)

        has_more_runs = self.has_more_runs

        run_count = self.run_count

        runs = []
        for runs_item_data in self.runs:
            runs_item = runs_item_data.to_dict()
            runs.append(runs_item)

        totals = self.totals.to_dict()

        unreported_run_count = self.unreported_run_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "conversation_id": conversation_id,
                "has_more_runs": has_more_runs,
                "run_count": run_count,
                "runs": runs,
                "totals": totals,
                "unreported_run_count": unreported_run_count,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ledger_totals import LedgerTotals
        from ..models.run_usage_response import RunUsageResponse

        d = dict(src_dict)
        conversation_id = UUID(d.pop("conversation_id"))

        has_more_runs = d.pop("has_more_runs")

        run_count = d.pop("run_count")

        runs = []
        _runs = d.pop("runs")
        for runs_item_data in _runs:
            runs_item = RunUsageResponse.from_dict(runs_item_data)

            runs.append(runs_item)

        totals = LedgerTotals.from_dict(d.pop("totals"))

        unreported_run_count = d.pop("unreported_run_count")

        conversation_usage_response = cls(
            conversation_id=conversation_id,
            has_more_runs=has_more_runs,
            run_count=run_count,
            runs=runs,
            totals=totals,
            unreported_run_count=unreported_run_count,
        )

        conversation_usage_response.additional_properties = d
        return conversation_usage_response

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
