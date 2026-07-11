from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.event_workflow_start_config_input_trigger_config import (
        EventWorkflowStartConfigInputTriggerConfig,
    )


T = TypeVar("T", bound="EventWorkflowStartConfigInput")


@_attrs_define
class EventWorkflowStartConfigInput:
    """
    Attributes:
        connector_id (str): Connector connector identifier.
        connector_trigger_id (str): Connector trigger identifier to subscribe to.
        trigger_config (EventWorkflowStartConfigInputTriggerConfig | Unset):
    """

    connector_id: str
    connector_trigger_id: str
    trigger_config: EventWorkflowStartConfigInputTriggerConfig | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connector_id = self.connector_id

        connector_trigger_id = self.connector_trigger_id

        trigger_config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.trigger_config, Unset):
            trigger_config = self.trigger_config.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connector_id": connector_id,
                "connector_trigger_id": connector_trigger_id,
            }
        )
        if trigger_config is not UNSET:
            field_dict["trigger_config"] = trigger_config

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.event_workflow_start_config_input_trigger_config import (
            EventWorkflowStartConfigInputTriggerConfig,
        )

        d = dict(src_dict)
        connector_id = d.pop("connector_id")

        connector_trigger_id = d.pop("connector_trigger_id")

        _trigger_config = d.pop("trigger_config", UNSET)
        trigger_config: EventWorkflowStartConfigInputTriggerConfig | Unset
        if isinstance(_trigger_config, Unset):
            trigger_config = UNSET
        else:
            trigger_config = EventWorkflowStartConfigInputTriggerConfig.from_dict(
                _trigger_config
            )

        event_workflow_start_config_input = cls(
            connector_id=connector_id,
            connector_trigger_id=connector_trigger_id,
            trigger_config=trigger_config,
        )

        event_workflow_start_config_input.additional_properties = d
        return event_workflow_start_config_input

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
