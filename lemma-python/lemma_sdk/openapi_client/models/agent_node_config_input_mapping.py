from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.expression_input_binding import ExpressionInputBinding
    from ..models.literal_input_binding import LiteralInputBinding


T = TypeVar("T", bound="AgentNodeConfigInputMapping")


@_attrs_define
class AgentNodeConfigInputMapping:
    """Explicit mapping from agent input key to either an expression or a literal JSON value. Strings are never auto-
    interpreted.

        Example:
            {'channel': {'type': 'literal', 'value': 'finance'}, 'issue_key': {'type': 'expression', 'value':
                'start.payload.issue.key'}}

    """

    additional_properties: dict[str, ExpressionInputBinding | LiteralInputBinding] = (
        _attrs_field(init=False, factory=dict)
    )

    def to_dict(self) -> dict[str, Any]:
        from ..models.expression_input_binding import ExpressionInputBinding

        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            if isinstance(prop, ExpressionInputBinding):
                field_dict[prop_name] = prop.to_dict()
            else:
                field_dict[prop_name] = prop.to_dict()

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.expression_input_binding import ExpressionInputBinding
        from ..models.literal_input_binding import LiteralInputBinding

        d = dict(src_dict)
        agent_node_config_input_mapping = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():

            def _parse_additional_property(
                data: object,
            ) -> ExpressionInputBinding | LiteralInputBinding:
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    additional_property_type_0 = ExpressionInputBinding.from_dict(data)

                    return additional_property_type_0
                except TypeError, ValueError, AttributeError, KeyError:
                    pass
                if not isinstance(data, dict):
                    raise TypeError()
                additional_property_type_1 = LiteralInputBinding.from_dict(data)

                return additional_property_type_1

            additional_property = _parse_additional_property(prop_dict)

            additional_properties[prop_name] = additional_property

        agent_node_config_input_mapping.additional_properties = additional_properties
        return agent_node_config_input_mapping

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> ExpressionInputBinding | LiteralInputBinding:
        return self.additional_properties[key]

    def __setitem__(
        self, key: str, value: ExpressionInputBinding | LiteralInputBinding
    ) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
