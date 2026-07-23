from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.form_node_config_input_schema import FormNodeConfigInputSchema
    from ..models.form_node_config_ui_schema_type_0 import FormNodeConfigUiSchemaType0


T = TypeVar("T", bound="FormNodeConfig")


@_attrs_define
class FormNodeConfig:
    """Configuration for Form node (user input).

    Attributes:
        input_schema (FormNodeConfigInputSchema): JSON Schema for user input
        assignee_pod_member_id (None | Unset | UUID): Pod member assigned to submit this form.
        assignee_pod_member_id_expression (None | str | Unset): Optional JMESPath expression resolving to a pod member
            id. Takes precedence over assignee_pod_member_id.
        ui_schema (FormNodeConfigUiSchemaType0 | None | Unset): UI configuration
    """

    input_schema: FormNodeConfigInputSchema
    assignee_pod_member_id: None | Unset | UUID = UNSET
    assignee_pod_member_id_expression: None | str | Unset = UNSET
    ui_schema: FormNodeConfigUiSchemaType0 | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.form_node_config_ui_schema_type_0 import (
            FormNodeConfigUiSchemaType0,
        )

        input_schema = self.input_schema.to_dict()

        assignee_pod_member_id: None | str | Unset
        if isinstance(self.assignee_pod_member_id, Unset):
            assignee_pod_member_id = UNSET
        elif isinstance(self.assignee_pod_member_id, UUID):
            assignee_pod_member_id = str(self.assignee_pod_member_id)
        else:
            assignee_pod_member_id = self.assignee_pod_member_id

        assignee_pod_member_id_expression: None | str | Unset
        if isinstance(self.assignee_pod_member_id_expression, Unset):
            assignee_pod_member_id_expression = UNSET
        else:
            assignee_pod_member_id_expression = self.assignee_pod_member_id_expression

        ui_schema: dict[str, Any] | None | Unset
        if isinstance(self.ui_schema, Unset):
            ui_schema = UNSET
        elif isinstance(self.ui_schema, FormNodeConfigUiSchemaType0):
            ui_schema = self.ui_schema.to_dict()
        else:
            ui_schema = self.ui_schema

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "input_schema": input_schema,
            }
        )
        if assignee_pod_member_id is not UNSET:
            field_dict["assignee_pod_member_id"] = assignee_pod_member_id
        if assignee_pod_member_id_expression is not UNSET:
            field_dict["assignee_pod_member_id_expression"] = (
                assignee_pod_member_id_expression
            )
        if ui_schema is not UNSET:
            field_dict["ui_schema"] = ui_schema

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.form_node_config_input_schema import FormNodeConfigInputSchema
        from ..models.form_node_config_ui_schema_type_0 import (
            FormNodeConfigUiSchemaType0,
        )

        d = dict(src_dict)
        input_schema = FormNodeConfigInputSchema.from_dict(d.pop("input_schema"))

        def _parse_assignee_pod_member_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                assignee_pod_member_id_type_0 = UUID(data)

                return assignee_pod_member_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        assignee_pod_member_id = _parse_assignee_pod_member_id(
            d.pop("assignee_pod_member_id", UNSET)
        )

        def _parse_assignee_pod_member_id_expression(
            data: object,
        ) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        assignee_pod_member_id_expression = _parse_assignee_pod_member_id_expression(
            d.pop("assignee_pod_member_id_expression", UNSET)
        )

        def _parse_ui_schema(
            data: object,
        ) -> FormNodeConfigUiSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                ui_schema_type_0 = FormNodeConfigUiSchemaType0.from_dict(data)

                return ui_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(FormNodeConfigUiSchemaType0 | None | Unset, data)

        ui_schema = _parse_ui_schema(d.pop("ui_schema", UNSET))

        form_node_config = cls(
            input_schema=input_schema,
            assignee_pod_member_id=assignee_pod_member_id,
            assignee_pod_member_id_expression=assignee_pod_member_id_expression,
            ui_schema=ui_schema,
        )

        form_node_config.additional_properties = d
        return form_node_config

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
