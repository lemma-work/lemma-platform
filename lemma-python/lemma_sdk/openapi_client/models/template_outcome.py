from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.outcome_kind import OutcomeKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="TemplateOutcome")


@_attrs_define
class TemplateOutcome:
    """What a template's Lemma owns, before its date is known: a finish
    line's sentence still carries ``{due}``.

        Attributes:
            kind (OutcomeKind): The two shapes of what a pod owns.

                ``week`` is a bar held every week, which never finishes ("Every customer
                hears back within a day"). ``date`` is a finish line, reached once by its
                ``due`` date ("The launch ships by the date it is due"). The values are the plan
                file's own (``kind: week`` in ``/plan.md``), so a plan a person or the
                pod's agent wrote reads straight across.
            sentence (str):
            measure (None | str | Unset): Counts rows of the tables the template brings.
            target (float | None | Unset):
            unit (None | str | Unset):
    """

    kind: OutcomeKind
    sentence: str
    measure: None | str | Unset = UNSET
    target: float | None | Unset = UNSET
    unit: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        sentence = self.sentence

        measure: None | str | Unset
        if isinstance(self.measure, Unset):
            measure = UNSET
        else:
            measure = self.measure

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
        if measure is not UNSET:
            field_dict["measure"] = measure
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

        def _parse_measure(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        measure = _parse_measure(d.pop("measure", UNSET))

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

        template_outcome = cls(
            kind=kind,
            sentence=sentence,
            measure=measure,
            target=target,
            unit=unit,
        )

        template_outcome.additional_properties = d
        return template_outcome

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
