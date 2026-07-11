from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.event_workflow_start_config_input import EventWorkflowStartConfigInput


T = TypeVar("T", bound="EventWorkflowStartInput")


@_attrs_define
class EventWorkflowStartInput:
    """
    Attributes:
        config (EventWorkflowStartConfigInput):
        type_ (Literal['EVENT'] | Unset): Event-triggered workflow start. Default: 'EVENT'.
    """

    config: EventWorkflowStartConfigInput
    type_: Literal["EVENT"] | Unset = "EVENT"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        config = self.config.to_dict()

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "config": config,
            }
        )
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.event_workflow_start_config_input import (
            EventWorkflowStartConfigInput,
        )

        d = dict(src_dict)
        config = EventWorkflowStartConfigInput.from_dict(d.pop("config"))

        type_ = cast(Literal["EVENT"] | Unset, d.pop("type", UNSET))
        if type_ != "EVENT" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'EVENT', got '{type_}'")

        event_workflow_start_input = cls(
            config=config,
            type_=type_,
        )

        event_workflow_start_input.additional_properties = d
        return event_workflow_start_input

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
