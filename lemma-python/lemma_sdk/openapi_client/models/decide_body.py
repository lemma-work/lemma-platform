from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.decide_body_visibility import DecideBodyVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.decide_body_options import DecideBodyOptions
    from ..models.decider_definition import DeciderDefinition


T = TypeVar("T", bound="DecideBody")


@_attrs_define
class DecideBody:
    """
    Attributes:
        state (Any):
        decider (None | str | Unset): A pod decider's name, or `system:<name>` for one that ships with Lemma. Leave out
            to ask the `definition` inline.
        definition (DeciderDefinition | None | Unset): Questions to ask without a named decider. Nothing is learned for
            them.
        options (DecideBodyOptions | Unset): Per question, options to add to the declared ones for this call: the
            person's pods, the open conversations, a list built just now.
        record (bool | Unset): Keep the decision. Off answers without keeping anything. Default: True.
        subject (None | str | Unset): What the decision is about, stable across retries. A decider is asked about a
            subject once; asking again returns the recorded decision.
        visibility (DecideBodyVisibility | Unset): Who may read the recorded decision, evidence included: only you, or
            everyone in the pod who can read deciders. Personal unless the state is the pod's to share, because evidence is
            whatever was decided about -- an inbox, a call, a conversation. Default: DecideBodyVisibility.PERSONAL.
    """

    state: Any
    decider: None | str | Unset = UNSET
    definition: DeciderDefinition | None | Unset = UNSET
    options: DecideBodyOptions | Unset = UNSET
    record: bool | Unset = True
    subject: None | str | Unset = UNSET
    visibility: DecideBodyVisibility | Unset = DecideBodyVisibility.PERSONAL

    def to_dict(self) -> dict[str, Any]:
        from ..models.decider_definition import DeciderDefinition

        state = self.state

        decider: None | str | Unset
        if isinstance(self.decider, Unset):
            decider = UNSET
        else:
            decider = self.decider

        definition: dict[str, Any] | None | Unset
        if isinstance(self.definition, Unset):
            definition = UNSET
        elif isinstance(self.definition, DeciderDefinition):
            definition = self.definition.to_dict()
        else:
            definition = self.definition

        options: dict[str, Any] | Unset = UNSET
        if not isinstance(self.options, Unset):
            options = self.options.to_dict()

        record = self.record

        subject: None | str | Unset
        if isinstance(self.subject, Unset):
            subject = UNSET
        else:
            subject = self.subject

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "state": state,
            }
        )
        if decider is not UNSET:
            field_dict["decider"] = decider
        if definition is not UNSET:
            field_dict["definition"] = definition
        if options is not UNSET:
            field_dict["options"] = options
        if record is not UNSET:
            field_dict["record"] = record
        if subject is not UNSET:
            field_dict["subject"] = subject
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decide_body_options import DecideBodyOptions
        from ..models.decider_definition import DeciderDefinition

        d = dict(src_dict)
        state = d.pop("state")

        def _parse_decider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        decider = _parse_decider(d.pop("decider", UNSET))

        def _parse_definition(data: object) -> DeciderDefinition | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                definition_type_0 = DeciderDefinition.from_dict(data)

                return definition_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(DeciderDefinition | None | Unset, data)

        definition = _parse_definition(d.pop("definition", UNSET))

        _options = d.pop("options", UNSET)
        options: DecideBodyOptions | Unset
        if isinstance(_options, Unset):
            options = UNSET
        else:
            options = DecideBodyOptions.from_dict(_options)

        record = d.pop("record", UNSET)

        def _parse_subject(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        subject = _parse_subject(d.pop("subject", UNSET))

        _visibility = d.pop("visibility", UNSET)
        visibility: DecideBodyVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = DecideBodyVisibility(_visibility)

        decide_body = cls(
            state=state,
            decider=decider,
            definition=definition,
            options=options,
            record=record,
            subject=subject,
            visibility=visibility,
        )

        return decide_body
