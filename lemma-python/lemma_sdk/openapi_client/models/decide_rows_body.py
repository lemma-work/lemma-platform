from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.decide_rows_body_visibility import DecideRowsBodyVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.decide_rows_body_options import DecideRowsBodyOptions
    from ..models.decider_definition import DeciderDefinition


T = TypeVar("T", bound="DecideRowsBody")


@_attrs_define
class DecideRowsBody:
    """
    Attributes:
        rows (list[Any]):
        decider (None | str | Unset): A pod decider's name, or `system:<name>` for one that ships with Lemma. Leave out
            to ask the `definition` inline.
        definition (DeciderDefinition | None | Unset): Questions to ask without a named decider. Nothing is learned for
            them.
        id_field (None | str | Unset): The field that identifies a row. Row numbers are used when absent.
        options (DecideRowsBodyOptions | Unset): Per question, options to add to the declared ones for this call: the
            person's pods, the open conversations, a list built just now.
        record (bool | Unset): Keep the decision. Off answers without keeping anything. Default: True.
        subject_prefix (None | str | Unset): Prefix for each row's subject (`<prefix>:<row id>`), so deciding the same
            rows again returns the recorded decisions.
        visibility (DecideRowsBodyVisibility | Unset): Who may read the recorded decision, evidence included: only you,
            or everyone in the pod who can read deciders. Personal unless the state is the pod's to share, because evidence
            is whatever was decided about -- an inbox, a call, a conversation. Default: DecideRowsBodyVisibility.PERSONAL.
    """

    rows: list[Any]
    decider: None | str | Unset = UNSET
    definition: DeciderDefinition | None | Unset = UNSET
    id_field: None | str | Unset = UNSET
    options: DecideRowsBodyOptions | Unset = UNSET
    record: bool | Unset = True
    subject_prefix: None | str | Unset = UNSET
    visibility: DecideRowsBodyVisibility | Unset = DecideRowsBodyVisibility.PERSONAL

    def to_dict(self) -> dict[str, Any]:
        from ..models.decider_definition import DeciderDefinition

        rows = self.rows

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

        id_field: None | str | Unset
        if isinstance(self.id_field, Unset):
            id_field = UNSET
        else:
            id_field = self.id_field

        options: dict[str, Any] | Unset = UNSET
        if not isinstance(self.options, Unset):
            options = self.options.to_dict()

        record = self.record

        subject_prefix: None | str | Unset
        if isinstance(self.subject_prefix, Unset):
            subject_prefix = UNSET
        else:
            subject_prefix = self.subject_prefix

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "rows": rows,
            }
        )
        if decider is not UNSET:
            field_dict["decider"] = decider
        if definition is not UNSET:
            field_dict["definition"] = definition
        if id_field is not UNSET:
            field_dict["id_field"] = id_field
        if options is not UNSET:
            field_dict["options"] = options
        if record is not UNSET:
            field_dict["record"] = record
        if subject_prefix is not UNSET:
            field_dict["subject_prefix"] = subject_prefix
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decide_rows_body_options import DecideRowsBodyOptions
        from ..models.decider_definition import DeciderDefinition

        d = dict(src_dict)
        rows = cast(list[Any], d.pop("rows"))

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

        def _parse_id_field(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        id_field = _parse_id_field(d.pop("id_field", UNSET))

        _options = d.pop("options", UNSET)
        options: DecideRowsBodyOptions | Unset
        if isinstance(_options, Unset):
            options = UNSET
        else:
            options = DecideRowsBodyOptions.from_dict(_options)

        record = d.pop("record", UNSET)

        def _parse_subject_prefix(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        subject_prefix = _parse_subject_prefix(d.pop("subject_prefix", UNSET))

        _visibility = d.pop("visibility", UNSET)
        visibility: DecideRowsBodyVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = DecideRowsBodyVisibility(_visibility)

        decide_rows_body = cls(
            rows=rows,
            decider=decider,
            definition=definition,
            id_field=id_field,
            options=options,
            record=record,
            subject_prefix=subject_prefix,
            visibility=visibility,
        )

        return decide_rows_body
