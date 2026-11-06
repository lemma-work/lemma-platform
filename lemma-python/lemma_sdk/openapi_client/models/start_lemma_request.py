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
    from ..models.outcome_input import OutcomeInput


T = TypeVar("T", bound="StartLemmaRequest")


@_attrs_define
class StartLemmaRequest:
    """Body for starting a Lemma: from a template, or from its own words.

    Attributes:
        organization_id (UUID):
        description (None | str | Unset): What it is for. A template's own when omitted.
        due (datetime.date | None | Unset): For a template that is a finish line: the date it is due by.
        name (None | str | Unset): Its short name: the template's when omitted, and required without one. A name already
            taken in the organization gets a number after it.
        outcome (None | OutcomeInput | Unset): What it owns, for a Lemma started without a template. Without a measure:
            nothing it could count exists yet, so its agent sets one once it has built what it counts.
        template (None | str | Unset): A template from GET /pods/bundle/templates. It decides what the Lemma owns and
            brings the tables, skill and schedules that count it.
    """

    organization_id: UUID
    description: None | str | Unset = UNSET
    due: datetime.date | None | Unset = UNSET
    name: None | str | Unset = UNSET
    outcome: None | OutcomeInput | Unset = UNSET
    template: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.outcome_input import OutcomeInput

        organization_id = str(self.organization_id)

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        due: None | str | Unset
        if isinstance(self.due, Unset):
            due = UNSET
        elif isinstance(self.due, datetime.date):
            due = self.due.isoformat()
        else:
            due = self.due

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        outcome: dict[str, Any] | None | Unset
        if isinstance(self.outcome, Unset):
            outcome = UNSET
        elif isinstance(self.outcome, OutcomeInput):
            outcome = self.outcome.to_dict()
        else:
            outcome = self.outcome

        template: None | str | Unset
        if isinstance(self.template, Unset):
            template = UNSET
        else:
            template = self.template

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "organization_id": organization_id,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if due is not UNSET:
            field_dict["due"] = due
        if name is not UNSET:
            field_dict["name"] = name
        if outcome is not UNSET:
            field_dict["outcome"] = outcome
        if template is not UNSET:
            field_dict["template"] = template

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.outcome_input import OutcomeInput

        d = dict(src_dict)
        organization_id = UUID(d.pop("organization_id"))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

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

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_outcome(data: object) -> None | OutcomeInput | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                outcome_type_0 = OutcomeInput.from_dict(data)

                return outcome_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | OutcomeInput | Unset, data)

        outcome = _parse_outcome(d.pop("outcome", UNSET))

        def _parse_template(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        template = _parse_template(d.pop("template", UNSET))

        start_lemma_request = cls(
            organization_id=organization_id,
            description=description,
            due=due,
            name=name,
            outcome=outcome,
            template=template,
        )

        start_lemma_request.additional_properties = d
        return start_lemma_request

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
