from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.update_schedule_request_config_type_0 import (
        UpdateScheduleRequestConfigType0,
    )
    from ..models.update_schedule_request_filter_output_schema_type_0 import (
        UpdateScheduleRequestFilterOutputSchemaType0,
    )


T = TypeVar("T", bound="UpdateScheduleRequest")


@_attrs_define
class UpdateScheduleRequest:
    """Request to update a schedule.

    Attributes:
        agent_name (None | str | Unset):
        config (None | Unset | UpdateScheduleRequestConfigType0):
        filter_instruction (None | str | Unset):
        filter_output_schema (None | Unset | UpdateScheduleRequestFilterOutputSchemaType0):
        instruction (None | str | Unset):
        is_active (bool | None | Unset):
        name (None | str | Unset):
        visibility (None | str | Unset):
        workflow_name (None | str | Unset):
    """

    agent_name: None | str | Unset = UNSET
    config: None | Unset | UpdateScheduleRequestConfigType0 = UNSET
    filter_instruction: None | str | Unset = UNSET
    filter_output_schema: (
        None | Unset | UpdateScheduleRequestFilterOutputSchemaType0
    ) = UNSET
    instruction: None | str | Unset = UNSET
    is_active: bool | None | Unset = UNSET
    name: None | str | Unset = UNSET
    visibility: None | str | Unset = UNSET
    workflow_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.update_schedule_request_config_type_0 import (
            UpdateScheduleRequestConfigType0,
        )
        from ..models.update_schedule_request_filter_output_schema_type_0 import (
            UpdateScheduleRequestFilterOutputSchemaType0,
        )

        agent_name: None | str | Unset
        if isinstance(self.agent_name, Unset):
            agent_name = UNSET
        else:
            agent_name = self.agent_name

        config: dict[str, Any] | None | Unset
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, UpdateScheduleRequestConfigType0):
            config = self.config.to_dict()
        else:
            config = self.config

        filter_instruction: None | str | Unset
        if isinstance(self.filter_instruction, Unset):
            filter_instruction = UNSET
        else:
            filter_instruction = self.filter_instruction

        filter_output_schema: dict[str, Any] | None | Unset
        if isinstance(self.filter_output_schema, Unset):
            filter_output_schema = UNSET
        elif isinstance(
            self.filter_output_schema, UpdateScheduleRequestFilterOutputSchemaType0
        ):
            filter_output_schema = self.filter_output_schema.to_dict()
        else:
            filter_output_schema = self.filter_output_schema

        instruction: None | str | Unset
        if isinstance(self.instruction, Unset):
            instruction = UNSET
        else:
            instruction = self.instruction

        is_active: bool | None | Unset
        if isinstance(self.is_active, Unset):
            is_active = UNSET
        else:
            is_active = self.is_active

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        else:
            visibility = self.visibility

        workflow_name: None | str | Unset
        if isinstance(self.workflow_name, Unset):
            workflow_name = UNSET
        else:
            workflow_name = self.workflow_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if agent_name is not UNSET:
            field_dict["agent_name"] = agent_name
        if config is not UNSET:
            field_dict["config"] = config
        if filter_instruction is not UNSET:
            field_dict["filter_instruction"] = filter_instruction
        if filter_output_schema is not UNSET:
            field_dict["filter_output_schema"] = filter_output_schema
        if instruction is not UNSET:
            field_dict["instruction"] = instruction
        if is_active is not UNSET:
            field_dict["is_active"] = is_active
        if name is not UNSET:
            field_dict["name"] = name
        if visibility is not UNSET:
            field_dict["visibility"] = visibility
        if workflow_name is not UNSET:
            field_dict["workflow_name"] = workflow_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.update_schedule_request_config_type_0 import (
            UpdateScheduleRequestConfigType0,
        )
        from ..models.update_schedule_request_filter_output_schema_type_0 import (
            UpdateScheduleRequestFilterOutputSchemaType0,
        )

        d = dict(src_dict)

        def _parse_agent_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent_name = _parse_agent_name(d.pop("agent_name", UNSET))

        def _parse_config(
            data: object,
        ) -> None | Unset | UpdateScheduleRequestConfigType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = UpdateScheduleRequestConfigType0.from_dict(data)

                return config_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UpdateScheduleRequestConfigType0, data)

        config = _parse_config(d.pop("config", UNSET))

        def _parse_filter_instruction(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        filter_instruction = _parse_filter_instruction(
            d.pop("filter_instruction", UNSET)
        )

        def _parse_filter_output_schema(
            data: object,
        ) -> None | Unset | UpdateScheduleRequestFilterOutputSchemaType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                filter_output_schema_type_0 = (
                    UpdateScheduleRequestFilterOutputSchemaType0.from_dict(data)
                )

                return filter_output_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                None | Unset | UpdateScheduleRequestFilterOutputSchemaType0, data
            )

        filter_output_schema = _parse_filter_output_schema(
            d.pop("filter_output_schema", UNSET)
        )

        def _parse_instruction(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        instruction = _parse_instruction(d.pop("instruction", UNSET))

        def _parse_is_active(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        is_active = _parse_is_active(d.pop("is_active", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_visibility(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        def _parse_workflow_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        workflow_name = _parse_workflow_name(d.pop("workflow_name", UNSET))

        update_schedule_request = cls(
            agent_name=agent_name,
            config=config,
            filter_instruction=filter_instruction,
            filter_output_schema=filter_output_schema,
            instruction=instruction,
            is_active=is_active,
            name=name,
            visibility=visibility,
            workflow_name=workflow_name,
        )

        update_schedule_request.additional_properties = d
        return update_schedule_request

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
