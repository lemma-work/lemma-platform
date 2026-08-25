from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.schedule_fire_status import ScheduleFireStatus
from ..models.schedule_type import ScheduleType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.schedule_detail_response_config import ScheduleDetailResponseConfig
    from ..models.schedule_detail_response_filter_output_schema_type_0 import (
        ScheduleDetailResponseFilterOutputSchemaType0,
    )


T = TypeVar("T", bound="ScheduleDetailResponse")


@_attrs_define
class ScheduleDetailResponse:
    """Schedule detail response.

    Attributes:
        account_id (None | UUID):
        agent_id (None | UUID):
        config (ScheduleDetailResponseConfig):
        connector_trigger_id (None | str):
        created_at (datetime.datetime):
        filter_instruction (None | str):
        filter_output_schema (None | ScheduleDetailResponseFilterOutputSchemaType0):
        id (UUID):
        is_active (bool):
        is_internal (bool):
        name (None | str):
        paused_by_failures (bool): True when the failure breaker paused this schedule, as opposed to a person pausing
            it. Reactivating resets the failure count.
        pod_id (None | UUID):
        schedule_type (ScheduleType): Type of schedule source.
        updated_at (datetime.datetime):
        user_id (UUID):
        visibility (str):
        workflow_id (None | UUID):
        agent_name (None | str | Unset):
        allowed_actions (list[str] | Unset):
        consecutive_failures (int | Unset):  Default: 0.
        instruction (None | str | Unset):
        last_error (None | str | Unset):
        last_fire_status (None | ScheduleFireStatus | Unset):
        last_fired_at (datetime.datetime | None | Unset):
        last_run_id (None | str | Unset):
        targets_pod_default (bool | Unset):  Default: False.
        workflow_name (None | str | Unset):
    """

    account_id: None | UUID
    agent_id: None | UUID
    config: ScheduleDetailResponseConfig
    connector_trigger_id: None | str
    created_at: datetime.datetime
    filter_instruction: None | str
    filter_output_schema: None | ScheduleDetailResponseFilterOutputSchemaType0
    id: UUID
    is_active: bool
    is_internal: bool
    name: None | str
    paused_by_failures: bool
    pod_id: None | UUID
    schedule_type: ScheduleType
    updated_at: datetime.datetime
    user_id: UUID
    visibility: str
    workflow_id: None | UUID
    agent_name: None | str | Unset = UNSET
    allowed_actions: list[str] | Unset = UNSET
    consecutive_failures: int | Unset = 0
    instruction: None | str | Unset = UNSET
    last_error: None | str | Unset = UNSET
    last_fire_status: None | ScheduleFireStatus | Unset = UNSET
    last_fired_at: datetime.datetime | None | Unset = UNSET
    last_run_id: None | str | Unset = UNSET
    targets_pod_default: bool | Unset = False
    workflow_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.schedule_detail_response_filter_output_schema_type_0 import (
            ScheduleDetailResponseFilterOutputSchemaType0,
        )

        account_id: None | str
        if isinstance(self.account_id, UUID):
            account_id = str(self.account_id)
        else:
            account_id = self.account_id

        agent_id: None | str
        if isinstance(self.agent_id, UUID):
            agent_id = str(self.agent_id)
        else:
            agent_id = self.agent_id

        config = self.config.to_dict()

        connector_trigger_id: None | str
        connector_trigger_id = self.connector_trigger_id

        created_at = self.created_at.isoformat()

        filter_instruction: None | str
        filter_instruction = self.filter_instruction

        filter_output_schema: dict[str, Any] | None
        if isinstance(
            self.filter_output_schema, ScheduleDetailResponseFilterOutputSchemaType0
        ):
            filter_output_schema = self.filter_output_schema.to_dict()
        else:
            filter_output_schema = self.filter_output_schema

        id = str(self.id)

        is_active = self.is_active

        is_internal = self.is_internal

        name: None | str
        name = self.name

        paused_by_failures = self.paused_by_failures

        pod_id: None | str
        if isinstance(self.pod_id, UUID):
            pod_id = str(self.pod_id)
        else:
            pod_id = self.pod_id

        schedule_type = self.schedule_type.value

        updated_at = self.updated_at.isoformat()

        user_id = str(self.user_id)

        visibility = self.visibility

        workflow_id: None | str
        if isinstance(self.workflow_id, UUID):
            workflow_id = str(self.workflow_id)
        else:
            workflow_id = self.workflow_id

        agent_name: None | str | Unset
        if isinstance(self.agent_name, Unset):
            agent_name = UNSET
        else:
            agent_name = self.agent_name

        allowed_actions: list[str] | Unset = UNSET
        if not isinstance(self.allowed_actions, Unset):
            allowed_actions = self.allowed_actions

        consecutive_failures = self.consecutive_failures

        instruction: None | str | Unset
        if isinstance(self.instruction, Unset):
            instruction = UNSET
        else:
            instruction = self.instruction

        last_error: None | str | Unset
        if isinstance(self.last_error, Unset):
            last_error = UNSET
        else:
            last_error = self.last_error

        last_fire_status: None | str | Unset
        if isinstance(self.last_fire_status, Unset):
            last_fire_status = UNSET
        elif isinstance(self.last_fire_status, ScheduleFireStatus):
            last_fire_status = self.last_fire_status.value
        else:
            last_fire_status = self.last_fire_status

        last_fired_at: None | str | Unset
        if isinstance(self.last_fired_at, Unset):
            last_fired_at = UNSET
        elif isinstance(self.last_fired_at, datetime.datetime):
            last_fired_at = self.last_fired_at.isoformat()
        else:
            last_fired_at = self.last_fired_at

        last_run_id: None | str | Unset
        if isinstance(self.last_run_id, Unset):
            last_run_id = UNSET
        else:
            last_run_id = self.last_run_id

        targets_pod_default = self.targets_pod_default

        workflow_name: None | str | Unset
        if isinstance(self.workflow_name, Unset):
            workflow_name = UNSET
        else:
            workflow_name = self.workflow_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "account_id": account_id,
                "agent_id": agent_id,
                "config": config,
                "connector_trigger_id": connector_trigger_id,
                "created_at": created_at,
                "filter_instruction": filter_instruction,
                "filter_output_schema": filter_output_schema,
                "id": id,
                "is_active": is_active,
                "is_internal": is_internal,
                "name": name,
                "paused_by_failures": paused_by_failures,
                "pod_id": pod_id,
                "schedule_type": schedule_type,
                "updated_at": updated_at,
                "user_id": user_id,
                "visibility": visibility,
                "workflow_id": workflow_id,
            }
        )
        if agent_name is not UNSET:
            field_dict["agent_name"] = agent_name
        if allowed_actions is not UNSET:
            field_dict["allowed_actions"] = allowed_actions
        if consecutive_failures is not UNSET:
            field_dict["consecutive_failures"] = consecutive_failures
        if instruction is not UNSET:
            field_dict["instruction"] = instruction
        if last_error is not UNSET:
            field_dict["last_error"] = last_error
        if last_fire_status is not UNSET:
            field_dict["last_fire_status"] = last_fire_status
        if last_fired_at is not UNSET:
            field_dict["last_fired_at"] = last_fired_at
        if last_run_id is not UNSET:
            field_dict["last_run_id"] = last_run_id
        if targets_pod_default is not UNSET:
            field_dict["targets_pod_default"] = targets_pod_default
        if workflow_name is not UNSET:
            field_dict["workflow_name"] = workflow_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.schedule_detail_response_config import (
            ScheduleDetailResponseConfig,
        )
        from ..models.schedule_detail_response_filter_output_schema_type_0 import (
            ScheduleDetailResponseFilterOutputSchemaType0,
        )

        d = dict(src_dict)

        def _parse_account_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                account_id_type_0 = UUID(data)

                return account_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        account_id = _parse_account_id(d.pop("account_id"))

        def _parse_agent_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                agent_id_type_0 = UUID(data)

                return agent_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        agent_id = _parse_agent_id(d.pop("agent_id"))

        config = ScheduleDetailResponseConfig.from_dict(d.pop("config"))

        def _parse_connector_trigger_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        connector_trigger_id = _parse_connector_trigger_id(
            d.pop("connector_trigger_id")
        )

        created_at = isoparse(d.pop("created_at"))

        def _parse_filter_instruction(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        filter_instruction = _parse_filter_instruction(d.pop("filter_instruction"))

        def _parse_filter_output_schema(
            data: object,
        ) -> None | ScheduleDetailResponseFilterOutputSchemaType0:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                filter_output_schema_type_0 = (
                    ScheduleDetailResponseFilterOutputSchemaType0.from_dict(data)
                )

                return filter_output_schema_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | ScheduleDetailResponseFilterOutputSchemaType0, data)

        filter_output_schema = _parse_filter_output_schema(
            d.pop("filter_output_schema")
        )

        id = UUID(d.pop("id"))

        is_active = d.pop("is_active")

        is_internal = d.pop("is_internal")

        def _parse_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        name = _parse_name(d.pop("name"))

        paused_by_failures = d.pop("paused_by_failures")

        def _parse_pod_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pod_id_type_0 = UUID(data)

                return pod_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        pod_id = _parse_pod_id(d.pop("pod_id"))

        schedule_type = ScheduleType(d.pop("schedule_type"))

        updated_at = isoparse(d.pop("updated_at"))

        user_id = UUID(d.pop("user_id"))

        visibility = d.pop("visibility")

        def _parse_workflow_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                workflow_id_type_0 = UUID(data)

                return workflow_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        workflow_id = _parse_workflow_id(d.pop("workflow_id"))

        def _parse_agent_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent_name = _parse_agent_name(d.pop("agent_name", UNSET))

        allowed_actions = cast(list[str], d.pop("allowed_actions", UNSET))

        consecutive_failures = d.pop("consecutive_failures", UNSET)

        def _parse_instruction(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        instruction = _parse_instruction(d.pop("instruction", UNSET))

        def _parse_last_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_error = _parse_last_error(d.pop("last_error", UNSET))

        def _parse_last_fire_status(data: object) -> None | ScheduleFireStatus | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_fire_status_type_0 = ScheduleFireStatus(data)

                return last_fire_status_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | ScheduleFireStatus | Unset, data)

        last_fire_status = _parse_last_fire_status(d.pop("last_fire_status", UNSET))

        def _parse_last_fired_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_fired_at_type_0 = isoparse(data)

                return last_fired_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_fired_at = _parse_last_fired_at(d.pop("last_fired_at", UNSET))

        def _parse_last_run_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_run_id = _parse_last_run_id(d.pop("last_run_id", UNSET))

        targets_pod_default = d.pop("targets_pod_default", UNSET)

        def _parse_workflow_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        workflow_name = _parse_workflow_name(d.pop("workflow_name", UNSET))

        schedule_detail_response = cls(
            account_id=account_id,
            agent_id=agent_id,
            config=config,
            connector_trigger_id=connector_trigger_id,
            created_at=created_at,
            filter_instruction=filter_instruction,
            filter_output_schema=filter_output_schema,
            id=id,
            is_active=is_active,
            is_internal=is_internal,
            name=name,
            paused_by_failures=paused_by_failures,
            pod_id=pod_id,
            schedule_type=schedule_type,
            updated_at=updated_at,
            user_id=user_id,
            visibility=visibility,
            workflow_id=workflow_id,
            agent_name=agent_name,
            allowed_actions=allowed_actions,
            consecutive_failures=consecutive_failures,
            instruction=instruction,
            last_error=last_error,
            last_fire_status=last_fire_status,
            last_fired_at=last_fired_at,
            last_run_id=last_run_id,
            targets_pod_default=targets_pod_default,
            workflow_name=workflow_name,
        )

        schedule_detail_response.additional_properties = d
        return schedule_detail_response

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
