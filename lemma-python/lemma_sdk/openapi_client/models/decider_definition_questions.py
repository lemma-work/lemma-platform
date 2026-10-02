from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.choice_question import ChoiceQuestion
    from ..models.multi_choice_question import MultiChoiceQuestion
    from ..models.scale_question import ScaleQuestion
    from ..models.yes_no_question import YesNoQuestion


T = TypeVar("T", bound="DeciderDefinitionQuestions")


@_attrs_define
class DeciderDefinitionQuestions:
    """ """

    additional_properties: dict[
        str, ChoiceQuestion | MultiChoiceQuestion | ScaleQuestion | YesNoQuestion
    ] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.choice_question import ChoiceQuestion
        from ..models.multi_choice_question import MultiChoiceQuestion
        from ..models.yes_no_question import YesNoQuestion

        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            if isinstance(prop, ChoiceQuestion):
                field_dict[prop_name] = prop.to_dict()
            elif isinstance(prop, MultiChoiceQuestion):
                field_dict[prop_name] = prop.to_dict()
            elif isinstance(prop, YesNoQuestion):
                field_dict[prop_name] = prop.to_dict()
            else:
                field_dict[prop_name] = prop.to_dict()

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.choice_question import ChoiceQuestion
        from ..models.multi_choice_question import MultiChoiceQuestion
        from ..models.scale_question import ScaleQuestion
        from ..models.yes_no_question import YesNoQuestion

        d = dict(src_dict)
        decider_definition_questions = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():

            def _parse_additional_property(
                data: object,
            ) -> ChoiceQuestion | MultiChoiceQuestion | ScaleQuestion | YesNoQuestion:
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    additional_property_type_0 = ChoiceQuestion.from_dict(data)

                    return additional_property_type_0
                except TypeError, ValueError, AttributeError, KeyError:
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    additional_property_type_1 = MultiChoiceQuestion.from_dict(data)

                    return additional_property_type_1
                except TypeError, ValueError, AttributeError, KeyError:
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    additional_property_type_2 = YesNoQuestion.from_dict(data)

                    return additional_property_type_2
                except TypeError, ValueError, AttributeError, KeyError:
                    pass
                if not isinstance(data, dict):
                    raise TypeError()
                additional_property_type_3 = ScaleQuestion.from_dict(data)

                return additional_property_type_3

            additional_property = _parse_additional_property(prop_dict)

            additional_properties[prop_name] = additional_property

        decider_definition_questions.additional_properties = additional_properties
        return decider_definition_questions

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(
        self, key: str
    ) -> ChoiceQuestion | MultiChoiceQuestion | ScaleQuestion | YesNoQuestion:
        return self.additional_properties[key]

    def __setitem__(
        self,
        key: str,
        value: ChoiceQuestion | MultiChoiceQuestion | ScaleQuestion | YesNoQuestion,
    ) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
