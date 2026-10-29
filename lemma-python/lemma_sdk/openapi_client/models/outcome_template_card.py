from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.outcome_kind import OutcomeKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.template_outcome import TemplateOutcome
    from ..models.template_plan import TemplatePlan


T = TypeVar("T", bound="OutcomeTemplateCard")


@_attrs_define
class OutcomeTemplateCard:
    """One Lemma a person can start, as the shelf shows it.

    Attributes:
        id (str): The template's name, passed as `template` to start one.
        kind (OutcomeKind): The two shapes of what a pod owns.

            ``week`` is a bar held every week, which never finishes ("Every customer
            hears back within a day"). ``date`` is a finish line, reached once by its
            ``due`` date ("The launch ships by the date it is due"). The values are the plan
            file's own (``kind: week`` in ``/plan.md``), so a plan a person or the
            pod's agent wrote reads straight across.
        name (str): The short name a Lemma made from it starts with.
        outcome (TemplateOutcome): What a template's Lemma owns, before its date is known: a finish
            line's sentence still carries ``{due}``.
        sentence (str): What it owns. A finish line's carries {due}.
        brings (list[str] | Unset):
        description (None | str | Unset):
        face (None | str | Unset): Which character's face it wears.
        place (None | str | Unset): Where its work usually happens.
        plan (TemplatePlan | Unset): The first lines of the plan a Lemma made from this template starts with.
        position (int | Unset):  Default: 100.
    """

    id: str
    kind: OutcomeKind
    name: str
    outcome: TemplateOutcome
    sentence: str
    brings: list[str] | Unset = UNSET
    description: None | str | Unset = UNSET
    face: None | str | Unset = UNSET
    place: None | str | Unset = UNSET
    plan: TemplatePlan | Unset = UNSET
    position: int | Unset = 100
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        kind = self.kind.value

        name = self.name

        outcome = self.outcome.to_dict()

        sentence = self.sentence

        brings: list[str] | Unset = UNSET
        if not isinstance(self.brings, Unset):
            brings = self.brings

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        face: None | str | Unset
        if isinstance(self.face, Unset):
            face = UNSET
        else:
            face = self.face

        place: None | str | Unset
        if isinstance(self.place, Unset):
            place = UNSET
        else:
            place = self.place

        plan: dict[str, Any] | Unset = UNSET
        if not isinstance(self.plan, Unset):
            plan = self.plan.to_dict()

        position = self.position

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "kind": kind,
                "name": name,
                "outcome": outcome,
                "sentence": sentence,
            }
        )
        if brings is not UNSET:
            field_dict["brings"] = brings
        if description is not UNSET:
            field_dict["description"] = description
        if face is not UNSET:
            field_dict["face"] = face
        if place is not UNSET:
            field_dict["place"] = place
        if plan is not UNSET:
            field_dict["plan"] = plan
        if position is not UNSET:
            field_dict["position"] = position

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.template_outcome import TemplateOutcome
        from ..models.template_plan import TemplatePlan

        d = dict(src_dict)
        id = d.pop("id")

        kind = OutcomeKind(d.pop("kind"))

        name = d.pop("name")

        outcome = TemplateOutcome.from_dict(d.pop("outcome"))

        sentence = d.pop("sentence")

        brings = cast(list[str], d.pop("brings", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_face(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        face = _parse_face(d.pop("face", UNSET))

        def _parse_place(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        place = _parse_place(d.pop("place", UNSET))

        _plan = d.pop("plan", UNSET)
        plan: TemplatePlan | Unset
        if isinstance(_plan, Unset):
            plan = UNSET
        else:
            plan = TemplatePlan.from_dict(_plan)

        position = d.pop("position", UNSET)

        outcome_template_card = cls(
            id=id,
            kind=kind,
            name=name,
            outcome=outcome,
            sentence=sentence,
            brings=brings,
            description=description,
            face=face,
            place=place,
            plan=plan,
            position=position,
        )

        outcome_template_card.additional_properties = d
        return outcome_template_card

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
