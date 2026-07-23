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
    from ..models.agent_node_config import AgentNodeConfig
    from ..models.agent_node_position_type_0 import AgentNodePositionType0


T = TypeVar("T", bound="AgentNode")


@_attrs_define
class AgentNode:
    """Agent node. The run waits on the agent conversation to complete.

    Attributes:
        config (AgentNodeConfig): Configuration for Agent node.
        id (str):
        label (None | str | Unset):
        position (AgentNodePositionType0 | None | Unset):
        type_ (Literal['AGENT'] | Unset):  Default: 'AGENT'.
    """

    config: AgentNodeConfig
    id: str
    label: None | str | Unset = UNSET
    position: AgentNodePositionType0 | None | Unset = UNSET
    type_: Literal["AGENT"] | Unset = "AGENT"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.agent_node_position_type_0 import AgentNodePositionType0

        config = self.config.to_dict()

        id = self.id

        label: None | str | Unset
        if isinstance(self.label, Unset):
            label = UNSET
        else:
            label = self.label

        position: dict[str, Any] | None | Unset
        if isinstance(self.position, Unset):
            position = UNSET
        elif isinstance(self.position, AgentNodePositionType0):
            position = self.position.to_dict()
        else:
            position = self.position

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "config": config,
                "id": id,
            }
        )
        if label is not UNSET:
            field_dict["label"] = label
        if position is not UNSET:
            field_dict["position"] = position
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_node_config import AgentNodeConfig
        from ..models.agent_node_position_type_0 import AgentNodePositionType0

        d = dict(src_dict)
        config = AgentNodeConfig.from_dict(d.pop("config"))

        id = d.pop("id")

        def _parse_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label = _parse_label(d.pop("label", UNSET))

        def _parse_position(data: object) -> AgentNodePositionType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                position_type_0 = AgentNodePositionType0.from_dict(data)

                return position_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(AgentNodePositionType0 | None | Unset, data)

        position = _parse_position(d.pop("position", UNSET))

        type_ = cast(Literal["AGENT"] | Unset, d.pop("type", UNSET))
        if type_ != "AGENT" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'AGENT', got '{type_}'")

        agent_node = cls(
            config=config,
            id=id,
            label=label,
            position=position,
            type_=type_,
        )

        agent_node.additional_properties = d
        return agent_node

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
