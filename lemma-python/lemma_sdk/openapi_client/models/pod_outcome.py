from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.outcome_kind import OutcomeKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="PodOutcome")


@_attrs_define
class PodOutcome:
    """What the pod owns, as stored on its config.

    Attributes:
        kind (OutcomeKind): The two shapes of what a pod owns.

            ``week`` is a bar held every week, which never finishes ("Every customer
            hears back within a day"). ``date`` is a finish line, reached once by its
            ``due`` date ("The launch ships by the date it is due"). The values are the plan
            file's own (``kind: week`` in ``/plan.md``), so a plan a person or the
            pod's agent wrote reads straight across.
        sentence (str): What it owns, as one sentence a person can check.
        due (datetime.date | None | Unset): The finish line's date. Required for kind=date, refused for week.
        measure (None | str | Unset): One read-only SQL query over the pod's tables that answers a single number: one
            row, one column. Counted for every member alike, so only someone who can read every row it touches may set it.
        row_security_checked (list[UUID] | Unset): The tables with row security the measure counts that were checked
            when it was set, by id: whoever set it administers each. Set by the server. A measure that counts a table with
            row security not listed here is not read until someone who administers that table sets it again.
        since (datetime.date | None | Unset): When the pod took this on: set by the server when it is first set, and
            kept while only its wording, target or measure change.
        target (float | None | Unset): The number the measure is held to: 90 with unit '%' for a share, 22 for a finish
            line of 22 steps. Unset while it is still being worked out.
        unit (None | str | Unset): '%' when the measure answers a share from 0 to 100; otherwise what it counts, such as
            'steps' or 'posts'.
    """

    kind: OutcomeKind
    sentence: str
    due: datetime.date | None | Unset = UNSET
    measure: None | str | Unset = UNSET
    row_security_checked: list[UUID] | Unset = UNSET
    since: datetime.date | None | Unset = UNSET
    target: float | None | Unset = UNSET
    unit: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        sentence = self.sentence

        due: None | str | Unset
        if isinstance(self.due, Unset):
            due = UNSET
        elif isinstance(self.due, datetime.date):
            due = self.due.isoformat()
        else:
            due = self.due

        measure: None | str | Unset
        if isinstance(self.measure, Unset):
            measure = UNSET
        else:
            measure = self.measure

        row_security_checked: list[str] | Unset = UNSET
        if not isinstance(self.row_security_checked, Unset):
            row_security_checked = []
            for row_security_checked_item_data in self.row_security_checked:
                row_security_checked_item = str(row_security_checked_item_data)
                row_security_checked.append(row_security_checked_item)

        since: None | str | Unset
        if isinstance(self.since, Unset):
            since = UNSET
        elif isinstance(self.since, datetime.date):
            since = self.since.isoformat()
        else:
            since = self.since

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
                "kind": kind,
                "sentence": sentence,
            }
        )
        if due is not UNSET:
            field_dict["due"] = due
        if measure is not UNSET:
            field_dict["measure"] = measure
        if row_security_checked is not UNSET:
            field_dict["row_security_checked"] = row_security_checked
        if since is not UNSET:
            field_dict["since"] = since
        if target is not UNSET:
            field_dict["target"] = target
        if unit is not UNSET:
            field_dict["unit"] = unit

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = OutcomeKind(d.pop("kind"))

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

        def _parse_measure(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        measure = _parse_measure(d.pop("measure", UNSET))

        _row_security_checked = d.pop("row_security_checked", UNSET)
        row_security_checked: list[UUID] | Unset = UNSET
        if _row_security_checked is not UNSET:
            row_security_checked = []
            for row_security_checked_item_data in _row_security_checked:
                row_security_checked_item = UUID(row_security_checked_item_data)

                row_security_checked.append(row_security_checked_item)

        def _parse_since(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                since_type_0 = isoparse(data).date()

                return since_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.date | None | Unset, data)

        since = _parse_since(d.pop("since", UNSET))

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

        pod_outcome = cls(
            kind=kind,
            sentence=sentence,
            due=due,
            measure=measure,
            row_security_checked=row_security_checked,
            since=since,
            target=target,
            unit=unit,
        )

        pod_outcome.additional_properties = d
        return pod_outcome

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
