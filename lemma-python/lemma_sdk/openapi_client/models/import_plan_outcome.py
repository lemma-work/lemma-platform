from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.import_plan_outcome_kind import ImportPlanOutcomeKind
from ..models.outcome_plan_action import OutcomePlanAction
from ..types import UNSET, Unset

T = TypeVar("T", bound="ImportPlanOutcome")


@_attrs_define
class ImportPlanOutcome:
    """What the bundle's pod owned, and what the import does with it.

    Attributes:
        action (OutcomePlanAction): What an import does with what its bundle's pod owned.
        kind (ImportPlanOutcomeKind): week: a bar held every week. date: a finish line by `due`.
        sentence (str): What it owns, as one sentence.
        due (datetime.date | None | Unset): The finish line's date.
        has_measure (bool | Unset): Whether the bundle carries a measure. It is read as the person importing when the
            import applies; one they cannot read is left off, with a warning, and the rest is still set. Default: False.
        target (float | None | Unset): The number its measure is held to, if set.
        unit (None | str | Unset): '%' for a share; otherwise what it counts.
    """

    action: OutcomePlanAction
    kind: ImportPlanOutcomeKind
    sentence: str
    due: datetime.date | None | Unset = UNSET
    has_measure: bool | Unset = False
    target: float | None | Unset = UNSET
    unit: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        action = self.action.value

        kind = self.kind.value

        sentence = self.sentence

        due: None | str | Unset
        if isinstance(self.due, Unset):
            due = UNSET
        elif isinstance(self.due, datetime.date):
            due = self.due.isoformat()
        else:
            due = self.due

        has_measure = self.has_measure

        target: float | None | Unset
        if isinstance(self.target, Unset):
            target = UNSET
        else:
            target = self.target

        unit: None | str | Unset
        if isinstance(self.unit, Unset):
            unit = UNSET
        else:
            unit = self.unit

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "action": action,
                "kind": kind,
                "sentence": sentence,
            }
        )
        if due is not UNSET:
            field_dict["due"] = due
        if has_measure is not UNSET:
            field_dict["has_measure"] = has_measure
        if target is not UNSET:
            field_dict["target"] = target
        if unit is not UNSET:
            field_dict["unit"] = unit

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        action = OutcomePlanAction(d.pop("action"))

        kind = ImportPlanOutcomeKind(d.pop("kind"))

        sentence = d.pop("sentence")

        def _parse_due(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                due_type_0 = isoparse(data).date()

                return due_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.date | None | Unset, data)

        due = _parse_due(d.pop("due", UNSET))

        has_measure = d.pop("has_measure", UNSET)

        def _parse_target(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        target = _parse_target(d.pop("target", UNSET))

        def _parse_unit(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        unit = _parse_unit(d.pop("unit", UNSET))

        import_plan_outcome = cls(
            action=action,
            kind=kind,
            sentence=sentence,
            due=due,
            has_measure=has_measure,
            target=target,
            unit=unit,
        )

        import_plan_outcome.additional_properties = d
        return import_plan_outcome

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
