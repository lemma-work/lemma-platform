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
    from ..models.end_node_config import EndNodeConfig
    from ..models.end_node_position_type_0 import EndNodePositionType0


T = TypeVar("T", bound="EndNode")


@_attrs_define
class EndNode:
    """End node. Completes the run.

    Attributes:
        id (str):
        config (EndNodeConfig | Unset): Configuration for End node.
        label (None | str | Unset):
        position (EndNodePositionType0 | None | Unset):
        type_ (Literal['END'] | Unset):  Default: 'END'.
    """

    id: str
    config: EndNodeConfig | Unset = UNSET
    label: None | str | Unset = UNSET
    position: EndNodePositionType0 | None | Unset = UNSET
    type_: Literal["END"] | Unset = "END"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.end_node_position_type_0 import EndNodePositionType0

        id = self.id

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        label: None | str | Unset
        if isinstance(self.label, Unset):
            label = UNSET
        else:
            label = self.label

        position: dict[str, Any] | None | Unset
        if isinstance(self.position, Unset):
            position = UNSET
        elif isinstance(self.position, EndNodePositionType0):
            position = self.position.to_dict()
        else:
            position = self.position

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
            }
        )
        if config is not UNSET:
            field_dict["config"] = config
        if label is not UNSET:
            field_dict["label"] = label
        if position is not UNSET:
            field_dict["position"] = position
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.end_node_config import EndNodeConfig
        from ..models.end_node_position_type_0 import EndNodePositionType0

        d = dict(src_dict)
        id = d.pop("id")

        _config = d.pop("config", UNSET)
        config: EndNodeConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = EndNodeConfig.from_dict(_config)

        def _parse_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label = _parse_label(d.pop("label", UNSET))

        def _parse_position(data: object) -> EndNodePositionType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                position_type_0 = EndNodePositionType0.from_dict(data)

                return position_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(EndNodePositionType0 | None | Unset, data)

        position = _parse_position(d.pop("position", UNSET))

        type_ = cast(Literal["END"] | Unset, d.pop("type", UNSET))
        if type_ != "END" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'END', got '{type_}'")

        end_node = cls(
            id=id,
            config=config,
            label=label,
            position=position,
            type_=type_,
        )

        end_node.additional_properties = d
        return end_node

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
