from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.decider_test_body_visibility import DeciderTestBodyVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.decider_definition import DeciderDefinition
    from ..models.decider_test_body_options import DeciderTestBodyOptions
    from ..models.sample_row_body import SampleRowBody


T = TypeVar("T", bound="DeciderTestBody")


@_attrs_define
class DeciderTestBody:
    """
    Attributes:
        rows (list[SampleRowBody]):
        decider (None | str | Unset): A pod decider's name, or `system:<name>` for one that ships with Lemma. Leave out
            to ask the `definition` inline.
        definition (DeciderDefinition | None | Unset): Questions to ask without a named decider. Nothing is learned for
            them.
        options (DeciderTestBodyOptions | Unset): Per question, options to add to the declared ones for this call: the
            person's pods, the open conversations, a list built just now.
        record (bool | Unset): Keep the decision. Off answers without keeping anything. Default: True.
        visibility (DeciderTestBodyVisibility | Unset): Who may read the recorded decision, evidence included: only you,
            or everyone in the pod who can read deciders. Personal unless the state is the pod's to share, because evidence
            is whatever was decided about -- an inbox, a call, a conversation. Default: DeciderTestBodyVisibility.PERSONAL.
    """

    rows: list[SampleRowBody]
    decider: None | str | Unset = UNSET
    definition: DeciderDefinition | None | Unset = UNSET
    options: DeciderTestBodyOptions | Unset = UNSET
    record: bool | Unset = True
    visibility: DeciderTestBodyVisibility | Unset = DeciderTestBodyVisibility.PERSONAL

    def to_dict(self) -> dict[str, Any]:
        from ..models.decider_definition import DeciderDefinition

        rows = []
        for rows_item_data in self.rows:
            rows_item = rows_item_data.to_dict()
            rows.append(rows_item)

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
        if options is not UNSET:
            field_dict["options"] = options
        if record is not UNSET:
            field_dict["record"] = record
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decider_definition import DeciderDefinition
        from ..models.decider_test_body_options import DeciderTestBodyOptions
        from ..models.sample_row_body import SampleRowBody

        d = dict(src_dict)
        rows = []
        _rows = d.pop("rows")
        for rows_item_data in _rows:
            rows_item = SampleRowBody.from_dict(rows_item_data)

            rows.append(rows_item)

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
        options: DeciderTestBodyOptions | Unset
        if isinstance(_options, Unset):
            options = UNSET
        else:
            options = DeciderTestBodyOptions.from_dict(_options)

        record = d.pop("record", UNSET)

        _visibility = d.pop("visibility", UNSET)
        visibility: DeciderTestBodyVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = DeciderTestBodyVisibility(_visibility)

        decider_test_body = cls(
            rows=rows,
            decider=decider,
            definition=definition,
            options=options,
            record=record,
            visibility=visibility,
        )

        return decider_test_body
