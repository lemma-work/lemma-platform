from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.schedule_type import ScheduleType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_schedule_request_config import CreateScheduleRequestConfig
    from ..models.create_schedule_request_filter_output_schema_type_0 import (
        CreateScheduleRequestFilterOutputSchemaType0,
    )


T = TypeVar("T", bound="CreateScheduleRequest")


@_attrs_define
class CreateScheduleRequest:
    """Request to create a pod schedule.

    Attributes:
        schedule_type (ScheduleType): Type of schedule source.
        account_id (None | Unset | UUID): Connected connector account used to provision provider-backed webhook
            schedules.
        agent_name (None | str | Unset): Pod agent to wake, by name. Pass 'POD_DEFAULT' (or 'pod_default') to wake the
            pod's default assistant, which has no name of its own.
        config (CreateScheduleRequestConfig | Unset):
        connector_trigger_id (None | str | Unset): Connector trigger id for agent WEBHOOK schedules. Do not provide this
            for workflow schedules; workflow WEBHOOK schedules derive it from the workflow start configuration.
        filter_instruction (None | str | Unset): Optional schedule-level LLM filter instruction. Filters belong to the
            schedule, not the workflow start.
        filter_output_schema (CreateScheduleRequestFilterOutputSchemaType0 | None | Unset): Optional schema for the
            schedule-level filter output. Filters belong to the schedule, not the workflow start.
        instruction (None | str | Unset): What the target should do when this fires, in your own words. Reaches an agent
            as the run's conversation instructions, layered after the agent's own. Required when targeting the default
            assistant, which has no standing instruction to fall back on. Distinct from filter_instruction, which decides
            whether to fire.
        name (None | str | Unset): Stable pod-scoped schedule name used for import/export upserts.
        visibility (None | str | Unset):
        workflow_name (None | str | Unset):
    """

    schedule_type: ScheduleType
    account_id: None | Unset | UUID = UNSET
    agent_name: None | str | Unset = UNSET
    config: CreateScheduleRequestConfig | Unset = UNSET
    connector_trigger_id: None | str | Unset = UNSET
    filter_instruction: None | str | Unset = UNSET
    filter_output_schema: (
        CreateScheduleRequestFilterOutputSchemaType0 | None | Unset
    ) = UNSET
    instruction: None | str | Unset = UNSET
    name: None | str | Unset = UNSET
    visibility: None | str | Unset = UNSET
    workflow_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.create_schedule_request_filter_output_schema_type_0 import (
            CreateScheduleRequestFilterOutputSchemaType0,
        )

        schedule_type = self.schedule_type.value

        account_id: None | str | Unset
        if isinstance(self.account_id, Unset):
            account_id = UNSET
        elif isinstance(self.account_id, UUID):
            account_id = str(self.account_id)
        else:
            account_id = self.account_id

        agent_name: None | str | Unset
        if isinstance(self.agent_name, Unset):
            agent_name = UNSET
        else:
            agent_name = self.agent_name

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        connector_trigger_id: None | str | Unset
        if isinstance(self.connector_trigger_id, Unset):
            connector_trigger_id = UNSET
        else:
            connector_trigger_id = self.connector_trigger_id

        filter_instruction: None | str | Unset
        if isinstance(self.filter_instruction, Unset):
            filter_instruction = UNSET
        else:
            filter_instruction = self.filter_instruction

        filter_output_schema: dict[str, Any] | None | Unset
        if isinstance(self.filter_output_schema, Unset):
            filter_output_schema = UNSET
        elif isinstance(
            self.filter_output_schema, CreateScheduleRequestFilterOutputSchemaType0
        ):
            filter_output_schema = self.filter_output_schema.to_dict()
        else:
            filter_output_schema = self.filter_output_schema

        instruction: None | str | Unset
        if isinstance(self.instruction, Unset):
            instruction = UNSET
        else:
            instruction = self.instruction

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
        field_dict.update(
            {
                "schedule_type": schedule_type,
            }
        )
        if account_id is not UNSET:
            field_dict["account_id"] = account_id
        if agent_name is not UNSET:
            field_dict["agent_name"] = agent_name
        if config is not UNSET:
            field_dict["config"] = config
        if connector_trigger_id is not UNSET:
            field_dict["connector_trigger_id"] = connector_trigger_id
        if filter_instruction is not UNSET:
            field_dict["filter_instruction"] = filter_instruction
        if filter_output_schema is not UNSET:
            field_dict["filter_output_schema"] = filter_output_schema
        if instruction is not UNSET:
            field_dict["instruction"] = instruction
        if name is not UNSET:
            field_dict["name"] = name
        if visibility is not UNSET:
            field_dict["visibility"] = visibility
        if workflow_name is not UNSET:
            field_dict["workflow_name"] = workflow_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_schedule_request_config import CreateScheduleRequestConfig
        from ..models.create_schedule_request_filter_output_schema_type_0 import (
            CreateScheduleRequestFilterOutputSchemaType0,
        )

        d = dict(src_dict)
        schedule_type = ScheduleType(d.pop("schedule_type"))

        def _parse_account_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                account_id_type_0 = UUID(data)

                return account_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        account_id = _parse_account_id(d.pop("account_id", UNSET))

        def _parse_agent_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent_name = _parse_agent_name(d.pop("agent_name", UNSET))

        _config = d.pop("config", UNSET)
        config: CreateScheduleRequestConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = CreateScheduleRequestConfig.from_dict(_config)

        def _parse_connector_trigger_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        connector_trigger_id = _parse_connector_trigger_id(
            d.pop("connector_trigger_id", UNSET)
        )

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
        ) -> CreateScheduleRequestFilterOutputSchemaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                filter_output_schema_type_0 = (
                    CreateScheduleRequestFilterOutputSchemaType0.from_dict(data)
                )

                return filter_output_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                CreateScheduleRequestFilterOutputSchemaType0 | None | Unset, data
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

        create_schedule_request = cls(
            schedule_type=schedule_type,
            account_id=account_id,
            agent_name=agent_name,
            config=config,
            connector_trigger_id=connector_trigger_id,
            filter_instruction=filter_instruction,
            filter_output_schema=filter_output_schema,
            instruction=instruction,
            name=name,
            visibility=visibility,
            workflow_name=workflow_name,
        )

        create_schedule_request.additional_properties = d
        return create_schedule_request

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
